"""Memory-verified cheat detection. Alert-only — this never touches the game.

Ported from the Squad-Replay project's `plugins/cheat_detect.py`. Its thresholds
carry real tuning history and are kept, with their reasoning, because that
history is the expensive part; what changed is the tick rate they are scaled to
and which detectors sqreader has the data to run at all.

Tick-rate scaling
-----------------
There is none left to get wrong, and that is deliberate.

The thresholds arrived as TICK counts, which only mean what you think at the
one tick rate they were written for. The source ran at 1 s and kept a table -
5 s tick -> 3 ticks, 2 s -> 8, 1 s -> 15 - and a warning to re-scale by hand
after changing `--hz`. That warning was correctly written and duly ignored: the
first deployment to switch this on ran at 3 Hz, where the inherited 8 meant 2.7
seconds instead of 16. A parachute landing lasts about that long.

So durations are in SECONDS now, accumulated from the game's own clock, and a
sample is worth however much time actually passed. Sixteen seconds is sixteen
seconds at any tick rate, on any box, with no table to consult.

What still counts in samples is `magic_bullet_consecutive`, and that detector
is off.

What is NOT here, and exactly why
---------------------------------
The no-guess rule applies to detection as hard as it applies to display: a
detector that infers a signal sqreader cannot read would be an accusation built
on a guess. This section is the record of that boundary — where it is, and what
moved it.

Three of the four entries that used to sit here have been built, because the
reader grew the fields they were waiting on:

  stamina_hack        was blocked on the sprint-stamina float; the snapshot only
                      carried `breathHoldStamina`, which is scope steadiness and
                      does not move when you sprint. `soldier.stamina` /
                      `staminaMax` are now read through the SoldierMovement
                      component, so the detector exists.

  no_reload           was written as `no_reload_sustained` and blocked on
                      bFiring / bReloading. It turned out not to need either:
                      Squad is a per-magazine system, so the summed magazine
                      count falls when a player fires and does NOT fall when
                      they reload. Consumption is therefore measurable directly,
                      and the flags would only have said what the numbers
                      already do.

  remote_mine         was blocked on the mine's placer — `read_deployable`
                      emitted OwningFob and nobody to accuse. It now resolves
                      the placer PlayerState (with a sticky cache, because the
                      link nulls out when the placer disconnects), so a mine
                      carries the name and account id of whoever put it there.

One entry is still genuinely blocked, and one caveat is worth keeping:

  remote_shovel       needs SQSoldier.ShovelAction (a replicated enum). We can
                      see who holds a shovel from the weapon className, but not
                      whether they are *using* it, and "holding a shovel near a
                      damaged FOB" is exactly the kind of proximity guess this
                      project refuses to make.

  projectile firers   RESOLVED. `read_projectile` originally chased an
                      instigator-controller field that produced a name for
                      none of 95494 projectiles in a four-match archive; a
                      live-fire probe then showed Squad stamps the engine's
                      own Instigator pawn instead, and the reader now follows
                      pawn -> player state (both offsets from reflection).
                      Live verification: 94 of 94 rocket and smoke sightings
                      named their firer. The `fire_no_ammo` projectile path
                      is live from that fix onward — but note the reference
                      corpus predates it, so the launcher path's
                      false-positive behaviour is unproven until
                      plugin_replay runs over post-fix recordings.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from . import register
from .base import Plugin, TickContext

# causerWeapon substrings that mean somebody swung something.
_MELEE_KEYWORDS: tuple[str, ...] = ("knife", "bayonet", "baton", "machete")

_CM_PER_M = 100.0


def _resolve_player(by_eos: dict, by_name: dict, eos: Optional[str],
                    name: Optional[str]) -> Optional[dict]:
    """The player an event names — by account id first, by name second.

    Both lookups are needed, and the fallback is not a nicety. The kill feed's
    ids come from the server LOG, which names players by their EOS account id;
    the snapshot's come from memory. On a licensed server those agree. On a real
    archive of four 100-player matches they did not: every log id was a 32-hex
    EOS id and every snapshot id a UUID, so an id-only lookup resolved 0 of the
    789 damage events that carried a causer, while the name resolved 780.

    The old code took the id branch whenever an id was present and never fell
    back, so on that server every damage-event detector here was inert — not
    quiet, inert. Ids stay first because a name is only unique by convention;
    the fallback runs exactly when the id fails.
    """
    if eos:
        hit = by_eos.get(eos)
        if hit is not None:
            return hit
    return by_name.get(name) if name else None


def _held_weapon(p: dict) -> tuple[Optional[str], Optional[list]]:
    """The class name and magazine list of the gun a player is holding now.

    A stale soldier block is refused, for the same reason `_xy` refuses one: it
    is last tick's reading, not this one's. It matters more here than it looks.
    A frozen magazine list is exactly what an ammo cheat is supposed to look
    like, so a stale read does not merely weaken the signal — it manufactures
    it. Seen in the archive: a soldier goes stale, the pool sticks at
    [30, 29, 30, 30, 30, 30] for a dozen ticks, and kills keep arriving.
    """
    sol = p.get("soldier")
    if not isinstance(sol, dict) or sol.get("stale"):
        return (None, None)
    wep = sol.get("weapon")
    if not isinstance(wep, dict):
        return (None, None)
    cls = wep.get("className")
    mags = wep.get("magazines")
    return (cls if isinstance(cls, str) and cls else None,
            mags if isinstance(mags, list) else None)


def _mag_sum(mags: list) -> int:
    return sum(m for m in mags if isinstance(m, int) and not isinstance(m, bool))


def _mag_max(mags: list) -> int:
    return max((m for m in mags if isinstance(m, int)
                and not isinstance(m, bool)), default=0)


def _entity_xy(e: dict) -> Optional[tuple[float, float]]:
    """A world entity's (x, y) in cm, or None if it has no real position."""
    pos = e.get("position")
    if not isinstance(pos, dict):
        return None
    x, y = pos.get("x"), pos.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return (float(x), float(y))


