"""Drone tracker (test T7 — the drone bundle; any match with a drone up).

10 Hz: world position + yaw and every reflected field of interest on each
live pawn whose class derives from SQFlyingDrone (commander drone, recon
drone, future variants), the recon launcher deployable
(BP_Deployable_DroneSpawner_C) and the commander drone's call actor
(BP_CommandActor_Drone_C). Rows are written only when something changed
(plus a 5 s heartbeat), so a whole flight is small.

Every offset comes from reflection at first sight of each class; bools via
their property masks; pointers to PlayerState / Controller are resolved to
player names, class pointers to class names. The pawn's HealthComponent is
followed: its class is reflected once and its numeric fields dumped on
change, so the health field identifies itself. A 1 Hz roll-call of soldier
positions rides along so pick-up / redeploy distances can be read.
Appends JSONL to /tmp/drone_track.jsonl. Never crashes on a bad read.

Canonical home: scripts/probes/ (authored for session B, relocated here
2026-09-04 under the per-test harness convention — see README). Run on the
box: `.venv/bin/python scripts/probes/drone_track.py`.
"""
import json
import math
import struct
import time

from probe_common import attach

from sqreader.health import SCENE_COMPONENT_TO_WORLD_TRANSLATION_OFF as C2W_T
from sqreader.squad.snapshot import read_fstring
from sqreader.ue.reflection import (bool_property_mask, get_class_layout,
                                    walk_super_chain)
from sqreader.ue.uobject import UOBJ_CLASS_PRIVATE, UOBJ_NAME_PRIVATE

C2W_Q = C2W_T - 0x20

FAST = 0.1
SCAN = 1.0
HEARTBEAT = 5.0
OUT = "/tmp/drone_track.jsonl"
DRONE_BASE = "SQFlyingDrone"
EXTRA_CLASSES = ("BP_Deployable_DroneSpawner_C", "BP_CommandActor_Drone_C")
FIELDS = {
    "drone": ["PlayerState", "Controller", "PreviousController", "LastHitBy",
              "SQ PC", "HealthComponent", "Command Action", "BleedOutTime",
              "EndFlightTimer", "BatteryLifetimeMax", "Max Fly Height",
              "CrashVelocity",
              "Zoom Level", "Desired Zoom", "FPV Item", "Dead", "Can Possess",
              "Can Increase Altitude"],
    "BP_Deployable_DroneSpawner_C": ["Health", "MaxHealth", "Team", "BuildState",
                                     "InstigatingPlayerState",
                                     "InstigatingPlayerController", "Drone Class",
                                     "Action", "bPlaced", "WasEverBuilt"],
    "BP_CommandActor_Drone_C": ["Health", "SQ PC", "Team",
                                "DamageInstigatorController", "Action",
                                "Action Destroyed"],
}

_a = attach()
pid, pm, arr, alloc = _a.pid, _a.pm, _a.arr, _a.alloc
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


found = arr.find_all_by_names({"Actor": "Class", DRONE_BASE: "Class",
                               "SQSoldier": "Class", "Pawn": "Class",
                               "PlayerState": "Class", "Controller": "Class"},
                              alloc=alloc)
ROOT_OFF = get_class_layout(pm, found["Actor"][1], alloc)["RootComponent"].offset
DRONE_CLS = found.get(DRONE_BASE, (0, 0))[1]
SOL_CLS = found.get("SQSoldier", (0, 0))[1]
PS_NAME_OFF = get_class_layout(pm, found["PlayerState"][1], alloc)["PlayerNamePrivate"].offset
PAWN_PS_OFF = get_class_layout(pm, found["Pawn"][1], alloc)["PlayerState"].offset
CTRL_PS_OFF = get_class_layout(pm, found["Controller"][1], alloc)["PlayerState"].offset
log(event="start", pid=pid, drone_base=f"{DRONE_CLS:#x}", root_off=ROOT_OFF)


def ps_name(ps):
    if not ps:
        return None
    try:
        return read_fstring(pm, ps + PS_NAME_OFF)
    except Exception:  # noqa: BLE001
        return None


