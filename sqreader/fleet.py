"""Fleet telemetry — what each enrolled agent tells central about itself.

Phase 0 of the remote-update system. On a periodic sealed check-in the agent
reports its version, the Squad BUILD it is attached to, and its reader HEALTH
(the `doctor` drift signal from `health.run_doctor`). Central records this so an
operator can answer, in one glance after a Squad patch, "which of my enrolled
servers just broke, and what are they running?" — the prerequisite for targeting
an offset pack (Phase 1) or a code release (Phase 2) at the servers that need it.

This module only ASSEMBLES the telemetry dict and keeps the restart counter;
`ingest_client.checkin()` seals + sends it over the same per-agent envelope as a
match push. Same opt-in boundary as push (only enrolled, push-enabled boxes
report), and best-effort — a failed check-in never touches the live reader.
"""
from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

from . import __version__, health, squad_build

SCHEMA_CHECKIN = "sqr-checkin-1"
# 5-minute cadence: frequent enough to see a fleet-wide breakage within minutes
# of a Squad patch, rare enough to be free. Covers IDLE servers too — a box that
# never finishes a match still reports its health on this timer.
CHECKIN_INTERVAL_SEC = 300.0


def bump_restarts(state_dir: str | Path) -> int:
    """Increment + return a persistent boot counter. Each serve start is a
    restart under systemd `Restart=always`, so a climbing count is the signal of
    a crash-looping reader. Best-effort; never raises."""
    p = Path(state_dir) / ".sqr_restarts"
    try:
        n = int(p.read_text(encoding="ascii").strip()) + 1
    except (OSError, ValueError):
        n = 1
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(n), encoding="ascii")
    except OSError:
        pass
    return n


def gather_offline(*, reason: str, build_sha: str | None, restarts: int,
                   uptime_sec: float, channel: str) -> dict[str, Any]:
    """Telemetry for an agent that could NOT attach to the game.

    This exists because the check-in used to live behind the attach: a reader
    that could not open the process died before saying so, systemd restarted
    it, and it died again — silently, forever, with nothing reaching central.
    That is the exact shape of a Squad patch breaking the fleet, and it took out
    the one channel that could have fixed it.

    `health="down"` is deliberately a fourth value beside ok/drift/unknown. An
    agent that cannot see the game is not the same as one whose offsets have
    drifted, and treating them alike would hide a bricked box among the noisy
    ones.
    """
    return {
        "schema": SCHEMA_CHECKIN,
        "agent_version": __version__,
        "platform": platform.system(),
        "squad_build": build_sha,
        "engine": None,
        "health": "down",
        "drift": [reason[:200]],
        "restarts": int(restarts),
        "uptime_sec": int(uptime_sec),
        "channel": channel,
    }


def gather(pm: Any, arr: Any, alloc: Any, *, build_sha: str | None,
           restarts: int, uptime_sec: float, channel: str,
           sample_actors: list[int] | None = None) -> dict[str, Any]:
    """Assemble the check-in telemetry over the reader's own open handles.

    `health.run_doctor` walks the live class layouts (cheap) and classifies the
    reader as ok / drift / unknown; a `drift` server is one a Squad patch broke.
    The drift list is capped so a totally-broken reader can't send a huge body.

    `sample_actors` is passed straight through to the doctor: a handful of
    actor addresses the CALLER already holds, so the value-based
    ComponentToWorld check can run without this path ever building a snapshot
    of its own.

    `skipped` names the checks that could not measure anything this time.
    Situational skips are fine and self-resolving — an empty server, a layer
    with no lane graph. A CHRONIC one is not: it is a coverage hole that looks
    exactly like a pass, and without this field central cannot tell "ok,
    everything measured" from "ok, but three checks never ran". Additive, so
    the schema string does not move.
    """
    doc = health.run_doctor(pm, arr, alloc, sample_actors=sample_actors)
    skipped = [str(c.get("check")) for c in (doc.get("checks") or [])
               if c.get("state") == "skipped" and c.get("check")]
    return {
        "schema": SCHEMA_CHECKIN,
        "agent_version": __version__,
        "platform": platform.system(),
        "squad_build": build_sha,
        "engine": squad_build.engine_version(pm, arr, alloc),
        "health": doc.get("state", "unknown"),      # ok | drift | unknown
        "drift": (doc.get("drift") or [])[:20],
        "skipped": skipped[:10],
        "restarts": int(restarts),
        "uptime_sec": int(uptime_sec),
        "channel": channel,
    }