def _angle_diff_deg(yaw_deg: float, dx: float, dy: float) -> float:
    """Smallest angle (0..180) between a yaw and the direction (dx, dy).

    UE yaw convention: 0 = +X, 90 = +Y, increasing counter-clockwise.
    """
    target = math.degrees(math.atan2(dy, dx))
    return abs((yaw_deg - target + 540.0) % 360.0 - 180.0)


def _xy(p: dict) -> Optional[tuple[float, float]]:
    """A player's (x, y) in cm — only when it is a real, current reading."""
    sol = p.get("soldier")
    if not isinstance(sol, dict) or sol.get("stale"):
        return None
    pos = sol.get("position")
    if not isinstance(pos, dict):
        return None
    x, y = pos.get("x"), pos.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return (float(x), float(y))


def _dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1]) / _CM_PER_M


def _occupied_eos(snapshot: dict) -> set[str]:
    """Everyone sitting in a vehicle seat this tick.

    A passenger's body position is replicated from the vehicle and lags it, so a
    helicopter passenger routinely "moves" at 40 m/s. Foot checks must exclude
    them, and `soldier.attached` alone does not always catch it.
    """
    out: set[str] = set()
    for v in (snapshot.get("vehicles") or []):
        for s in (v.get("seats") or []):
            eos = s.get("occupantEosId")
            if eos:
                out.add(eos)
    return out


class _PlayerState:
    """Per-player rolling state. Lives as long as the plugin does."""

    __slots__ = ("last_xy", "last_elapsed", "last_soldier_addr", "speed_streak",
                 "ammo", "magic_streak", "last_report", "stamina_streak",
                 "shots", "rounds")

    def __init__(self) -> None:
        self.last_xy: Optional[tuple[float, float]] = None
        self.last_elapsed: Optional[float] = None
        self.last_soldier_addr: Optional[str] = None
        self.speed_streak: float = 0.0
        # weapon class -> {"events": n, "first_ts": t, "last_ts": t, "ammo": n}
        self.ammo: dict[str, dict] = {}
        self.magic_streak: int = 0
        # (alert_type) -> last emit ts
        self.last_report: dict[str, float] = {}
        # Game-clock seconds spent moving at sprint speed on a full bar.
        self.stamina_streak: float = 0.0
        # HELD weapon class -> {"shots": n, "first_ts": t, "last_ts": t,
        #                       "ammo": n} — the fire_no_ammo ledger. Keyed on
        # the gun whose magazines we can see, not on what an event called it.
        self.shots: dict[str, dict] = {}
        # The no_reload ledger: one slot for the currently held weapon.
        # {"weapon": cls, "cap": n, "sum": n, "elapsed": t, "addr": a,
        #  "window": [(ts, rounds), ...], "strikes": n}
        self.rounds: Optional[dict] = None


