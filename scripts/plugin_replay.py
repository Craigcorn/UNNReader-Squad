"""Run the detectors over recorded matches and see what they would have said.

A detector is an accusation generator. Its thresholds arrived from another
project, tuned against another server, at another tick rate — and until now the
only way to find out what they do here was to switch them on and watch a
Discord channel fill up with names.

There is no need for that. Every match is on disk, frame for frame, and the
plugin contract takes a snapshot and returns alerts. So the archive can be
replayed through the detectors offline: no game, no network, nothing announced,
and the answer is a count instead of an opinion.

That makes three things possible that were not:

  * **Tuning against evidence.** "18 m/s over 16 seconds fires N times across
    219 matches" beats any argument about what the number should be.
  * **Judging a detector that is switched off.** `magic_bullet` is disabled
    because yaw is sampled at the tick and not at the shot. Here it can be run
    anyway, over real matches, and the question becomes how often it would have
    been wrong.
  * **Catching a regression in a threshold** before it reaches a channel.

    python -m scripts.plugin_replay /path/to/*.sqrx --plugin cheat_detect
    python -m scripts.plugin_replay REC.sqrx --config '{"detect_magic_bullet": true}'
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqreader.plugins import PLUGIN_CLASSES, PluginManager      # noqa: E402
from sqreader.sqrx import SqrxReader                            # noqa: E402


def replay_one(path: Path, plugin_id: str, cfg: dict[str, Any],
               limit: int | None = None) -> list[dict]:
    """Every alert the plugin would have raised over one recording."""
    alerts: list[dict] = []
    mgr = PluginManager({plugin_id: {"enabled": True, "config": cfg}},
                        server_id="replay", emit_alert=alerts.append)
    if not mgr.plugins:
        raise SystemExit(f"plugin {plugin_id!r} did not load")

    n = 0
    with SqrxReader(path) as r:
        for line in r.lines():
            if limit is not None and n >= limit:
                break
            try:
                snap = json.loads(line)
            except ValueError:
                continue
            if not isinstance(snap, dict) or snap.get("t") == "pos":
                # Position frames carry no players/damageEvents in the shape a
                # plugin expects, and the live reader never hands one to a
                # plugin either. Feeding them here would measure something the
                # detector never sees.
                continue
            n += 1
            # `now` comes from the recording, not the wall clock: cooldowns are
            # in real seconds and would otherwise all collapse into one instant.
            ts = snap.get("timestamp")
            now = _epoch(ts) if ts else float(n)
            mgr.run_tick(snap, tick=int(snap.get("tick") or n), now=now)
    return alerts


def _epoch(ts: str) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--plugin", default="cheat_detect")
    ap.add_argument("--config", default="{}",
                    help="JSON overlaid on the plugin's DEFAULT_CONFIG")
    ap.add_argument("--limit-frames", type=int, default=None)
    ap.add_argument("--show", type=int, default=8,
                    help="how many individual alerts to print")
    args = ap.parse_args(argv)

    if args.plugin not in PLUGIN_CLASSES:
        print(f"unknown plugin {args.plugin!r}; have: "
              f"{', '.join(sorted(PLUGIN_CLASSES))}", file=sys.stderr)
        return 2
    cfg = json.loads(args.config)

    by_type: collections.Counter = collections.Counter()
    by_player: collections.Counter = collections.Counter()
    shown = 0
    files = [f for f in args.files if f.is_file()]
    print(f"{args.plugin}: {len(files)} recording(s)"
          + (f", first {args.limit_frames} frames each" if args.limit_frames
             else ""))
    if cfg:
        print(f"config overlay: {json.dumps(cfg, sort_keys=True)}")
    print()

    for f in files:
        try:
            alerts = replay_one(f, args.plugin, cfg, args.limit_frames)
        except Exception as e:                      # a bad file is a result
            print(f"  {f.name[:40]:<40} FAILED {type(e).__name__}: {e}")
            continue
        for a in alerts:
            by_type[a.get("alert_type", "?")] += 1
            by_player[a.get("player_name") or a.get("eos_id") or "?"] += 1
        if alerts:
            print(f"  {f.name[:40]:<40} {len(alerts):>4} alert(s)")
            for a in alerts[:max(0, args.show - shown)]:
                d = json.dumps(a.get("details") or {}, sort_keys=True)
                print(f"      {a.get('alert_type')}  "
                      f"{a.get('player_name')}  conf="
                      f"{a.get('confidence')}  {d[:110]}")
                shown += 1

    print()
    total = sum(by_type.values())
    print(f"TOTAL {total} alert(s) over {len(files)} recording(s)")
    for t, n in by_type.most_common():
        print(f"  {t:<24} {n}")
    if by_player:
        print("most-alerted players:")
        for p, n in by_player.most_common(5):
            print(f"  {str(p)[:32]:<32} {n}")
    # A detector that fires on everybody is not a detector, it is a threshold
    # in the wrong place — so say that plainly rather than leaving it in a list.
    if len(by_player) > 20:
        print(f"\n  NOTE: {len(by_player)} distinct players were flagged. "
              f"That is a threshold problem, not {len(by_player)} cheaters.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