def ctrl_name(ctrl):
    ps = u64(ctrl + CTRL_PS_OFF) if ctrl else None
    return ps_name(ps) if ps else None


def read_pose(oa):
    root = u64(oa + ROOT_OFF)
    if not root or root > 0x00007fffffffffff:
        return None
    raw = pm.try_read(root + C2W_Q, 0x38)
    if not raw or len(raw) != 0x38:
        return None
    q = struct.unpack("<4d", raw[:32])
    tr = struct.unpack("<3d", raw[32:56])
    if any(abs(v) > 1e8 for v in tr):
        return None
    x, y, z, w = q
    yaw = math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))
    return [round(v, 1) for v in tr], round(yaw, 1)


# --- class classification ------------------------------------------------------
_cname: dict = {}
_ckind: dict = {}
_cfields: dict = {}     # class addr -> {label: (prop, mask or None)}
_comp_layout: dict = {} # component class addr -> {name: prop} numeric fields


def kind(ca):
    if ca in _ckind:
        return _ckind[ca]
    n = uname(ca) or ""
    _cname[ca] = n
    k = None
    if n.startswith(("UMG_", "UI_", "W_")):
        k = None
    elif DRONE_CLS and DRONE_CLS in walk_super_chain(pm, ca):
        k = "drone"
    elif n in EXTRA_CLASSES:
        k = n
    elif SOL_CLS and SOL_CLS in walk_super_chain(pm, ca):
        k = "soldier"
    _ckind[ca] = k
    if k and k != "soldier":
        lay = get_class_layout(pm, ca, alloc)
        fl = {}
        for name in FIELDS[k]:
            p = lay.get(name)
            if p is None:
                continue
            mask = (bool_property_mask(pm, ca, name, alloc)
                    if p.type_name == "BoolProperty" else None)
            fl[name] = (p, mask)
        _cfields[ca] = fl
        log(event="class", kind=k, cls=n, addr=f"{ca:#x}",
            fields={nm: (p.type_name, f"{p.offset:#x}") for nm, (p, _m) in fl.items()},
            missing=[nm for nm in FIELDS[k] if nm not in fl])
    return k


def read_field(oa, name, p, mask):
    t = p.type_name
    try:
        if t == "BoolProperty":
            if mask is None:
                return None
            off, m = mask
            b = pm.try_read(oa + off, 1)
            return bool(b[0] & m) if b else None
        if t == "FloatProperty":
            b = pm.try_read(oa + p.offset, 4)
            return round(struct.unpack("<f", b)[0], 2) if b else None
        if t == "DoubleProperty":
            b = pm.try_read(oa + p.offset, 8)
            return round(struct.unpack("<d", b)[0], 2) if b else None
        if t == "IntProperty":
            b = pm.try_read(oa + p.offset, 4)
            return struct.unpack("<i", b)[0] if b else None
        if t in ("ByteProperty", "EnumProperty"):
            b = pm.try_read(oa + p.offset, 1)
            return b[0] if b else None
        if t in ("ObjectProperty", "ClassProperty"):
            ptr = u64(oa + p.offset)
            if not ptr:
                return None
            if t == "ClassProperty":
                return uname(ptr)
            if name in ("PlayerState", "InstigatingPlayerState"):
                return {"addr": f"{ptr:#x}", "name": ps_name(ptr)}
            if name in ("Controller", "PreviousController", "LastHitBy", "SQ PC",
                        "InstigatingPlayerController"):
                return {"addr": f"{ptr:#x}", "name": ctrl_name(ptr)}
            return {"addr": f"{ptr:#x}", "cls": uname(cls_of(ptr)) if cls_of(ptr) else None}
        if t == "WeakObjectProperty":
            b = pm.try_read(oa + p.offset, 8)
            return b.hex() if b else None
        if t == "StructProperty":
            b = pm.try_read(oa + p.offset, min(p.element_size or 8, 32) or 8)
            return b.hex() if b else None
    except Exception:  # noqa: BLE001
        return None
    return None