@register
class CheatDetect(Plugin):
    PLUGIN_ID = "cheat_detect"
    LABEL = "Cheat detection"
    DESCRIPTION = ("Memory-verified anomaly detection. Alert-only; every "
                   "threshold is anchored to a physical or mechanical limit.")

    DEFAULT_CONFIG: dict[str, Any] = {
        # Same (player, alert_type) is silenced for this long after it fires.
        "report_cooldown_minutes": 5.0,

        # ---- speedhack (foot only) ----
        # Squad's sprint cap is ~7.8 m/s (MaxWalkSpeed ~400 uu/s ×
        # SprintSpeedMultiplier 1.94). The source walked this threshold 12 -> 15
        # -> 18 as review turned up legitimate players crossing it: parachute
        # landings, fall momentum after bailing from a moving vehicle, and the
        # helicopter-passenger position leak that puts a body where the heli was
        # seconds ago. 18 m/s is ~2.3x the sprint cap and still far under real
        # offenders, who cluster at 22-30 m/s. Do not lower it.
        "detect_speedhack": True,
        "speed_max_foot_mps": 18.0,
        # How long the player has to stay over the limit before it is called.
        # In SECONDS, measured from the game's own clock, so it means the same
        # thing at every --hz. It used to be a tick count, which only meant 16
        # seconds at the one tick rate it was written for: the same 8 there is
        # 2.7 s at 3 Hz, and a parachute landing or a bail from a moving truck
        # lasts about that long. That is a false accusation waiting for a
        # config nobody thought to re-scale.
        "speed_sustained_seconds": 16.0,

        # ---- infinite ammo ----
        # Same player, same weapon, N damage events, and their carried ammo
        # never went down.
        "detect_infinite_ammo": True,
        "inf_ammo_min_damage_events": 3,
        # Give reload windows room to land before calling it.
        "inf_ammo_min_window_seconds": 30.0,
        # If the last event we counted is older than this, the suspicion is
        # stale: a player who fired a burst and then resupplied in main looks
        # identical to a cheater until you notice they stopped shooting. A real
        # one keeps firing, so their last event is always fresh.
        #
        # These two interact, and the interaction is the detector's blind spot:
        # to reach the 30 s window you need events no more than 15 s apart, so
        # somebody who fires only every 20 s never accumulates and is never
        # flagged. That is the accepted price of near-zero false positives.
        # Widening the staleness window closes the gap and re-opens the burst-
        # then-resupply false positive — do not do it without a way to tell
        # those two apart.
        "inf_ammo_stale_seconds": 15.0,

        # ---- stamina hack (EXPERIMENTAL, off) ----
        # Sprinting drains stamina in Squad, universally: there is no kit, role
        # or state in which a soldier runs at sprint speed on a bar that does
        # not move. So the tell is neither the speed nor the stamina but the two
        # together, held long enough that no amount of stop-start could produce
        # it.
        #
        # 5.5 m/s sits between the walk (~4.0) and the sprint cap (~7.8): above
        # it a soldier is sprinting whatever else is true, and the margin below
        # the cap absorbs the position jitter a 1 Hz sample carries. 0.98 of the
        # bar counts as "not draining" — the field is a float read live and the
        # last percent is noise. Twenty seconds is roughly two full
        # sprint-to-empty cycles, so a legitimate player who sprints, stops and
        # sprints again never accumulates it.
        #
        # OFF until it has been measured against recorded matches
        # (scripts/plugin_replay.py). Every detector here is an accusation
        # generator; this one has never run anywhere.
        #
        # There is an upper bound as well as a lower one, and it is not
        # decoration. A stamina cheat lets you sprint indefinitely; it does not
        # make you faster than a sprint. Above the cap the movement has some
        # other explanation — a vehicle seat we failed to resolve, a parachute,
        # the position leak that puts a mounted soldier where the helicopter
        # was — and that is speedhack's business, with speedhack's tuning
        # history behind it. On the reference archive this detector's only
        # alert was at 18.4 m/s: the same player, in the same moment, that
        # speedhack reported at 18.2. One phenomenon, two accusations, and only
        # one of them was about stamina. 9.0 leaves the 7.8 cap a margin for
        # slope and for the jitter a 1 Hz position sample carries.
        "detect_stamina_hack": False,
        "stamina_sprint_min_mps": 5.5,
        "stamina_sprint_max_mps": 9.0,
        "stamina_full_fraction": 0.98,
        "stamina_sustained_seconds": 20.0,

        # ---- firing with a frozen ammo ledger (EXPERIMENTAL, off) ----
        # `infinite_ammo` above assumes a cheater's carried ammo stops falling.
        # That is unconfirmed and quite possibly backwards: Squad's client holds
        # enough authority over the firing path for these cheats to exist at
        # all, so the server's copy may well keep decrementing while the client
        # fires anyway. This detector therefore takes no position on it — it
        # counts VERIFIED SHOTS and only then asks whether the ledger moved.
        #
        # A shot is verified two ways: a damage event whose causer is exactly
        # the gun the player is holding, and a projectile spawned this tick
        # whose firer the reader resolved from memory. The second path needs no
        # damage at all, which is the point — spraying a treeline produces no
        # events and plenty of rounds.
        #
        # Four shots inside ten seconds is a burst nobody fires by accident,
        # and ten seconds is short enough that a reload would have had to land
        # inside it. An ammo sum that RISES is a resupply and resets the
        # observation; one that FALLS is a player spending rounds like everyone
        # else and also resets. Only a ledger that sits perfectly still while
        # shots land is evidence.
        "detect_fire_no_ammo": False,
        "fire_no_ammo_min_shots": 4,
        "fire_no_ammo_min_window_seconds": 10.0,

        # ---- no-reload / impossible consumption (EXPERIMENTAL, off) ----
        # The other half of the ammo question: the variant that removes the
        # reload timer but leaves the accounting honest. Squad is a per-magazine
        # system — firing drains the summed total, reloading does not (the
        # partial magazine goes back in the pool) — so a tick-over-tick DROP in
        # the sum is verified fire volume, with no inference anywhere.
        #
        # The ceiling has to scale with the magazine rather than multiply it.
        # A flat "3x capacity" was considered and rejected: a legitimate rifle
        # player mag-dumping at robot speed reaches ~150 rounds in 30 s, which
        # would have crossed a flat 120 — a false accusation waiting for a
        # good machine-gunner. So the legit ceiling is derived from the
        # mandatory dump-then-reload cycle:
        #
        #   cycle_min = capacity / MAX_RPS + RELOAD_MIN
        #   ceiling   = capacity * (window / cycle_min + 1)   # +1: partial mag
        #
        # 17 rounds/s is ~1000 rpm, faster than any infantry weapon in the
        # game; 3 s is faster than any real reload. Both are deliberately
        # unreachable rather than typical — the threshold is a physical limit
        # with margin on top, not an average.
        #
        # Worked, with these defaults: a 30-round rifle gives cycle_min 4.8 s,
        # ceiling ~219, x1.5 = ~328, against a cheater's continuous 350-500 in
        # 30 s — detectable. A 200-round belt box gives ceiling ~606, x1.5 =
        # ~909, so a no-reload MG at ~300 is invisible. That is accepted: belt
        # weapons reload so rarely that removing the timer barely helps, and
        # the detector's value is on magazine weapons where reloads are
        # constant. The 120-round floor guards the windowed path against a
        # capacity estimate that has not settled yet.
        "detect_no_reload": False,
        "noreload_window_seconds": 30.0,
        "noreload_max_rps": 17.0,
        "noreload_reload_min_seconds": 3.0,
        "noreload_margin": 1.5,
        "noreload_min_rounds": 120,
        # Below this observed capacity the windowed model is structurally blind
        # — a grenade launcher holds one round, the whole pool is a handful, and
        # the 120-round floor can never be reached. Under it the rule becomes
        # shot SPACING: two rounds gone inside one tick interval means two shots
        # closer together than one mandatory reload, which is mechanically
        # impossible. Capacity 0 (bayonets and binoculars present as [0] in real
        # data) arms neither path — they cannot fire at all.
        #
        # This was 10, and 10 was wrong. The spacing rule's premise is that
        # EVERY shot is followed by a mandatory reload, and that holds only when
        # a magazine holds exactly one round. Between 2 and 9 sit pistols
        # (Makarov 8, TT-33 9), bolt-action rifles (Mosin 6, C14 6, TW-338 5)
        # and the QLZ-87 automatic grenade launcher (6-7) — all of which fire
        # consecutive rounds with no reload whatsoever. Every false positive
        # this detector produced on the reference archive came from exactly
        # those: two pistol rounds in two seconds, called impossible. At 2 the
        # rule arms only for the one-round family it was written for — AT4,
        # RPG, M320, the underbarrel launchers — 51 distinct weapons in that
        # archive. Everything above falls to the windowed path, which stays
        # silent for small pools because the 120-round floor is out of reach,
        # and that silence is the honest answer: at a 1 Hz sample there is no
        # spacing a six-round drum could show us that is mechanically
        # impossible.
        "noreload_smallmag_capacity": 2,
        "noreload_strikes": 2,

        # ---- remote mine (EXPERIMENTAL, off) ----
        # A mine that materializes far from the body that placed it. Legitimate
        # placement is arm's reach, so the budget only has to absorb the drift
        # between the placement and the tick that samples it: a sprinting player
        # covers ~16 m in two 1 s ticks, and 50 m clears that with room while
        # real remote placement is hundreds of metres. The placer link is
        # memory-verified; without it there is nobody to accuse and nothing is
        # raised, exactly as remote_melee does with an unplaceable victim.
        "detect_remote_mine": False,
        "mine_class_keywords": ["mine", "ied"],
        "remote_mine_max_dist_m": 50.0,

        # ---- remote melee ----
        # Squad melee reach is ~1 m. At a 2 s tick a sprinting attacker drifts
        # up to 7.8 x 2 = ~16 m between the shot and the sample, so 25 m clears
        # the drift ceiling while staying far below real cheat ranges (50-200 m).
        # That drift budget was written for a 2 s tick — which is exactly ours.
        "detect_remote_melee": True,
        "remote_melee_max_dist_m": 25.0,

        # ---- magic bullet (EXPERIMENTAL, off) ----
        # Off by default, and it should stay off until it has been checked
        # against recorded matches. The reasoning: yaw is sampled at the tick,
        # not at the shot. At 2 s a player can turn 180 degrees between pulling
        # the trigger and being looked at, so "was facing away" is not a fact we
        # actually have. Left in, wired up, and disabled — with the evidence in
        # the alert so a human can open the replay and judge.
        "detect_magic_bullet": False,
        "magic_bullet_min_angle_deg": 90.0,
        "magic_bullet_consecutive": 4,
        # Only score events sampled close enough to the shot to mean anything.
        "magic_bullet_max_event_age_sec": 2.5,
    }

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config)
        self._players: dict[str, _PlayerState] = {}
        self._seen_events: set[tuple] = set()
        self._last_cache_resets: Optional[int] = None
        # World-object ids carried over from the previous tick. `None` means
        # "we have not seen a previous tick yet" — distinct from an empty set,
        # because on the first tick after attaching, every object in the world
        # is new to US and none of it is new to the WORLD. Diffing against an
        # empty set there would accuse everyone who had ever placed a mine.
        self._prev_deployables: Optional[set[str]] = None
        self._prev_projectiles: Optional[set[str]] = None

    # -- helpers -----------------------------------------------------------
    def _state(self, eos: str) -> _PlayerState:
        st = self._players.get(eos)
        if st is None:
            st = _PlayerState()
            self._players[eos] = st
        return st

    def _cooled_down(self, st: _PlayerState, kind: str, now: float) -> bool:
        window = float(self.config["report_cooldown_minutes"]) * 60.0
        last = st.last_report.get(kind)
        if last is not None and (now - last) < window:
            return False
        st.last_report[kind] = now
        return True

    # -- entry point -------------------------------------------------------
    def on_tick(self, ctx: TickContext) -> None:
        if ctx.match_state != "InProgress":
            # Between matches everything teleports (respawn, map load). Reset
            # rather than reason about it.
            self._players.clear()
            self._seen_events.clear()
            self._prev_deployables = None
            self._prev_projectiles = None
            return

        # A cache reset makes the reader re-read every object, and positions can
        # legitimately jump on the tick it lands. Break every streak instead of
        # explaining away the spike afterwards.
        counts = ctx.snapshot.get("counts") or {}
        resets = counts.get("cacheResets")
        cache_reset = (isinstance(resets, int)
                       and self._last_cache_resets is not None
                       and resets != self._last_cache_resets)
        if isinstance(resets, int):
            self._last_cache_resets = resets

        gs = ctx.game_state()
        elapsed = gs.get("elapsedSec")
        elapsed_f = float(elapsed) if isinstance(elapsed, (int, float)) else None

        by_eos: dict[str, dict] = {}
        by_name: dict[str, dict] = {}
        for p in ctx.players():
            eos, name = p.get("eosId"), p.get("name")
            if eos:
                by_eos[eos] = p
            if name:
                by_name.setdefault(name, p)

        in_vehicle = _occupied_eos(ctx.snapshot)

        # One motion pass feeds every detector that needs a speed. It also owns
        # the per-player position bookkeeping, so two detectors cannot consume
        # each other's previous sample.
        motion: dict[str, tuple[float, float]] = {}
        if (self.config.get("detect_speedhack")
                or self.config.get("detect_stamina_hack")):
            motion = self._motion(by_eos, in_vehicle, elapsed_f, cache_reset)
        if self.config.get("detect_speedhack"):
            self._speedhack(ctx, by_eos, motion)
        if self.config.get("detect_stamina_hack"):
            self._stamina_hack(ctx, by_eos, motion)

        events = self._fresh_events(ctx)
        if events:
            if self.config.get("detect_infinite_ammo"):
                self._infinite_ammo(ctx, events, by_eos, by_name, in_vehicle)
            if self.config.get("detect_remote_melee"):
                self._remote_melee(ctx, events, by_eos, by_name)
            if self.config.get("detect_magic_bullet"):
                self._magic_bullet(ctx, events, by_eos, by_name)

        # Projectile spawns are a shot record in their own right, so the diff
        # runs whenever anything consumes it — and is maintained even when
        # nothing does, so switching a detector on mid-match does not hand it a
        # world full of "new" objects.
        new_projectiles = self._new_projectiles(ctx, cache_reset)
        if self.config.get("detect_fire_no_ammo"):
            self._fire_no_ammo(ctx, events, new_projectiles, by_eos, by_name,
                               in_vehicle, elapsed_f)
        if self.config.get("detect_no_reload"):
            self._no_reload(ctx, by_eos, in_vehicle, elapsed_f, cache_reset)
        new_deployables = self._new_deployables(ctx, cache_reset)
        if self.config.get("detect_remote_mine"):
            self._remote_mine(ctx, new_deployables, by_eos, by_name)

        # Forget players who left, so state cannot grow without bound across a
        # long-running server.
        live = set(by_eos)
        for eos in [e for e in self._players if e not in live]:
            del self._players[eos]

    def _fresh_events(self, ctx: TickContext) -> list[dict]:
        """Damage events not already counted.

        Log events can be re-delivered across ticks, and every detector here
        counts events, so a duplicate is a false positive waiting to happen. The
        key is deliberately coarse-grained on ts (0.1 s) because the log's
        timestamps are not exact.
        """
        out = []
        for ev in ctx.damage_events():
            ts = ev.get("ts")
            key = (round(float(ts), 1) if isinstance(ts, (int, float)) else None,
                   ev.get("attacker"), ev.get("victim"), ev.get("causerWeapon"))
            if key in self._seen_events:
                continue
            self._seen_events.add(key)
            out.append(ev)
        if len(self._seen_events) > 4000:
            self._seen_events.clear()   # bounded; a match is over long before this
        return out

    # -- shared measurement ------------------------------------------------
    def _motion(self, by_eos: dict, in_vehicle: set,
                elapsed: Optional[float],
                cache_reset: bool) -> dict[str, tuple[float, float]]:
        """Foot speed and the game-clock interval it was measured over.

        Only players whose two samples are genuinely comparable appear in the
        result. Everyone else is re-anchored here and their streaks dropped, so
        no detector downstream has to know what a respawn or a cache reset looks
        like.
        """
        out: dict[str, tuple[float, float]] = {}
        for eos, p in by_eos.items():
            st = self._state(eos)
            sol = p.get("soldier") or {}
            xy = _xy(p)
            addr = sol.get("addr")

            on_foot = (
                xy is not None
                and not sol.get("attached")
                and eos not in in_vehicle
                and isinstance(sol.get("health"), (int, float))
                and sol["health"] > 0)

            # Any of these means the previous sample is not comparable to this
            # one: a new pawn (respawn), a context change, a cache reset. Drop
            # the streaks and re-anchor rather than measure across the gap.
            if not on_foot or cache_reset or addr != st.last_soldier_addr:
                st.speed_streak = 0.0
                st.stamina_streak = 0.0
                st.last_xy = xy
                st.last_elapsed = elapsed
                st.last_soldier_addr = addr
                continue

            dt = None
            if elapsed is not None and st.last_elapsed is not None:
                dt = elapsed - st.last_elapsed
            if dt is None or dt <= 0.0 or st.last_xy is None or xy is None:
                st.last_xy = xy
                st.last_elapsed = elapsed
                continue

            speed = _dist_m(st.last_xy, xy) / dt
            st.last_xy = xy
            st.last_elapsed = elapsed
            out[eos] = (speed, dt)
        return out

    # -- detectors ---------------------------------------------------------
    def _speedhack(self, ctx: TickContext, by_eos: dict,
                   motion: dict[str, tuple[float, float]]) -> None:
        limit = float(self.config["speed_max_foot_mps"])
        need = float(self.config["speed_sustained_seconds"])

        for eos, (speed, dt) in motion.items():
            st = self._state(eos)
            if speed > limit:
                # Accumulate the game's own seconds rather than counting
                # samples: a sample is worth 2 s on one deployment and 0.33 s
                # on another, and one torn read then buys a cheat call on the
                # fast one. Sixteen seconds is sixteen seconds anywhere.
                st.speed_streak += dt
            else:
                st.speed_streak = 0.0
                continue

            if st.speed_streak >= need and self._cooled_down(
                    st, "speedhack", ctx.now):
                self.alert(
                    ctx, alert_type="speedhack", eos_id=eos,
                    player_name=(by_eos.get(eos) or {}).get("name"),
                    confidence=min(1.0, speed / (limit * 2.0)),
                    details={
                        "speedMps": round(speed, 1),
                        "limitMps": limit,
                        "sustainedSeconds": round(st.speed_streak, 1),
                        "sprintCapMps": 7.8,
                    })

    def _stamina_hack(self, ctx: TickContext, by_eos: dict,
                      motion: dict[str, tuple[float, float]]) -> None:
        """Sprint-class movement on a bar that never falls.

        Sprinting drains stamina in Squad without exception, so the pair is the
        signal — neither half is suspicious alone. An unreadable bar breaks the
        streak instead of being assumed full: no reading, no accusation.
        """
        min_speed = float(self.config["stamina_sprint_min_mps"])
        max_speed = float(self.config["stamina_sprint_max_mps"])
        full = float(self.config["stamina_full_fraction"])
        need = float(self.config["stamina_sustained_seconds"])

        for eos, (speed, dt) in motion.items():
            st = self._state(eos)
            p = by_eos.get(eos) or {}
            sol = p.get("soldier") or {}
            stam, smax = sol.get("stamina"), sol.get("staminaMax")
            if (not isinstance(stam, (int, float))
                    or not isinstance(smax, (int, float)) or smax <= 0):
                st.stamina_streak = 0.0      # torn read — measure nothing
                continue
            frac = float(stam) / float(smax)
            if min_speed <= speed <= max_speed and frac >= full:
                st.stamina_streak += dt
            else:
                st.stamina_streak = 0.0
                continue

            if st.stamina_streak >= need and self._cooled_down(
                    st, "stamina_hack", ctx.now):
                self.alert(
                    ctx, alert_type="stamina_hack", eos_id=eos,
                    player_name=p.get("name"), confidence=0.7,
                    details={
                        "speedMps": round(speed, 1),
                        "staminaFraction": round(frac, 3),
                        "sustainedSeconds": round(st.stamina_streak, 1),
                        "sprintBandMps": [min_speed, max_speed],
                        "sprintCapMps": 7.8,
                    })

    def _infinite_ammo(self, ctx: TickContext, events: list[dict],
                       by_eos: dict, by_name: dict, in_vehicle: set) -> None:
        min_events = int(self.config["inf_ammo_min_damage_events"])
        min_window = float(self.config["inf_ammo_min_window_seconds"])
        stale_after = float(self.config["inf_ammo_stale_seconds"])

        for ev in events:
            attacker = _resolve_player(by_eos, by_name,
                                       ev.get("attackerEosId"),
                                       ev.get("attacker"))
            if attacker is None:
                continue
            eos = attacker.get("eosId")
            if not eos or eos in in_vehicle:
                continue  # vehicle ammo has its own resupply path — not our call
            weapon = ev.get("causerWeapon")
            held, mags = _held_weapon(attacker)
            if not weapon or not held or not mags:
                continue
            # The event must be about the gun they are actually holding — after a
            # weapon swap the ammo we can see is a different gun's. Matched
            # EXACTLY: this was a substring test, and across 2499 comparable
            # events in a four-match archive it never once paired anything
            # exact equality missed, while a substring rule can pair two
            # unrelated class names by accident. What the loose test does let
            # through is what it should not: an explosive names its projectile
            # and a vehicle weapon names the vehicle, and neither is the gun
            # whose magazines we are reading.
            if weapon != held:
                continue

            ammo = _mag_sum(mags)
            st = self._state(eos)
            slot = st.ammo.get(weapon)
            ts = float(ev.get("ts") or ctx.now)

            if slot is None or (ts - slot["last_ts"]) > stale_after:
                st.ammo[weapon] = {"events": 1, "first_ts": ts, "last_ts": ts,
                                   "ammo": ammo}
                continue

            # Ammo went down: they reloaded/spent rounds like everyone else.
            if ammo < slot["ammo"]:
                st.ammo[weapon] = {"events": 1, "first_ts": ts, "last_ts": ts,
                                   "ammo": ammo}
                continue

            slot["events"] += 1
            slot["last_ts"] = ts
            slot["ammo"] = max(slot["ammo"], ammo)

            window = ts - slot["first_ts"]
            if (slot["events"] >= min_events and window >= min_window
                    and self._cooled_down(st, "infinite_ammo", ctx.now)):
                self.alert(
                    ctx, alert_type="infinite_ammo", eos_id=eos,
                    player_name=attacker.get("name"), confidence=0.8,
                    details={
                        "weapon": weapon,
                        "damageEvents": slot["events"],
                        "windowSec": round(window, 1),
                        "ammoUnchangedAt": slot["ammo"],
                    })
                st.ammo.pop(weapon, None)

    # -- world-object diffs -------------------------------------------------
    def _diff_new(self, entities: list, prev: Optional[set[str]],
                  cache_reset: bool) -> tuple[list[dict], set[str]]:
        """Entities that appeared THIS tick, and the id set to carry forward.

        The first tick after attaching — and the first after a cache reset,
        which makes the reader re-read the world from scratch — reports nothing
        new. Everything visible then is new to us and old to the world, and a
        reader restarting mid-match must not accuse every player who has ever
        placed a mine or fired a rocket.
        """
        ids: set[str] = set()
        fresh: list[dict] = []
        for e in entities:
            if not isinstance(e, dict):
                continue
            eid = e.get("id")
            if not isinstance(eid, str) or not eid:
                continue      # no stable identity — cannot say it is new
            ids.add(eid)
            if prev is not None and not cache_reset and eid not in prev:
                fresh.append(e)
        return (fresh, ids)

    def _new_projectiles(self, ctx: TickContext,
                         cache_reset: bool) -> list[dict]:
        fresh, ids = self._diff_new(ctx.snapshot.get("projectiles") or [],
                                    self._prev_projectiles, cache_reset)
        self._prev_projectiles = ids
        return fresh

    def _new_deployables(self, ctx: TickContext,
                         cache_reset: bool) -> list[dict]:
        fresh, ids = self._diff_new(ctx.snapshot.get("deployables") or [],
                                    self._prev_deployables, cache_reset)
        self._prev_deployables = ids
        return fresh

    def _fire_no_ammo(self, ctx: TickContext, events: list[dict],
                      new_projectiles: list[dict], by_eos: dict,
                      by_name: dict, in_vehicle: set,
                      elapsed: Optional[float]) -> None:
        """Verified shots landing against an ammo ledger that never moves.

        Counts shots first and asks about ammo second, which is the whole
        difference from `infinite_ammo`: it makes no assumption about whether a
        cheater's server-side ammo falls. A ledger that RISES is a resupply and
        a ledger that FALLS is an ordinary player; only one that sits perfectly
        still while shots land is evidence.
        """
        min_shots = int(self.config["fire_no_ammo_min_shots"])
        min_window = float(self.config["fire_no_ammo_min_window_seconds"])
        stale_after = float(self.config["inf_ammo_stale_seconds"])
        now_ts = float(elapsed) if elapsed is not None else float(ctx.now)

        # Shots this tick, per player. Two independent kinds of evidence.
        shots: dict[str, int] = {}
        for ev in events:
            if not ev.get("wounded"):
                # Only a WOUND is a shot. The kill that follows is emitted from
                # the log's Die() line and credited to whatever downed the
                # victim — which may have been minutes earlier, and cost a round
                # then, not now. Counting those was this detector's whole false
                # positive rate on the archive: a marksman downs three people,
                # they bleed out over the next twenty seconds, and the ammo
                # ledger sits perfectly still throughout because nothing was
                # fired. Insta-kills are lost with them (they present the same
                # way), which is the acceptable direction to be wrong in.
                continue
            attacker = _resolve_player(by_eos, by_name,
                                       ev.get("attackerEosId"),
                                       ev.get("attacker"))
            if attacker is None:
                continue
            eos = attacker.get("eosId")
            held, _mags = _held_weapon(attacker)
            if not eos or not held:
                continue
            # Only an event naming the gun we can read the magazines of counts.
            # An explosive names its projectile and a vehicle weapon names the
            # vehicle; pairing either with an infantry magazine count would be
            # a guess, and mapping one to the other needs a lookup table this
            # project will not build.
            if ev.get("causerWeapon") != held:
                continue
            shots[eos] = shots.get(eos, 0) + 1
        for pr in new_projectiles:
            firer = pr.get("firer")
            if not isinstance(firer, str) or not firer:
                # The memory-verified firer link — live since the reader
                # switched to the Instigator-pawn chain (94/94 sightings
                # named in live fire). Rounds recorded before that fix carry
                # no name and are skipped, never guessed; a launcher fired at
                # a treeline produces rounds and no damage events, which is
                # why this path exists at all.
                continue
            p = by_name.get(firer)
            eos = p.get("eosId") if p else None
            if not eos:
                continue
            shots[eos] = shots.get(eos, 0) + 1

        for eos, n in shots.items():
            if eos in in_vehicle:
                continue      # vehicle ammo has its own resupply path
            p = by_eos.get(eos)
            if p is None:
                continue
            held, mags = _held_weapon(p)
            if not held or not mags:
                continue
            ammo = _mag_sum(mags)
            st = self._state(eos)
            slot = st.shots.get(held)
            if (slot is None or ammo != slot["ammo"]
                    or (now_ts - slot["last_ts"]) > stale_after):
                # First sighting, ammo moved in either direction, or the run
                # went stale. Re-anchor: only an unbroken run counts.
                st.shots = {held: {"shots": n, "first_ts": now_ts,
                                   "last_ts": now_ts, "ammo": ammo}}
                continue
            slot["shots"] += n
            slot["last_ts"] = now_ts
            window = now_ts - slot["first_ts"]
            if (slot["shots"] >= min_shots and window >= min_window
                    and self._cooled_down(st, "fire_no_ammo", ctx.now)):
                self.alert(
                    ctx, alert_type="fire_no_ammo", eos_id=eos,
                    player_name=p.get("name"), confidence=0.8,
                    details={
                        "weapon": held,
                        "verifiedShots": slot["shots"],
                        "windowSec": round(window, 1),
                        "ammoUnchangedAt": ammo,
                        "magazines": list(mags),
                    })
                st.shots.pop(held, None)

    def _no_reload(self, ctx: TickContext, by_eos: dict, in_vehicle: set,
                   elapsed: Optional[float], cache_reset: bool) -> None:
        """Rounds leaving the pool faster than reloading could allow.

        Squad is a per-magazine system: firing drains the summed total and
        reloading does not, because the partial magazine goes back in the pool.
        A drop is therefore verified fire volume with nothing inferred. A rise
        is a resupply, which can only hide consumption — the error direction is
        towards missing a cheater, and that is the one to prefer.
        """
        window_s = float(self.config["noreload_window_seconds"])
        max_rps = float(self.config["noreload_max_rps"])
        reload_min = float(self.config["noreload_reload_min_seconds"])
        margin = float(self.config["noreload_margin"])
        min_rounds = int(self.config["noreload_min_rounds"])
        small_cap = int(self.config["noreload_smallmag_capacity"])
        need_strikes = int(self.config["noreload_strikes"])
        if elapsed is None or max_rps <= 0 or reload_min <= 0:
            return                       # no game clock, nothing to measure

        for eos, p in by_eos.items():
            st = self._state(eos)
            sol = p.get("soldier") or {}
            addr = sol.get("addr")
            held, mags = _held_weapon(p)
            if not held or mags is None:
                continue                 # nothing readable — measure nothing
            slot = st.rounds
            # A different gun's pool, a different body, a seat, or a reader
            # that just re-read the world: none of those are comparable to the
            # previous sample.
            if (slot is None or slot["weapon"] != held or slot["addr"] != addr
                    or eos in in_vehicle or cache_reset):
                st.rounds = {"weapon": held, "addr": addr,
                             "cap": _mag_max(mags), "sum": _mag_sum(mags),
                             "elapsed": float(elapsed), "window": [],
                             "strikes": 0}
                continue

            cap = max(int(slot["cap"]), _mag_max(mags))
            slot["cap"] = cap
            total = _mag_sum(mags)
            dt = float(elapsed) - float(slot["elapsed"])
            drop = int(slot["sum"]) - total
            slot["sum"] = total
            slot["elapsed"] = float(elapsed)
            if dt <= 0 or drop <= 0 or cap < 1:
                # cap 0 is a bayonet or a pair of binoculars — they present as
                # [0] magazines in real data and can never fire.
                continue

            if cap < small_cap:
                # Single-shot path: launchers and grenade launchers, where the
                # windowed model is structurally blind. Two rounds gone inside
                # one tick interval means two shots closer together than one
                # mandatory reload, and the allowance grows with the interval
                # so a slow sampler cannot manufacture a strike.
                allowed = 2 + int(dt // reload_min)
                if drop < allowed:
                    continue
                slot["strikes"] = int(slot["strikes"]) + 1
                if slot["strikes"] < need_strikes:
                    continue
                if self._cooled_down(st, "no_reload", ctx.now):
                    self.alert(
                        ctx, alert_type="no_reload", eos_id=eos,
                        player_name=p.get("name"), confidence=0.7,
                        details={
                            "weapon": held,
                            "path": "single-shot spacing",
                            "roundsConsumed": drop,
                            "dtSec": round(dt, 2),
                            "capacityEstimate": cap,
                            "minRoundsForStrike": allowed,
                            "strikes": slot["strikes"],
                            "reloadMinSec": reload_min,
                        })
                slot["strikes"] = 0
                continue

            # Windowed path: magazine weapons, where reloads are constant and
            # removing the timer is worth something.
            win = slot["window"]
            win.append((float(elapsed), drop))
            cutoff = float(elapsed) - window_s
            while win and win[0][0] < cutoff:
                win.pop(0)
            consumed = sum(d for _t, d in win)
            cycle_min = cap / max_rps + reload_min
            ceiling = cap * (window_s / cycle_min + 1.0)
            if consumed < min_rounds or consumed <= ceiling * margin:
                continue
            if self._cooled_down(st, "no_reload", ctx.now):
                self.alert(
                    ctx, alert_type="no_reload", eos_id=eos,
                    player_name=p.get("name"), confidence=0.7,
                    details={
                        "weapon": held,
                        "path": "rolling window",
                        "roundsConsumed": consumed,
                        "windowSec": window_s,
                        "capacityEstimate": cap,
                        "ceiling": round(ceiling, 1),
                        "threshold": round(ceiling * margin, 1),
                        "cycleMinSec": round(cycle_min, 2),
                    })
            slot["window"] = []

    def _remote_mine(self, ctx: TickContext, new_deployables: list[dict],
                     by_eos: dict, by_name: dict) -> None:
        """A mine that appears somewhere its placer's body is not.

        Placement is arm's reach in Squad, so the only thing the budget has to
        absorb is the drift between the placement and the tick that samples it.
        Both positions are taken from the SAME tick; an unresolvable placer or a
        missing position raises nothing, which is the rule `remote_melee` has
        always applied to an unplaceable victim.
        """
        keywords = tuple(k.lower() for k in self.config["mine_class_keywords"]
                         if isinstance(k, str) and k)
        max_d = float(self.config["remote_mine_max_dist_m"])
        if not keywords:
            return
        for d in new_deployables:
            cls = d.get("classShort")
            if not isinstance(cls, str) or not cls:
                continue
            low = cls.lower()
            if not any(k in low for k in keywords):
                continue
            placer = _resolve_player(by_eos, by_name, d.get("placerEosId"),
                                     d.get("placer"))
            if placer is None:
                continue              # nobody to accuse
            eos = placer.get("eosId")
            d_xy, p_xy = _entity_xy(d), _xy(placer)
            if not eos or d_xy is None or p_xy is None:
                continue              # no distance, no accusation
            dist = _dist_m(p_xy, d_xy)
            if dist <= max_d:
                continue
            st = self._state(eos)
            if self._cooled_down(st, "remote_mine", ctx.now):
                self.alert(
                    ctx, alert_type="remote_mine", eos_id=eos,
                    player_name=placer.get("name"), confidence=0.85,
                    details={
                        "deployable": cls,
                        "distanceM": round(dist, 1),
                        "driftBudgetM": max_d,
                        "placerXY": [round(p_xy[0], 1), round(p_xy[1], 1)],
                        "deployableXY": [round(d_xy[0], 1), round(d_xy[1], 1)],
                    })

    def _remote_melee(self, ctx: TickContext, events: list[dict],
                      by_eos: dict, by_name: dict) -> None:
        max_d = float(self.config["remote_melee_max_dist_m"])
        for ev in events:
            if not ev.get("killed") or ev.get("teamKill"):
                continue
            weapon = (ev.get("causerWeapon") or "").lower()
            if not any(k in weapon for k in _MELEE_KEYWORDS):
                continue
            attacker = _resolve_player(by_eos, by_name,
                                       ev.get("attackerEosId"),
                                       ev.get("attacker"))
            victim = by_name.get(ev.get("victim"))
            if attacker is None or victim is None:
                continue          # cannot place one of them — no accusation
            a_xy, v_xy = _xy(attacker), _xy(victim)
            if a_xy is None or v_xy is None:
                continue

            dist = _dist_m(a_xy, v_xy)
            if dist <= max_d:
                continue
            eos = attacker.get("eosId")
            if not eos:
                continue
            st = self._state(eos)
            if self._cooled_down(st, "remote_melee", ctx.now):
                self.alert(
                    ctx, alert_type="remote_melee", eos_id=eos,
                    player_name=attacker.get("name"), confidence=0.9,
                    details={
                        "weapon": ev.get("causerWeapon"),
                        "victim": ev.get("victim"),
                        "distanceM": round(dist, 1),
                        "meleeRangeM": 1.0,
                        "driftBudgetM": max_d,
                    })

    def _magic_bullet(self, ctx: TickContext, events: list[dict],
                      by_eos: dict, by_name: dict) -> None:
        min_angle = float(self.config["magic_bullet_min_angle_deg"])
        need = int(self.config["magic_bullet_consecutive"])
        max_age = float(self.config["magic_bullet_max_event_age_sec"])

        for ev in events:
            attacker = _resolve_player(by_eos, by_name,
                                       ev.get("attackerEosId"),
                                       ev.get("attacker"))
            victim = by_name.get(ev.get("victim"))
            if attacker is None or victim is None:
                continue
            eos = attacker.get("eosId")
            if not eos:
                continue
            ts = ev.get("ts")
            if isinstance(ts, (int, float)) and abs(ctx.now - float(ts)) > max_age:
                continue   # sampled too long after the shot to mean anything
            a_xy, v_xy = _xy(attacker), _xy(victim)
            yaw = (attacker.get("soldier") or {}).get("yaw")
            if a_xy is None or v_xy is None or not isinstance(yaw, (int, float)):
                continue

            angle = _angle_diff_deg(float(yaw), v_xy[0] - a_xy[0], v_xy[1] - a_xy[1])
            st = self._state(eos)
            if angle < min_angle:
                st.magic_streak = 0
                continue
            st.magic_streak += 1
            if st.magic_streak >= need and self._cooled_down(
                    st, "magic_bullet", ctx.now):
                self.alert(
                    ctx, alert_type="magic_bullet", eos_id=eos,
                    player_name=attacker.get("name"),
                    # Deliberately low: yaw is tick-sampled, not shot-sampled.
                    confidence=0.4,
                    details={
                        "angleDeg": round(angle, 1),
                        "consecutive": st.magic_streak,
                        "victim": ev.get("victim"),
                        "caveat": ("yaw is sampled at the tick, not at the shot "
                                   "— confirm in the replay before acting"),
                    })


__all__ = ["CheatDetect"]
