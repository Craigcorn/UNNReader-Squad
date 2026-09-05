"""Soldier inventory pointer + full-kit walk timing (test T1).

The seat-inventory walk (W12) reads SQPawnInventoryComponent.Inventory; a
soldier is an SQPawn, so the same walk should read a player's whole kit —
IF the soldier exposes its inventory component through a reflectable
field. This probe answers that, the no-guess way: it never matches field
names, it dereferences every ObjectProperty on a live pawn and keeps the
ones whose pointed-to object's class chain actually reaches
SQPawnInventoryComponent. Then it runs the real `read_inventory_weapons`
walk on what it found and times it, so the C4 cost estimate gets a
measured number.

One-shot and read-only: scan, report, exit. No live pawn of the target
class is a SKIP (exit 0 with a message), not a failure. Output: stdout
summary plus JSONL to /tmp/soldier_inventory.jsonl.

Run on the box while at least one player is alive:
  `.venv/bin/python scripts/probes/soldier_inventory.py`
Smoke-test against seat pawns (exist at 0 players whenever vehicles do):
  `.venv/bin/python scripts/probes/soldier_inventory.py --cls SQVehicleSeat`
"""
from __future__ import annotations

import argparse
import json
import statistics
import struct
import time

from probe_common import attach_with_paths

from sqreader.squad.snapshot import (SQ_INV_CURRENT_WEAPON_OFFSET,
                                     read_inventory_weapons)
from sqreader.ue.reflection import get_class_layout, walk_super_chain
from sqreader.ue.uobject import UOBJ_CLASS_PRIVATE, UOBJ_NAME_PRIVATE

INV_COMPONENT_BASE = "SQPawnInventoryComponent"
OUT = "/tmp/soldier_inventory.jsonl"

ap = argparse.ArgumentParser()
ap.add_argument("--cls", default="SQSoldier",
                help="pawn class to probe (SQVehicleSeat smoke-tests the "
                     "discovery against seat pawns at 0 players)")
ap.add_argument("--max-pawns", type=int, default=3)
ap.add_argument("--iters", type=int, default=50,
                help="timing iterations of the full walk")
args = ap.parse_args()

a, paths = attach_with_paths()
pm, arr, alloc = a.pm, a.arr, a.alloc
out = open(OUT, "a", buffering=1)


def log(**kw):
    kw.setdefault("t", round(time.time(), 3))
    out.write(json.dumps(kw) + "\n")


def u64(addr):
    b = pm.try_read(addr, 8)
    return struct.unpack("<Q", b)[0] if b and len(b) == 8 else 0


def uname(addr):
    try:
        b = pm.try_read(addr + UOBJ_NAME_PRIVATE, 8)
        return alloc.fname_to_str(*struct.unpack("<II", b)) if b else None
    except Exception:  # noqa: BLE001
        return None


def cls_of(addr):
    b = pm.try_read(addr + UOBJ_CLASS_PRIVATE, 8)
    return struct.unpack("<Q", b)[0] if b and len(b) == 8 else 0


found = arr.find_all_by_names(
    {args.cls: "Class", INV_COMPONENT_BASE: "Class"}, alloc=alloc)
target_cls = found.get(args.cls, (0, 0))[1]
inv_base_cls = found.get(INV_COMPONENT_BASE, (0, 0))[1]
if not target_cls or not inv_base_cls:
    raise SystemExit(f"FAIL classes did not resolve: {args.cls}="
                     f"{target_cls:#x} {INV_COMPONENT_BASE}={inv_base_cls:#x}")

# --- find live pawns of the target class ---------------------------------
_is_target: dict[int, bool] = {}
pawns: list[int] = []
for _i, oa in arr.iter_object_addrs():
    if not oa:
        continue
    ca = cls_of(oa)
    if not ca:
        continue
    hit = _is_target.get(ca)
    if hit is None:
        hit = target_cls in walk_super_chain(pm, ca)
        _is_target[ca] = hit
    if not hit:
        continue
    nm = uname(oa) or ""
    if nm.startswith("Default__"):
        continue
    pawns.append(oa)
    if len(pawns) >= args.max_pawns:
        break

if not pawns:
    print(f"SKIP nothing to measure — no live {args.cls} pawn "
          f"(run while a player is alive)")
    log(event="skip", cls=args.cls)
    raise SystemExit(0)

# --- the question: which ObjectProperty actually points at the inventory --
candidates: dict[str, dict] = {}
for oa in pawns:
    lay = get_class_layout(pm, cls_of(oa), alloc)
    for fname, p in lay.items():
        if p.type_name != "ObjectProperty":
            continue
        ptr = u64(oa + p.offset)
        if not ptr:
            continue
        pca = cls_of(ptr)
        if not pca or inv_base_cls not in walk_super_chain(pm, pca):
            continue
        c = candidates.setdefault(fname, {
            "field": fname, "offset": p.offset,
            "component_class": uname(pca), "hits": 0, "sample": ptr})
        c["hits"] += 1

print(f"pawns sampled: {len(pawns)} ({args.cls})")
if not candidates:
    print(f"RESULT no ObjectProperty on {args.cls} points at an "
          f"{INV_COMPONENT_BASE}-derived component — the pointer is not "
          f"reflectable on this class; C4 would need another route")
    log(event="no-candidate", cls=args.cls, pawns=len(pawns))
    raise SystemExit(0)

for c in sorted(candidates.values(), key=lambda c: -c["hits"]):
    print(f"RESULT {args.cls}.{c['field']} @ {c['offset']:#x} -> "
          f"{c['component_class']}  ({c['hits']}/{len(pawns)} pawns)")
    log(event="candidate", cls=args.cls, field=c["field"],
        offset=f"{c['offset']:#x}", component=c["component_class"],
        hits=c["hits"], pawns=len(pawns))

# --- run and time the real walk on the best candidate ---------------------
best = max(candidates.values(), key=lambda c: c["hits"])
inv = best["sample"]
cur = u64(inv + SQ_INV_CURRENT_WEAPON_OFFSET)
weapons = read_inventory_weapons(pm, alloc, inv, paths, cur)
print(f"walk: {len(weapons)} weapon record(s) via {best['field']}")
for w in weapons:
    print(f"  group {w.get('group')}: {w.get('weaponClass')}"
          f"{'  <- active' if w.get('active') else ''}"
          f"  mags={w.get('magazines')}")
log(event="walk", field=best["field"], records=weapons)

times = []
for _ in range(max(1, args.iters)):
    t0 = time.perf_counter()
    read_inventory_weapons(pm, alloc, inv, paths, cur)
    times.append((time.perf_counter() - t0) * 1000)
med = statistics.median(times)
print(f"timing: median {med:.2f} ms, min {min(times):.2f}, "
      f"max {max(times):.2f} over {len(times)} warm walks of one kit")
log(event="timing", iters=len(times), median_ms=round(med, 3),
    min_ms=round(min(times), 3), max_ms=round(max(times), 3))
print("done (read-only probe, nothing written)")
