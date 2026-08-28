#!/usr/bin/env python3
"""Find the true firer offset on in-flight projectiles, by correlation.

`projectiles[].firer` reads a hardcoded `InstigatorController` offset
(PROJECTILE_INSTIGATOR_CONTROLLER_OFFSET, 0x0580) that returns null for every
projectile on Squad 10.5.x — 95 494 of 95 494 observations in the reference
corpus. The field was never cleanly reflectable, so the fix is the same
technique that found the deployable placer: while a KNOWN player fires rounds,
scan each in-flight projectile's memory for pointer-sized values that land on
that player's controller / player state / pawn, and let the offsets that hit
consistently name themselves.

Read-only, like everything here. Run it on the game host while somebody
fires — mortars are ideal (long flight time), grenade launchers and rockets
also work:

    .venv/bin/python scripts/probe_projectile_firer.py --seconds 90

Output: a table of (offset, target-kind, hits, projectile classes seen), the
current content of the legacy 0x0580 slot for comparison, and the candidate
verdict. Nothing is written anywhere.
"""
from __future__ import annotations

import argparse
import struct
import sys
import time
from collections import Counter, defaultdict

from sqreader import addrcache
from sqreader.cli import _resolve_fname_pool, _resolve_gobjects
from sqreader.config import find_squad_server_pid
from sqreader.mem import ProcessMemory
from sqreader.ue.uobject import UOBJ_CLASS_PRIVATE, UOBJ_NAME_PRIVATE

LEGACY_OFF = 0x0580          # PROJECTILE_INSTIGATOR_CONTROLLER_OFFSET today
SPAN = 0x0A80                # how deep into each projectile object we scan
WORD = 8

# Class-name buckets for the correlation targets. Substring matches are fine
# HERE because this is a diagnostic run a human reads, not a detector.
KINDS = (
    ("PC",   lambda n: n == "SQPlayerController"),
    ("PS",   lambda n: n == "SQPlayerState"),
    ("PAWN", lambda n: "Soldier" in n),
)


def _class_name(pm, alloc, cls_cache: dict, obj_addr: int) -> str | None:
    raw = pm.try_read(obj_addr + UOBJ_CLASS_PRIVATE, WORD)
    if not raw or len(raw) != WORD:
        return None
    cls = struct.unpack("<Q", raw)[0]
    if not cls:
        return None
    name = cls_cache.get(cls)
    if name is None:
        nm = pm.try_read(cls + UOBJ_NAME_PRIVATE, WORD)
        if not nm or len(nm) != WORD:
            return None
        try:
            name = alloc.fname_to_str(*struct.unpack("<II", nm))
        except Exception:
            return None
        cls_cache[cls] = name
    return name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, default=None)
    ap.add_argument("--seconds", type=float, default=90.0)
    ap.add_argument("--interval", type=float, default=0.4,
                    help="pause between walks; a mortar flies ~15 s")
    args = ap.parse_args()

    pid = args.pid or find_squad_server_pid()
    pm = ProcessMemory(pid)
    binary_id = addrcache.binary_identity(pid)
    arr = _resolve_gobjects(pm, binary_id)
    alloc = _resolve_fname_pool(pm, binary_id)

    cls_cache: dict[int, str] = {}
    # (offset, kind) -> hit count, and offset -> projectile classes it hit on.
    hits: Counter = Counter()
    off_classes: dict[tuple[int, str], set] = defaultdict(set)
    legacy_content: Counter = Counter()   # what lives at 0x0580 right now
    seen_proj: set[tuple[int, str]] = set()   # (addr, class) pairs sampled
    walks = 0

    deadline = time.monotonic() + args.seconds
    print(f"probing pid {pid} for {args.seconds:.0f}s — fire away "
          "(mortar > GL > rocket for flight time)...", file=sys.stderr)
    while time.monotonic() < deadline:
        targets: dict[str, set] = {k: set() for k, _ in KINDS}
        projectiles: list[tuple[int, str]] = []
        for _idx, obj in arr.iter_object_addrs():
            if not obj:
                continue
            name = _class_name(pm, alloc, cls_cache, obj)
            if not name:
                continue
            for kind, match in KINDS:
                if match(name):
                    targets[kind].add(obj)
                    break
            else:
                if "Proj" in name:
                    projectiles.append((obj, name))

        for addr, pname in projectiles:
            raw = pm.try_read(addr, SPAN)
            if not raw:
                continue
            seen_proj.add((addr, pname))
            words = struct.unpack(f"<{len(raw) // WORD}Q",
                                  raw[: len(raw) // WORD * WORD])
            for i, w in enumerate(words):
                if not w:
                    continue
                off = i * WORD
                for kind, _ in KINDS:
                    if w in targets[kind]:
                        hits[(off, kind)] += 1
                        off_classes[(off, kind)].add(pname)
            legacy = words[LEGACY_OFF // WORD] if LEGACY_OFF // WORD < len(words) else 0
            if not legacy:
                legacy_content["null"] += 1
            else:
                for kind, _ in KINDS:
                    if legacy in targets[kind]:
                        legacy_content[kind] += 1
                        break
                else:
                    legacy_content["other-ptr"] += 1

        walks += 1
        time.sleep(args.interval)

    print(f"\nwalks={walks}  projectile samples={len(seen_proj)}  "
          f"distinct classes={len({c for _, c in seen_proj})}")
    if not seen_proj:
        print("no projectiles observed — nobody fired, or nothing tracked. "
              "Fire a mortar and rerun.")
        return 1

    print(f"\nlegacy 0x{LEGACY_OFF:04x} content across samples: "
          f"{dict(legacy_content)}")
    print("\ncandidate offsets (hits across all samples):")
    print(f"  {'offset':>8}  {'kind':<5} {'hits':>5}  classes")
    for (off, kind), n in sorted(hits.items(), key=lambda kv: -kv[1])[:20]:
        cls = ", ".join(sorted(off_classes[(off, kind)])[:4])
        print(f"  {off:#08x}  {kind:<5} {n:>5}  {cls}")
    if not hits:
        print("  (none — projectiles seen, but no pointer into PC/PS/PAWN "
              "sets within the scanned span)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
