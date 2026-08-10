"""Synthetic match ids for UNLICENSED Squad servers.

A Squad server with no OWI license key never opens an ODK backend session, so
``SQGameState.MatchID`` stays an empty FString for the whole match while
``MatchState`` still reads ``InProgress`` perfectly. Both the recorder and the
stats store narrow an empty id to ``None``, so such a server records nothing at
all: no ``.sqrx``, no stats rows, and — by design — not even a log line, because
that is the deliberately silent "ambiguous tick" path. The agent looks healthy,
``/health`` stays green and ``doctor`` passes, while it produces zero matches.

Diagnosed live on an unlicensed clan server: it authenticates to OWI's Nakama
backend as ``[<hex>_platform]`` instead of ``[<licenseid>_license]``, gets back
``403 permission denied for user or role``, and logs
``UpdateODKSession: ODK session update failed`` — the match session that would
have carried the id never opens.

Clan servers are custom servers and have no reason to hold a license, so this
is not an edge case to warn about; it is a supported deployment. This module
mints a stable id for those servers so everything downstream works unchanged.

Identity
--------
::

    uuid5(_NAMESPACE, f"{scope}|{server_id}|{server_start_ts}|{start_wall}")

``scope`` is the community central assigned at enrollment, which is globally
unique. That is what keeps two unlicensed clans from ever colliding:
``match.match_id`` is a global PRIMARY KEY in central, so a collision would have
one clan's match silently overwrite another's. The output is a well-formed UUID,
so filenames, central and the UI cannot tell it from a real one.

Continuity
----------
The id is minted ONCE per match and then held. It is deliberately not recomputed
per tick, because the obvious anchor for "when did this match start" is not
stable: ``ElapsedTime`` advances about 5% slower than the server's world clock
(measured live: 19 s of ElapsedTime per 20.00 s of world time), so
``worldTimeSec - elapsedSec`` creeps upward by ~1 s every 20 s. A tolerance
tight enough to separate two matches would have split a 40-minute match into
twenty.

So continuity is decided from signals drift cannot touch:

* ``serverStartTimestamp`` changed -> Squad restarted -> new match
* ``mapName`` changed                                 -> new match
* ``elapsedSec`` went DOWN         -> counter reset   -> new match
* none of the above                -> same match, keep the id

Monotonicity of ``elapsedSec`` carries this: it only asks whether the counter
went up, never by how much, so the creep is irrelevant. The anchor survives only
as a secondary check for the one case monotonicity misses — the agent being down
long enough that the same layer is replayed and the new match's elapsed is
already higher than the last value we saw. That check gets a drift-aware
envelope rather than a fixed tolerance.

A new match is confirmed over consecutive ticks before it is minted. A single
torn read of any of these fields would otherwise mint a fresh id mid-match, and
the recorder treats an id change as a match boundary — one bad tick would split
both the recording and the stats row. While unconfirmed we emit nothing, which
lands on the same already-handled path a licensed server takes between matches.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

# Fixed namespace: the same (scope, server, match) must always hash to the same
# UUID, so a reinstall cannot renumber a match that is still open.
_NAMESPACE = uuid.UUID("6f0d5a3e-8d5a-5f1e-9a4c-0f9d1c2b3a44")

# Consecutive agreeing ticks required before a new match is minted.
_CONFIRM_TICKS = 3
# `elapsedSec` only climbs within a match; allow a little backwards jitter
# before calling it a reset.
_ELAPSED_SLACK_SEC = 3
# Secondary anchor check. The allowance grows with the wall gap because the
# anchor creeps at ~5% of match time; 10% + 60 s is roughly double the observed
# rate, so normal operation never trips it.
_ANCHOR_DRIFT_RATE = 0.10
_ANCHOR_GRACE_SEC = 60.0
# The state file only exists to survive a restart. At 2 Hz a per-tick write
# would be pure churn.
_PERSIST_EVERY_SEC = 30.0

STATE_FILENAME = "synth_match.json"


def _observe(gs: dict) -> Optional[dict[str, Any]]:
    """Pull the identifying fields out of a gameState, or None if any of them
    is unreadable. Missing fields mean we cannot tell one match from the next,
    and inventing an id then would be worse than staying silent."""
    srv = gs.get("serverStartTimestamp")
    elapsed = gs.get("elapsedSec")
    world = gs.get("worldTimeSec")
    name = gs.get("mapName")
    if not isinstance(srv, int) or isinstance(srv, bool) or srv <= 0:
        return None
    if not isinstance(elapsed, int) or isinstance(elapsed, bool) or elapsed < 0:
        return None
    if not isinstance(world, (int, float)) or isinstance(world, bool):
        return None
    if world < 0:
        return None
    return {
        "server_start_ts": srv,
        "elapsed": elapsed,
        "anchor": float(world) - float(elapsed),
        "map_name": name if isinstance(name, str) and name else None,
    }


class SyntheticMatchIds:
    """Fills in ``gameState.matchId`` when — and only when — Squad left it empty.

    Licensed servers never reach the minting path: a non-empty id is returned
    untouched, so this is inert everywhere it is not needed.
    """

    def __init__(self, state_path: str | Path, *, community: str = "",
                 server_id: str = "squad") -> None:
        self.state_path = Path(state_path)
        self.community = (community or "").strip()
        self.server_id = server_id or "squad"
        self._install_id = ""
        self._state: Optional[dict[str, Any]] = None
        self._pending: Optional[dict[str, Any]] = None
        self._pending_n = 0
        self._last_persist = 0.0
        self._announced = False
        self._blind_warned = False
        self._load()

    # -- public ---------------------------------------------------------------
    def apply(self, snap: dict) -> Optional[str]:
        """Mutate ``snap`` in place; return the id we supplied, else None."""
        gs = snap.get("gameState")
        if not isinstance(gs, dict):
            return None
        if gs.get("matchState") != "InProgress":
            # Between matches. The state file is deliberately kept: it is what
            # lets a restart during the NEXT match recognise it, and clearing
            # here would drop the elapsed/anchor baseline for no gain.
            self._pending = None
            self._pending_n = 0
            return None
        existing = gs.get("matchId")
        if isinstance(existing, str) and existing:
            return None  # licensed server — never touch a real id

        obs = _observe(gs)
        if obs is None:
            if not self._blind_warned:
                self._blind_warned = True
                print("synth-match: matchId is empty and the fields needed to "
                      "derive one are unreadable; this match cannot be "
                      "recorded", file=sys.stderr)
            return None

        now = time.time()
        st = self._state
        if st is not None and self._continues(st, obs, now):
            st.update(elapsed=obs["elapsed"], anchor=obs["anchor"], seen_at=now)
            self._pending = None
            self._pending_n = 0
            self._maybe_persist(now)
            match_id = str(st["match_id"])
        else:
            minted = self._confirm_new(obs, now)
            if minted is None:
                return None
            match_id = minted

        gs["matchId"] = match_id
        # Additive marker so central, the hub and support can tell a derived id
        # from one Squad handed us. Nothing downstream reads it yet.
        gs["matchIdSynthetic"] = True
        return match_id

    # -- internals ------------------------------------------------------------
    @staticmethod
    def _continues(prev: dict, obs: dict, now: float) -> bool:
        """Is `obs` the same match `prev` described?"""
        if prev.get("server_start_ts") != obs["server_start_ts"]:
            return False
        if prev.get("map_name") != obs["map_name"]:
            return False
        try:
            prev_elapsed = int(prev.get("elapsed", 0))
            prev_anchor = float(prev.get("anchor", obs["anchor"]))
            seen_at = float(prev.get("seen_at", now))
        except (TypeError, ValueError):
            return False  # corrupt state file — treat as a fresh match
        if obs["elapsed"] < prev_elapsed - _ELAPSED_SLACK_SEC:
            return False
        gap = max(0.0, now - seen_at)
        envelope = _ANCHOR_DRIFT_RATE * gap + _ANCHOR_GRACE_SEC
        return abs(obs["anchor"] - prev_anchor) <= envelope

    def _confirm_new(self, obs: dict, now: float) -> Optional[str]:
        """Require `_CONFIRM_TICKS` mutually consistent observations before
        minting, so one torn read cannot split a match in two."""
        prev = self._pending
        if prev is None or not self._continues(prev, obs, now):
            self._pending = dict(obs, seen_at=now)
            self._pending_n = 1
            return None
        self._pending = dict(obs, seen_at=now)
        self._pending_n += 1
        if self._pending_n < _CONFIRM_TICKS:
            return None

        match_id = self._mint(obs)
        self._state = dict(obs, seen_at=now, match_id=match_id)
        self._pending = None
        self._pending_n = 0
        self._persist(now)
        if not self._announced:
            self._announced = True
            print(f"synth-match: this server reports no matchId (unlicensed "
                  f"custom server); deriving stable ids instead — first is "
                  f"{match_id}", file=sys.stderr)
        return match_id

    def _mint(self, obs: dict) -> str:
        # Computed once, at mint time, so the ~5%/s creep in `anchor` never
        # reaches the id. Its only job is uniqueness, not re-derivability.
        start_wall = int(round(obs["server_start_ts"] + obs["anchor"]))
        # The layer is in the key as well as the continuity check. Two matches
        # cannot really start at the same second on one server, so this is
        # belt-and-braces — but the id is a global primary key in central and
        # the cost of one more field is nothing.
        key = (f"{self._scope()}|{self.server_id}"
               f"|{obs['server_start_ts']}|{start_wall}|{obs['map_name'] or ''}")
        return str(uuid.uuid5(_NAMESPACE, key))

    def _scope(self) -> str:
        """Globally unique prefix. The enrolled community is preferred because
        it is stable across a reinstall; the random install id is only a
        stand-in for a box that has not enrolled yet."""
        return self.community or self._install_id

    # -- persistence ----------------------------------------------------------
    def _load(self) -> None:
        payload: dict[str, Any] = {}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        install = payload.get("install_id")
        self._install_id = (install if isinstance(install, str) and install
                            else str(uuid.uuid4()))
        match = payload.get("match")
        if isinstance(match, dict) and match.get("match_id"):
            self._state = match
        if not isinstance(install, str) or not install:
            # Persist the freshly generated install id now, so an unenrolled box
            # keeps the same scope across restarts.
            self._persist(time.time())

    def _maybe_persist(self, now: float) -> None:
        if now - self._last_persist >= _PERSIST_EVERY_SEC:
            self._persist(now)

    def _persist(self, now: float) -> None:
        payload = {"install_id": self._install_id, "match": self._state}
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self.state_path)
            self._last_persist = now
        except OSError:
            # Losing the file only costs continuity across a restart. It must
            # never break a tick.
            self._last_persist = now
