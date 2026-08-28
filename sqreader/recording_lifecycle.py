"""Shared, dependency-free recording lifecycle security constants."""

from __future__ import annotations

import fnmatch
from typing import Any, Mapping, Optional


RECORDING_STATE_ACTIVE = "active"
RECORDING_STATE_FINALIZED = "finalized"
RECORDING_STATE_UNVERIFIED = "unverified"

# Hub authorization and recorder rotation must use the same threshold.  If the
# recorder accepted a new match id sooner than the hub, a transient id could
# create an orphan recording outside the set of ids the HTTP gate protects.
MATCH_TRANSITION_CONFIRM_TICKS = 3

# Only values known to mean "not currently playing" may contribute to an end
# confirmation. Unknown/future/torn strings remain ambiguous and fail closed.
INACTIVE_MATCH_STATES = frozenset({
    "WaitingToStart",
    "Warmup",
    "WaitingPostMatch",
    "EndState",
})


# Field names carrying the game's own name for the mode being played. The live
# snapshot spells it `gameModeName` (an FText read off the game state) with
# `gameModeId` as the blueprint class behind it; a finished recording's sidecar
# flattens the same value to `gameMode`. All three are checked so ONE predicate
# serves the recorder, the stats writer and the archive backfill.
_GAME_MODE_KEYS = ("gameMode", "gameModeName", "gameModeId")


def _mode_and_name(snap: Any) -> tuple[list[str], Optional[str]]:
    """The mode strings and the layer/map name a snapshot or sidecar carries.

    Accepts a live snapshot (``{"gameState": {...}}``), a bare game-state dict,
    or a ``.meta.json`` sidecar — the three shapes the same match is described
    in — and returns only values that were actually readable.
    """
    if not isinstance(snap, Mapping):
        return ([], None)
    gs = snap.get("gameState")
    gs = gs if isinstance(gs, Mapping) else snap
    modes = [v for v in (gs.get(k) for k in _GAME_MODE_KEYS)
             if isinstance(v, str) and v.strip()]
    layer = gs.get("layer")
    name = (layer.get("name") if isinstance(layer, Mapping) else None)
    if not isinstance(name, str) or not name.strip():
        name = gs.get("layerName")
    if not isinstance(name, str) or not name.strip():
        name = gs.get("mapName")
    return (modes, name if isinstance(name, str) and name.strip() else None)


def is_excluded_match(snap: Any, *, game_modes: frozenset[str],
                      layer_patterns: tuple[str, ...]) -> bool:
    """Whether this match is one the operator has asked us not to record.

    Seeding is the case it exists for: a 6-hour two-player session on a seed
    layer is noise dressed as a match, and it lands in the archive, the stats DB
    and the upload queue exactly like a real one. The game names the mode
    itself, so the primary key is that name — no inference from player counts or
    duration.

    ``game_modes`` is matched EXACTLY (case-insensitively) against the mode the
    game reports; ``layer_patterns`` are case-insensitive fnmatch globs against
    the layer name, falling back to the map name when no layer is resolved. The
    patterns are the override hatch for scrims and events, whose mode is a
    normal one.

    **Fails open, deliberately.** Nothing readable means nothing matches means
    ``False``: an unverifiable tick must never cost a competitive recording. The
    no-guess rule applies to skipping just as hard as it applies to display —
    only a match we can positively identify as excluded is skipped.
    """
    modes, name = _mode_and_name(snap)
    if game_modes and modes:
        wanted = {m.strip().lower() for m in game_modes
                  if isinstance(m, str) and m.strip()}
        if any(m.strip().lower() in wanted for m in modes):
            return True
    if layer_patterns and name:
        low = name.strip().lower()
        for pat in layer_patterns:
            if isinstance(pat, str) and pat and fnmatch.fnmatchcase(
                    low, pat.lower()):
                return True
    return False


__all__ = [
    "RECORDING_STATE_ACTIVE",
    "RECORDING_STATE_FINALIZED",
    "RECORDING_STATE_UNVERIFIED",
    "MATCH_TRANSITION_CONFIRM_TICKS",
    "INACTIVE_MATCH_STATES",
    "is_excluded_match",
]
