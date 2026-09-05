"""What does a command marker's `Action` class pointer hold? (test T12)

Every command marker class — the squad leader's request in both its
pending and approved forms, and each asset footprint — carries an `Action`
class pointer (reflected 2026-09-05; it is on the master class every one
of them derives from). Nobody has read its value. This probe follows every
live actor whose class derives from `BP_MapMarker_CommandMaster_C` at 1 Hz
and writes a row whenever a marker appears, changes its `Action`, `Request`,
`Distance` or `AddDistance`, or disappears, so the pointer's behaviour can
be read across a request's life (placed, approved, consumed by a call) and
on each footprint marker while a call is live.

Solo half: one squad leader placing and approving a request answers what
the pointer holds before and after approval. Call half: any session with a
commander calling assets answers what the footprint markers hold; that
half rides the acceptance run (T8).

Read-only; never dies on a bad read; appends JSONL to
/tmp/marker_action.jsonl. Run on the box after the layer is loaded:
  `.venv/bin/python scripts/probes/marker_action.py`
"""
import json
import struct
import time

from probe_common import attach

from sqreader.ue.reflection import bool_property_mask, get_class_layout, walk_super_chain
from sqreader.ue.uobject import UOBJ_CLASS_PRIVATE, UOBJ_NAME_PRIVATE

MASTER = "BP_MapMarker_CommandMaster_C"
FIELDS = ("Action", "Request", "Distance", "AddDistance")
SCAN = 1.0
HEARTBEAT = 30.0
OUT = "/tmp/marker_action.jsonl"

_a = attach()
pm, arr, alloc = _a.pm, _a.arr, _a.alloc
out = open(OUT, "a", buffering=1)


def log(**kw):
    kw.setdefault("t", round(time.time(), 3))
    out.write(json.dumps(kw) + "\n")


def uname(a):
    try:
        b = pm.try_read(a + UOBJ_NAME_PRIVATE, 8)
        return alloc.fname_to_str(*struct.unpack("<II", b)) if b else None
    except Exception:  # noqa: BLE001
        return None


def cls_of(a):
    b = pm.try_read(a + UOBJ_CLASS_PRIVATE, 8)
    return struct.unpack("<Q", b)[0] if b and len(b) == 8 else 0


def u64(a):
    b = pm.try_read(a, 8)
    return struct.unpack("<Q", b)[0] if b and len(b) == 8 else None


def f64(a):
    b = pm.try_read(a, 8)
    return round(struct.unpack("<d", b)[0], 2) if b and len(b) == 8 else None


found = arr.find_all_by_names({MASTER: "BlueprintGeneratedClass"}, alloc=alloc)
if MASTER not in found:
    raise SystemExit(f"{MASTER} is not loaded on this layer; nothing to watch")
MASTER_CLS = found[MASTER][1]
layout = get_class_layout(pm, MASTER_CLS, alloc)
OFF = {n: layout[n].offset for n in FIELDS}
REQ = bool_property_mask(pm, MASTER_CLS, "Request", alloc)  # (offset, mask)
log(event="start", pid=_a.pid, master=f"{MASTER_CLS:#x}",
    offsets={n: f"{o:#x}" for n, o in OFF.items()})

_is_marker: dict = {}   # class addr -> bool (derives from the master)
_cname: dict = {}       # class addr -> name


def is_marker(ca):
    if ca not in _is_marker:
        try:
            _is_marker[ca] = MASTER_CLS in walk_super_chain(pm, ca)
        except Exception:  # noqa: BLE001
            _is_marker[ca] = False
    return _is_marker[ca]


def cname(ca):
    if ca not in _cname:
        _cname[ca] = uname(ca)
    return _cname[ca]


def read_marker(a):
    act = u64(a + OFF["Action"])
    req_b = pm.try_read(a + REQ[0], 1) if REQ else None
    return {
        "action": cname(act) if act else None,
        "request": bool(req_b[0] & REQ[1]) if req_b else None,
        "distance": f64(a + OFF["Distance"]),
        "addDistance": f64(a + OFF["AddDistance"]),
    }


last: dict = {}
last_beat = 0.0
while True:
    t0 = time.time()
    seen = set()
    for _idx, oa in arr.iter_object_addrs():
        if not oa:
            continue
        ca = cls_of(oa)
        if not ca or not is_marker(ca):
            continue
        seen.add(oa)
        try:
            row = read_marker(oa)
        except Exception as e:  # noqa: BLE001
            log(event="read-error", addr=f"{oa:#x}", err=repr(e))
            continue
        row["cls"] = cname(ca)
        key = f"{oa:#x}"
        if key not in last:
            log(event="new", addr=key, **row)
        elif last[key] != row:
            log(event="change", addr=key, was=last[key], **row)
        last[key] = row
    for key in list(last):
        if int(key, 16) not in seen:
            log(event="gone", addr=key, last=last.pop(key))
    if t0 - last_beat > HEARTBEAT:
        log(event="heartbeat", markers=len(last))
        last_beat = t0
    time.sleep(max(0.0, SCAN - (time.time() - t0)))