def health_component(ptr):
    """Numeric fields of the pawn's HealthComponent, reflected once per class."""
    if not ptr:
        return None
    ca = cls_of(ptr)
    if not ca:
        return None
    if ca not in _comp_layout:
        lay = get_class_layout(pm, ca, alloc)
        num = {n: p for n, p in lay.items()
               if p.type_name in ("FloatProperty", "DoubleProperty", "IntProperty", "BoolProperty")
               and p.offset >= 0x90}
        _comp_layout[ca] = num
        by_off = sorted(num.items(), key=lambda kv: kv[1].offset)
        log(event="component-class", cls=uname(ca), addr=f"{ca:#x}",
            fields={n: (p.type_name, f"{p.offset:#x}") for n, p in by_off})
    vals = {}
    for n, p in _comp_layout[ca].items():
        mask = bool_property_mask(pm, ca, n, alloc) if p.type_name == "BoolProperty" else None
        v = read_field(ptr, n, p, mask)
        if v is not None:
            vals[n] = v
    return vals


def scan():
    tracked, soldiers = {}, {}
    for _i, oa in arr.iter_object_addrs():
        if not oa:
            continue
        ca = cls_of(oa)
        if not ca:
            continue
        k = kind(ca)
        if k is None:
            continue
        nm = uname(oa) or ""
        if nm.startswith("Default__"):
            continue
        if k == "soldier":
            soldiers[oa] = nm
        else:
            tracked[oa] = (ca, k, nm)
    return tracked, soldiers


tracked: dict = {}
soldiers: dict = {}
state: dict = {}
next_scan = 0.0
while True:
    now = time.time()
    try:
        if now >= next_scan:
            next_scan = now + SCAN
            new_tracked, new_soldiers = scan()
            for oa, (ca, k, nm) in new_tracked.items():
                if oa not in tracked:
                    log(event="new", kind=k, cls=_cname.get(ca, "?"), name=nm, addr=f"{oa:#x}")
            for oa, (ca, k, nm) in tracked.items():
                if oa not in new_tracked:
                    st = state.pop(oa, None) or {}
                    log(event="gone", kind=k, cls=_cname.get(ca, "?"), name=nm,
                        addr=f"{oa:#x}", last=st.get("row"))
            tracked, soldiers = new_tracked, new_soldiers
            roll = []
            for oa, _nm in soldiers.items():
                pose = read_pose(oa)
                ps = u64(oa + PAWN_PS_OFF)
                roll.append({"who": ps_name(ps) if ps else None,
                             "pos": pose[0] if pose else None})
            log(event="pawns", n=len(roll), pawns=roll)
        t = round(now, 3)
        for oa, (ca, k, _nm) in tracked.items():
            row = {}
            pose = read_pose(oa)
            if pose:
                row["pos"], row["yaw"] = pose
            for name, (p, mask) in _cfields.get(ca, {}).items():
                v = read_field(oa, name, p, mask)
                if v is not None:
                    row[name] = v
            if k == "drone" and "HealthComponent" in _cfields.get(ca, {}):
                hp_ptr = u64(oa + _cfields[ca]["HealthComponent"][0].offset)
                hc = health_component(hp_ptr)
                if hc:
                    row["health"] = hc
            st = state.setdefault(oa, {"row": None, "hb": 0.0})
            prev = st["row"]
            changed = prev is None or now - st["hb"] >= HEARTBEAT
            if not changed and prev is not None:
                for key, val in row.items():
                    if key == "pos" and prev.get("pos"):
                        if any(abs(a - b) > 1.0
                               for a, b in zip(val, prev["pos"], strict=False)):
                            changed = True
                            break
                    elif key == "yaw":
                        continue
                    elif prev.get(key) != val:
                        changed = True
                        break
            if changed:
                out.write(json.dumps({"t": t, "addr": f"{oa:#x}", "kind": k,
                                      "cls": _cname.get(ca, "?"), **row}) + "\n")
                st["row"] = row
                st["hb"] = now
    except Exception as e:  # noqa: BLE001 — the tracker must never die
        log(event="error", err=repr(e)[:160])
    time.sleep(FAST)
