"""
Authoritative kill-feed events from the Squad server log.

The memory reader samples take-hit info at ~0.5 Hz off the (short-lived)
soldier actor, so it only ever catches a fraction of kills. The server's
own log records EVERY damage / wound / death with the exact attacker and
weapon — it is the game's ground truth. We tail it and correlate the
lines into clean events shaped like the frontend's DamageEvent, so the
event-first kill feed attributes ~every death exactly.

Log lines we use (verified live on Squad v10.x):

  LogSquad: Player: <victim> ActualDamage=<f> from <attacker>
      (Online IDs: EOS: <eos> steam: <id> | Player Controller ID: <ctrl>)caused by <weapon>
  LogSquadTrace: [DedicatedServer]Wound(): Player: <victim> KillingDamage=<f> from <ctrl> (...)
  LogSquadTrace: [DedicatedServer]Die():   Player:<victim> KillingDamage=<f> from <ctrl|nullptr> (...)

Correlation:
  - ActualDamage carries the attacker NAME + weapon; remember it per victim.
  - Wound() -> the victim is incapacitated. Emit a wounded event using the
    remembered attacker/weapon, and remember who downed them.
  - Die() from a real controller -> instant kill; attacker from the last
    ActualDamage. Die() from nullptr -> bleed-out; attacker is whoever the
    Wound() recorded. No record at all -> a true world death (no attacker).
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Optional

# Victim names contain spaces and unicode; capture non-greedily up to the
# " ActualDamage=" / " KillingDamage=" delimiter. "Player:" may or may not
# be followed by a space.
_ACTUAL_RE = re.compile(
    r"LogSquad: Player:\s?(?P<victim>.+?) ActualDamage=[-\d.]+ from "
    r"(?P<attacker>.+?) \(Online IDs:\s*EOS:\s*(?P<eos>\w+).*?\)\s*caused by (?P<weapon>\S+)"
)
_WOUND_RE = re.compile(
    r"Wound\(\): Player:\s?(?P<victim>.+?) KillingDamage=[-\d.]+ from (?P<from>\S+)"
)
_DIE_RE = re.compile(
    r"Die\(\): Player:\s?(?P<victim>.+?) KillingDamage=[-\d.]+ from (?P<from>\S+)"
    r"(?:\s*\(Online IDs:\s*EOS:\s*(?P<from_eos>\w+))?"
    r"(?:.*?caused by (?P<causer>\S+))?"
)
# "<reviver> (Online IDs: EOS: ... steam: ...) has revived <victim> (Online IDs: ...)"
# Both halves of the line carry a name AND that name's EOS id, so a revive
# teaches the id cache two players at once — including the medic, who may
# never deal damage all match and would otherwise be unknown to it. The
# id-bearing prefix is OPTIONAL: identifying the revive and its victim is what
# clears the correlation state, and that must keep working even if a future
# log revision moves the reviver's ids somewhere this pattern cannot see.
_REVIVE_RE = re.compile(
    r"(?:LogSquad: (?P<reviver>.+?) \(Online IDs:\s*EOS:\s*(?P<reviver_eos>\w+)[^)]*\)\s*)?"
    r"has revived (?P<victim>.+?) \(Online IDs:(?:\s*EOS:\s*(?P<victim_eos>\w+))?"
)
_TS_RE = re.compile(r"^\[(?P<ts>[\d.]+-[\d.]+:[\d]+)\]")


def _base_name_resolver(players: list[dict]
                        ) -> Callable[[Optional[str]], Optional[str]]:
    """Build the log-name -> roster-base-name resolver for one roster.

    Squad log lines carry the full display name WITH the clan tag
    ("『GM』 I3lack"); the memory reader splits the tag off (name="I3lack",
    clanTag separate). Exact match first, else the longest base name that is a
    suffix of the log name at a tag boundary (a non-alphanumeric separator
    precedes it). A name the roster cannot account for is returned untouched —
    the resolver strips tags, it never invents an identity.

    Lifted out of `resolve_event_names` verbatim so the revive feed resolves
    names by exactly the same rule the kill feed does, rather than growing a
    second copy of it that could drift.
    """
    bases = [p["name"] for p in players if p.get("name")]
    base_set = set(bases)

    def resolve(full: Optional[str]) -> Optional[str]:
        if not full or full in base_set:
            return full
        best = None
        for n in bases:
            if len(n) < 3 or not full.endswith(n):
                continue
            i = len(full) - len(n)
            if i > 0 and full[i - 1].isalnum():
                continue  # mid-word, not a clan-tag boundary
            if best is None or len(n) > len(best):
                best = n
        return best or full

    return resolve


def resolve_event_names(events: list[dict], players: list[dict]) -> list[dict]:
    """Rewrite each event's attacker/victim to the matching snapshot player's
    base name.

    Squad log lines carry the full display name WITH the clan tag
    ("『GM』 I3lack"); the memory reader splits the tag off (name="I3lack",
    clanTag separate). The frontend keys the kill feed on the base name, so
    without this ~half the events (every tagged player) would fail to match.
    Exact match first, else the longest base name that is a suffix of the log
    name at a tag boundary (a non-alphanumeric separator precedes it).

    Also performs EOS-based attacker recovery: when the name-cache lost the
    attacker (a death long after the wound, past the correlation TTL), the Die
    line still carried the killer's controller EOS in `killerEos`. Resolve it to
    a name against the live roster here — or, if it is the victim's own EOS,
    flag a self death rather than crediting them their own kill.

    This roster attempt is the FALLBACK, not the main path. It only works where
    the roster's `eosId` and the log's ids are the same namespace, which is to
    say on an unlicensed server: a licensed one reports `OnlineUserId` as a UUID
    in memory while every id in the log is a 32-hex EOS ProductUserId, so the
    map can never hit and this recovery silently did nothing on exactly the
    servers that matter. `DamageLogParser` now resolves the Die line's id
    against the log's own id cache first (same namespace by construction) and
    only leaves `killerEos` set when that misses. Kept anyway, because where the
    namespaces do coincide it still names a killer the log never mentioned."""
    eos_to_name = {p["eosId"]: p["name"]
                   for p in players if p.get("eosId") and p.get("name")}
    name_to_eos = {p["name"]: p["eosId"]
                   for p in players if p.get("name") and p.get("eosId")}
    resolve = _base_name_resolver(players)

    for e in events:
        e["victim"] = resolve(e.get("victim"))
        e["attacker"] = resolve(e.get("attacker"))
        keos = e.pop("killerEos", None)
        if not e.get("attacker") and keos:
            veos = e.get("victimEosId") or name_to_eos.get(e.get("victim"))
            if veos and keos == veos:
                e["selfInflicted"] = True   # died to their own pawn — self/give-up
            else:
                nm = eos_to_name.get(keos)
                if nm and nm != e.get("victim"):
                    e["attacker"] = nm
                    e["attackerEosId"] = keos
    return events


def resolve_revive_names(events: list[dict], players: list[dict]) -> list[dict]:
    """Rewrite each revive event's reviver/victim to the roster's base name.

    Same clan-tag problem the kill feed has, same fix, same resolver: a revive
    line names both players the way the server displays them, and consumers key
    on the base name the snapshot's roster carries.

    A reviver the log did not name stays None — the roster is asked to strip a
    tag, never to fill a blank.
    """
    resolve = _base_name_resolver(players)
    for e in events:
        e["reviver"] = resolve(e.get("reviver"))
        e["victim"] = resolve(e.get("victim"))
    return events


def log_from_pid(pid: int) -> Optional[str]:
    """The running server's own log, derived from the binary it is executing.

    Squad always lays itself out as
    ``<root>/SquadGame/Binaries/Linux/SquadGameServer`` and always writes
    ``<root>/SquadGame/Saved/Logs/SquadGame.log``, so the process points at its
    own log no matter where somebody installed it. That matters because the
    fallback glob below only looks under ``/home/*/serverfiles`` — LinuxGSM's
    layout — and a Docker image, an /opt install or a user outside /home would
    find nothing and lose the kill feed without ever saying why.
    """
    import os
    try:
        exe = os.path.realpath(f"/proc/{pid}/exe")
    except OSError:
        return None
    root = exe
    for _ in range(4):                       # .../SquadGame/Binaries/Linux/exe
        root = os.path.dirname(root)
    if not root or root == os.sep:
        return None
    cand = os.path.join(root, "SquadGame", "Saved", "Logs", "SquadGame.log")
    return cand if os.access(cand, os.R_OK) else None


def find_squad_log(pid: Optional[int] = None) -> Optional[str]:
    """Locate the active server's SquadGame.log.

    Order matters: the PROCESS is asked first, because it is the only source
    that is right regardless of how the server was installed. The glob is kept
    as a fallback for the case where the exe path is unreadable, and an
    operator's explicit `squad_log_glob` still wins over both by pointing the
    glob wherever they say.
    """
    import glob
    import os

    from ..config import get as _config_get
    if pid is not None:
        found = log_from_pid(pid)
        if found:
            return found
    cands = [c for c in glob.glob(_config_get("squad_log_glob"))
             if os.access(c, os.R_OK)]
    if not cands:
        return None
    return max(cands, key=lambda p: os.stat(p).st_mtime)


def _parse_ts(line: str) -> Optional[float]:
    m = _TS_RE.match(line)
    if not m:
        return None
    try:
        # e.g. 2026.07.12-18.31.24:082  (last field is milliseconds)
        dt = datetime.strptime(m.group("ts"), "%Y.%m.%d-%H.%M.%S:%f")
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _norm_weapon(w: str) -> str:
    # BP_M4_SimonOffense_T800_C_2007009837 -> BP_M4_SimonOffense_T800_C
    # (strip the trailing runtime instance id so it matches the catalog)
    return re.sub(r"_\d+$", "", w) if w and w != "nullptr" else w


class DamageLogParser:
    """Pure correlation state machine — feed it lines, drain events. No I/O,
    so it is directly unit-testable."""

    # 330, not a round three minutes: Squad lets a downed player wait 300 s
    # for a medic before the forced give-up, and players hoping for a revive
    # routinely use most of it. The previous 180 s meant any incap held past
    # three minutes died unattributed — verified on a real recording (wounded
    # at 27:24, gave up at 31:41, 257 s later: "?" in the feed while the game
    # itself credited the wounder). The margin over 300 absorbs log-timestamp
    # jitter. The GC that enforces this runs on every processed death, so on
    # a busy server the TTL bites at exactly its value.
    def __init__(self, *, ttl_sec: float = 330.0, max_buffer: int = 512,
                 max_eos_names: int = 1024):
        self.ttl = ttl_sec
        # victim -> (attacker, eos, weapon, ts) from the most recent hit
        self._pending: dict[str, tuple] = {}
        # victim -> (attacker, eos, weapon, ts) recorded at incap, awaiting death
        self._wounded_by: dict[str, tuple] = {}
        # log-EOS -> display name, learned from the log's OWN lines. The Die
        # line names the credited killer by id and nothing else, so a name for
        # that id has to come from somewhere; the roster cannot supply one on a
        # licensed server (different namespace — see resolve_event_names), but
        # every ActualDamage line already carries an attacker's name and their
        # id together, in the same namespace as the Die line by construction.
        # Revive lines add the players who never dealt damage. Lifetime is the
        # parser's, because a name is stable for the session that owns it.
        self._eos_names: dict[str, str] = {}
        self.max_eos_names = max_eos_names
        self._events: deque = deque(maxlen=max_buffer)
        # Revives, drained separately from the damage events. The log is the
        # ONLY place a revive is evented — memory carries the medic's item and
        # target, never the completion — so the line the parser already reads
        # to clear correlation state is also the whole record of the act.
        self._revive_events: deque = deque(maxlen=max_buffer)
        self._last_ts: float = 0.0

    def _remember_eos(self, eos: Optional[str], name: Optional[str]) -> None:
        """Learn one id -> name pair. Insertion-ordered, so the bound evicts the
        oldest id seen — a match's roster is a few hundred players at most, and
        an evicted id costs a "?" on a death, never a wrong name."""
        if not eos or not name or eos == "INVALID":
            return
        self._eos_names[eos] = name
        while len(self._eos_names) > self.max_eos_names:
            del self._eos_names[next(iter(self._eos_names))]

    def _gc(self, now: float) -> None:
        for d in (self._pending, self._wounded_by):
            stale = [k for k, v in d.items() if now - v[3] > self.ttl]
            for k in stale:
                del d[k]

    def feed(self, line: str) -> None:
        ts = _parse_ts(line) or self._last_ts
        self._last_ts = ts

        m = _ACTUAL_RE.search(line)
        if m:
            victim = m.group("victim").strip()
            attacker = m.group("attacker").strip()
            self._remember_eos(m.group("eos"), attacker)
            self._pending[victim] = (
                attacker, m.group("eos"), _norm_weapon(m.group("weapon")), ts)
            return

        m = _REVIVE_RE.search(line)
        if m:
            # A revived player is back in the fight — forget who downed them
            # so their NEXT death (a fresh incap, or a later world cause) is
            # not mis-credited to the old wounder.
            victim = m.group("victim").strip()
            rev = m.group("reviver")
            self._remember_eos(m.group("reviver_eos"), rev.strip() if rev else None)
            self._remember_eos(m.group("victim_eos"), victim)
            # ...and record the revive itself. Both halves come off this one
            # line with their ids, so the event is what the log said and
            # nothing more: when the id-bearing prefix is absent (see the
            # regex comment above) the reviver goes out as None rather than
            # being inferred from who was nearby.
            self._revive_events.append({
                "reviver": rev.strip() if rev else None,
                "reviverEosId": m.group("reviver_eos"),
                "victim": victim,
                "victimEosId": m.group("victim_eos"),
                "ts": round(ts, 3),
            })
            self._wounded_by.pop(victim, None)
            self._pending.pop(victim, None)
            return

        m = _WOUND_RE.search(line)
        if m:
            victim = m.group("victim").strip()
            src = self._pending.get(victim)
            attacker = src[0] if src else None
            eos = src[1] if src else None
            weapon = src[2] if src else None
            self._wounded_by[victim] = (attacker, eos, weapon, ts)
            self._emit(victim, attacker, eos, weapon, ts,
                       wounded=True, killed=False)
            return

        m = _DIE_RE.search(line)
        if m:
            victim = m.group("victim").strip()
            frm = m.group("from")
            causer = m.group("causer")
            if frm and frm != "nullptr":
                # instant kill — attacker is the last hit's source
                src = self._pending.get(victim)
            else:
                # bleed-out / give-up — attacker is whoever downed them
                src = self._wounded_by.get(victim)
            attacker = src[0] if src else None
            eos = src[1] if src else None
            weapon = src[2] if src else None
            # The Die line names the CREDITED killer by controller EOS (the same
            # EOS the game scores the kill to). Keep it: when the name-cache has
            # expired — a death long after the wound, e.g. round-end deaths of
            # players downed minutes earlier, past the correlation TTL — this is
            # the ONLY thing that still identifies the killer. It is turned into
            # a name below against the log's own id cache (or a self death if it
            # is the victim's own id), and only what that misses is handed to
            # resolve_event_names to try the roster. This is what kills the bare
            # "?" rows.
            die_eos = m.group("from_eos")
            killer_eos = die_eos if die_eos and die_eos != "INVALID" else None
            # No damage record attributes this death — classify it from the
            # log's own "from" / "caused by" instead of showing a bare "?":
            #   caused by <world class>    -> a world cause (fall, drown,
            #     vehicle, roadkill), NOT a soldier pawn; surface it as the
            #     damage type so the feed names the cause.
            #   from <own pawn> + nullptr  -> the player gave up / self-
            #     terminated while downed (no weapon). A self death, credit no one.
            #   caused by <soldier pawn>   -> a real kill whose name-cache
            #     expired; leave it for killer_eos to recover the name.
            #   from nullptr (no cause)    -> a true unnamed world/bleed death.
            self_inflicted = None
            damage_type = None
            if attacker is None:
                if causer and causer != "nullptr" \
                        and not causer.startswith("BP_Soldier"):
                    damage_type = _norm_weapon(causer)
                # The id cache is the log answering its own question, so it
                # outranks every heuristic below it: same namespace as the Die
                # line by construction, and the pairing was read off a line
                # that named both halves. Resolving it here rather than in
                # resolve_event_names is the whole repair — the roster it
                # consults holds UUIDs on a licensed server and can never match
                # a 32-hex log id, which left these deaths as bare "?" rows on
                # exactly the servers this reader is deployed on.
                cached = self._eos_names.get(killer_eos) if killer_eos else None
                if cached is not None:
                    if cached == victim:
                        # The credited killer is the victim: a give-up or a
                        # self-kill. Answering this from the roster asked the
                        # same impossible question in the same wrong namespace.
                        self_inflicted = True
                    else:
                        attacker, eos = cached, killer_eos
                    killer_eos = None          # answered; no roster attempt left
                elif frm and frm != "nullptr" and (not causer or causer == "nullptr"):
                    self_inflicted = True
                # A player the cache has never seen — no damage dealt, no
                # revive either way all match — stays unresolved here and goes
                # out with killer_eos for the roster fallback to try. If that
                # misses too the row is an honest "?", never a guess.
            self._emit(victim, attacker, eos, weapon, ts,
                       wounded=False, killed=True,
                       self_inflicted=self_inflicted, damage_type=damage_type,
                       killer_eos=killer_eos)
            self._wounded_by.pop(victim, None)
            self._pending.pop(victim, None)
            self._gc(ts)

    def _emit(self, victim, attacker, eos, weapon, ts, *, wounded, killed,
              self_inflicted=None, damage_type=None, killer_eos=None):
        self._events.append({
            "victim": victim,
            "victimEosId": None,          # log gives the attacker's EOS, not victim's
            "victimTeam": None,           # frontend fills team from the snapshot
            "attacker": None if attacker == victim else attacker,
            "attackerEosId": eos,
            "killerEos": killer_eos,      # Die-line controller EOS — resolve_event_names
                                          # turns it into a name / self-death, then drops it
            "selfInflicted": (attacker == victim) if self_inflicted is None
                             else bool(self_inflicted),
            "causerWeapon": weapon,
            "causerClass": None,
            "damageType": damage_type,
            "hitDistance": None,
            "headshot": None,
            "wounded": wounded,
            "killed": killed,
            "ts": round(ts, 3),
        })

    def drain(self) -> list[dict]:
        out = list(self._events)
        self._events.clear()
        return out

    def drain_revives(self) -> list[dict]:
        """The revives seen since the last call. Separate buffer, so draining
        one feed never consumes the other."""
        out = list(self._revive_events)
        self._revive_events.clear()
        return out


class LogTailer:
    """Follows the Squad log from its end in a daemon thread, feeds each new
    line to a DamageLogParser, and hands recent events to the snapshot loop
    via drain(). Read-only and isolated: if the log path is unreadable or the
    thread dies, the reader keeps running with an empty event stream."""

    def __init__(self, path: str, *, poll_sec: float = 0.25,
                 catchup_bytes: int = 4_000_000):
        self.path = path
        self.poll_sec = poll_sec
        # On startup, replay this many bytes from the log tail to rebuild
        # correlation state (in-flight wounds) without emitting the stale
        # events, so a death just after a reader restart is still attributed.
        self.catchup_bytes = catchup_bytes
        self.parser = DamageLogParser()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.lines_read = 0
        self.last_error: Optional[str] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="squad-logtail",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def drain(self) -> list[dict]:
        with self._lock:
            return self.parser.drain()

    def drain_revives(self) -> list[dict]:
        with self._lock:
            return self.parser.drain_revives()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._follow()
            except Exception as e:  # never let the tailer crash the reader
                self.last_error = f"{type(e).__name__}: {e}"
                time.sleep(2.0)

    def _follow(self) -> None:
        import os
        f = open(self.path, encoding="utf-8", errors="replace")
        try:
            # Initial catch-up: replay the log's tail to rebuild the
            # correlation state (a player downed just before we started can
            # still be credited when they bleed out) but DISCARD those old
            # events — drain() clears the event buffer while keeping the
            # pending / wounded_by state. Without this, a reader restart
            # mid-match leaves in-flight deaths unattributed for ~a minute.
            size = os.fstat(f.fileno()).st_size
            if self.catchup_bytes > 0 and size > 0:
                f.seek(max(0, size - self.catchup_bytes))
                if size > self.catchup_bytes:
                    f.readline()  # drop the partial first line
                catch = f.read()  # up to the current end
                with self._lock:
                    for line in catch.splitlines():
                        if line:
                            self.parser.feed(line)
                    self.parser.drain()  # discard stale events, keep state
                    # The revive feed is discarded on the same terms: a revive
                    # replayed from the log's tail already happened, and the
                    # first tick after a restart must not report it as if it
                    # had just been performed.
                    self.parser.drain_revives()
            else:
                f.seek(0, os.SEEK_END)
            cur_ino = os.fstat(f.fileno()).st_ino
            buf = ""
            while not self._stop.is_set():
                chunk = f.read(65536)
                if chunk:
                    buf += chunk
                    while "\n" in buf:
                        line, _, buf = buf.partition("\n")
                        if line:
                            with self._lock:
                                self.parser.feed(line)
                            self.lines_read += 1
                    continue
                # no new data — check for rotation (restart / new map log)
                try:
                    st = os.stat(self.path)
                    if st.st_ino != cur_ino or st.st_size < f.tell():
                        f.close()
                        f = open(self.path, encoding="utf-8", errors="replace")
                        cur_ino = os.fstat(f.fileno()).st_ino
                        buf = ""
                        continue
                except OSError:
                    pass
                time.sleep(self.poll_sec)
        finally:
            f.close()
