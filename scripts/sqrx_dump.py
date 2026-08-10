#!/usr/bin/env python3
"""Standalone .sqrx reader — turns a match recording into a flat 3-D stream.

Written to be handed to someone who has a .sqrx file and no sqreader checkout.
It imports nothing from sqreader; the only dependency is zstandard:

    pip install zstandard
    python sqrx_dump.py match.sqrx --out frames.ndjson
    python sqrx_dump.py match.sqrx --out scene.json --format json --anonymize

WHY THIS EXISTS
---------------
A recording interleaves two kinds of line at two different rates:

  * ~1 Hz  FULL frames  — everything: names, teams, roles, scores, vehicles,
                          damage events, game state. Heavy.
  * 4 Hz   POSITION frames (`"t":"pos"`) — just id/x/y/z/yaw/health. Cheap.

Positions are the ones you want to animate, but they carry **no identity** —
no name, no team. You have to carry the last full frame's identity forward.
This script does that join for you and emits one flat, evenly-spaced stream.

COORDINATES — read this before you render anything
--------------------------------------------------
* Units are **centimetres**. A 4 km map spans about ±200000. Divide by 100
  for metres.
* Squad's world **Y increases southward**, and Unreal is **Z-up, left-handed**.
  three.js is **Y-up, right-handed**. The mapping that comes out upright and
  un-mirrored is:

      three.position.set(world.x / 100, world.z / 100, -world.y / 100)
                          ^ east         ^ height      ^ negate: Y is south

  Skip the negation and your scene renders mirrored — and it looks plausible
  enough that people lose days to it.
* `yaw` is **degrees**. three.js wants radians about the up axis:

      mesh.rotation.y = -yaw * Math.PI / 180

  (negated for the same handedness reason as above.)
* `z` may be `null` on very old recordings that predate height capture — treat
  a missing height as "put it on the ground", don't drop the entity.

OUTPUT
------
`--format ndjson` (default) — one JSON object per line, streamable:

    {"t": 12.5, "players": [{"id","name","team","x","y","z","yaw","h"}, ...],
                "vehicles": [{"id","name","team","x","y","z","yaw","h"}, ...]}

`--format json` — a single object: {"meta": {...}, "frames": [...]}, easier to
`fetch()` in a browser but loads entirely into memory.

`t` is seconds from the first frame, so you can drive an animation clock
directly without worrying about wall-clock timestamps.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

try:
    import zstandard as zstd
except ImportError:  # pragma: no cover - operator-facing message
    sys.exit("needs zstandard:  pip install zstandard")

MAGIC = b"SQRX"
HEADER_FIXED = 18  # 4 magic + 2 version + 2 reserved + 8 created_ms + 2 id_len


# ----------------------------------------------------------------- container


def read_lines(path: Path) -> tuple[dict[str, Any], Iterator[str]]:
    """Parse the .sqrx header, then iterate the NDJSON lines inside.

    Layout: magic | u16 version | u16 reserved | u64 created_ms |
            u16 server_id_len | server_id | N independent zstd frames,
            one NDJSON line each.
    """
    fh = path.open("rb")
    head = fh.read(HEADER_FIXED)
    if len(head) < HEADER_FIXED or head[:4] != MAGIC:
        sys.exit(f"{path}: not a .sqrx file")
    version, _res, created_ms, sid_len = struct.unpack("<HHQH", head[4:])
    server_id = fh.read(sid_len).decode("utf-8", "replace")
    meta = {"formatVersion": version, "createdMs": created_ms,
            "serverId": server_id}

    def lines() -> Iterator[str]:
        # read_across_frames walks the independent per-tick frames in one pass.
        with fh:
            reader = zstd.ZstdDecompressor().stream_reader(
                fh, read_across_frames=True)
            buf = b""
            while True:
                chunk = reader.read(1 << 20)
                if not chunk:
                    break
                buf += chunk
                *ready, buf = buf.split(b"\n")
                for ln in ready:
                    if ln.strip():
                        yield ln.decode("utf-8", "replace")
            if buf.strip():
                yield buf.decode("utf-8", "replace")

    return meta, lines()


# -------------------------------------------------------------------- names


def anon(key: str) -> str:
    """Stable pseudonym — same player keeps one label across the match."""
    return "P" + hashlib.sha1(key.encode()).hexdigest()[:6].upper()


# --------------------------------------------------------------------- join


def convert(path: Path, *, anonymize: bool) -> tuple[dict[str, Any],
                                                     list[dict[str, Any]]]:
    meta, lines = read_lines(path)
    # Identity carried forward from the most recent full frame.
    pid_info: dict[str, dict[str, Any]] = {}
    vid_info: dict[str, dict[str, Any]] = {}
    frames: list[dict[str, Any]] = []
    full_frames: list[dict[str, Any]] = []   # fallback, see below
    t0: float | None = None
    layer: Any = None
    skipped_origin = [0]

    def stamp(d: dict[str, Any]) -> float | None:
        """Epoch seconds, or None when the frame carries no usable time.

        Recordings stamp frames with an ISO-8601 string
        (``2026-07-23T22:18:15.058146+00:00``), not a number — treating it as
        numeric silently collapses every frame to t=0 and the animation never
        advances.
        """
        ts = d.get("timestamp")
        if isinstance(ts, (int, float)):
            return float(ts)
        if isinstance(ts, str):
            try:
                return datetime.fromisoformat(ts).timestamp()
            except ValueError:
                return None
        return None

    for raw in lines:
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue                      # tolerate a torn final frame

        if d.get("t") != "pos":
            # FULL frame: refresh identity, remember the layer, and emit it
            # too (it has positions of its own).
            gs = d.get("gameState") or {}
            layer = layer or gs.get("layer") or gs.get("mapName")
            for p in d.get("players") or []:
                key = str(p.get("eosId") or p.get("id") or p.get("name") or "")
                if not key:
                    continue
                pid_info[key] = {
                    "name": anon(key) if anonymize else p.get("name"),
                    "team": p.get("teamId"),
                }
            for v in d.get("vehicles") or []:
                vid = str(v.get("id") or "")
                if vid:
                    vid_info[vid] = {"name": v.get("classShort"),
                                     "team": v.get("team")}
            # Keep the full frame's own positions as a fallback: recordings
            # made before the 4 Hz sampler existed contain no "pos" lines at
            # all, and emitting an empty file for them looks like a bug.
            # ~1 Hz nominal when the frame carries no parseable stamp, so the
            # fallback clock still advances rather than blowing up on None.
            ts_f = stamp(d)
            if ts_f is None:
                ts_f = float(len(full_frames))
            fp = []
            for p in d.get("players") or []:
                sol = p.get("soldier") or {}
                pos = sol.get("position") or {}
                if pos.get("x") is None:
                    continue
                key = str(p.get("eosId") or p.get("id") or p.get("name") or "")
                fp.append({"id": anon(key) if anonymize else key,
                           "name": anon(key) if anonymize else p.get("name"),
                           "team": p.get("teamId"),
                           "x": pos.get("x"), "y": pos.get("y"),
                           "z": pos.get("z"), "yaw": sol.get("yaw"),
                           "h": sol.get("health")})
            fv = []
            for v in d.get("vehicles") or []:
                pos = v.get("position") or {}
                if pos.get("x") is None:
                    continue
                fv.append({"id": str(v.get("id") or ""),
                           "name": v.get("classShort"), "team": v.get("team"),
                           "x": pos.get("x"), "y": pos.get("y"),
                           "z": pos.get("z"), "yaw": v.get("yaw"),
                           "h": v.get("health")})
            if fp or fv:
                full_frames.append({"t": ts_f, "players": fp, "vehicles": fv})
            continue

        # POSITION frame — the 4 Hz stream we actually animate.
        # Nominal 4 Hz index is the fallback when a frame has no usable stamp,
        # so the clock still advances instead of freezing at zero.
        ts = stamp(d)
        if ts is None:
            ts = (t0 or 0.0) + len(frames) / 4.0
        if t0 is None:
            t0 = ts
        out_p = []
        for p in d.get("players") or []:
            key = str(p.get("id") or "")
            info = pid_info.get(key, {})
            out_p.append({"id": anon(key) if anonymize else key,
                          "name": info.get("name"), "team": info.get("team"),
                          "x": p.get("x"), "y": p.get("y"), "z": p.get("z"),
                          "yaw": p.get("yaw"), "h": p.get("h")})
        out_v = []
        for v in d.get("vehicles") or []:
            vid = str(v.get("id") or "")
            info = vid_info.get(vid, {})
            # Unplaced emplacements read as exactly (0, 0). Rendering them
            # stacks a pile of objects at the world origin, which reads as a
            # bug in the viewer; they are counted in meta rather than dropped
            # silently.
            if v.get("x") == 0 and v.get("y") == 0:
                skipped_origin[0] += 1
                continue
            out_v.append({"id": vid, "name": info.get("name"),
                          "team": v.get("team", info.get("team")),
                          "x": v.get("x"), "y": v.get("y"), "z": v.get("z"),
                          "yaw": v.get("yaw"), "h": v.get("h")})
        frames.append({"t": round(ts - (t0 or 0), 3),
                       "players": out_p, "vehicles": out_v})

    rate = "4hz-position"
    if not frames:
        # Pre-4 Hz recording: fall back to the ~1 Hz full frames so the file
        # still animates, just coarser.
        rate = "1hz-full-fallback"
        base = full_frames[0]["t"] if full_frames else 0
        frames = [{**f, "t": round(f["t"] - base, 3)} for f in full_frames]

    lay = layer if isinstance(layer, dict) else {}
    meta.update({
        "layer": lay.get("name") or layer,
        "map": lay.get("mapName"), "gameMode": lay.get("gameMode"),
        # World extent of this layer — use it to size/centre your terrain.
        "worldBounds": ({"topLeft": lay["topLeft"],
                         "bottomRight": lay["bottomRight"]}
                        if lay.get("topLeft") and lay.get("bottomRight")
                        else None),
        "frames": len(frames),
        "sampling": rate,
        "skippedAtOrigin": skipped_origin[0],
        "durationSec": round(frames[-1]["t"], 1) if frames else 0,
        "units": "centimetres",
        "axes": "Unreal Z-up left-handed; world Y increases southward",
        "threeHint": "three.set(x/100, z/100, -y/100); rotation.y = -yaw*PI/180",
    })
    return meta, frames


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sqrx", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--format", choices=("ndjson", "json"), default="ndjson")
    ap.add_argument("--anonymize", action="store_true",
                    help="replace names/ids with stable pseudonyms")
    a = ap.parse_args()

    meta, frames = convert(a.sqrx, anonymize=a.anonymize)
    with a.out.open("w", encoding="utf-8") as fh:
        if a.format == "json":
            json.dump({"meta": meta, "frames": frames}, fh)
        else:
            fh.write(json.dumps({"meta": meta}) + "\n")
            for f in frames:
                fh.write(json.dumps(f) + "\n")

    print(f"{a.sqrx.name}: {meta['frames']} frames ({meta['sampling']}), "
          f"{meta['durationSec']}s, layer={meta['layer']} -> {a.out}")
    if meta["sampling"].startswith("1hz"):
        print("  note: no 4 Hz position frames in this recording (made before "
              "the two-tier sampler) — using the ~1 Hz full frames instead.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
