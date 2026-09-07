"""The command-assets probe: what the commander state, the command markers,
the per-call actors and the action configs hold, tick by tick.

The oracle of the acceptance run (tracker test T8) and the capture the
commander tests decode from: T9 (the unobserved commander branches), T13
(the action configs' display strings) and T14 (the commander drone's call
actor). Authored for the 08-30 and 09-02 sessions as `cmd_probe_b1.py` on
the box; graduated here 2026-09-07 with the strides the 09-04 self-test
build took from reflection and the two string reads T13 needs.

Every ~1 s it walks GUObjectArray for live objects whose class name matches
the watch list and, for each: emits new/gone rows, dumps the class's
reflected layout once (JSON to /tmp/cmd_layouts/, the shape
`spec_names_check.py --archive` reads) and records a raw memory snapshot of
the instance every tick, so field changes are decoded offline against the
layouts. `SQCommanderState`'s arrays (categories, intervals, nominees,
last-use stamps) live behind pointers outside the instance and are chased
with element sizes from reflection — never a guessed stride: the 09-02
build sliced 40-byte items at 32 and lost the one mid-cooldown sample.

The action configs (`CommandAction_*`) load when a commander claim
resolves. Each loaded CDO is read by name at first sight and again whenever
a value changes: `CategoryId`, `EnrouteDuration`, `ActiveDuration`,
`CooldownDuration`, `DisplayName`, `Description`, `MapMarkerClass`,
`CommandActor` — the two strings are what T13 settles.

Read-only; never dies on a bad read; fails at startup if the commander
state class or one of its arrays does not resolve. Restart it after a layer
change (class addresses churn). Appends JSONL to /tmp/command_assets.jsonl.
Run on the box after the layer is loaded:
  `.venv/bin/python scripts/probes/command_assets.py`
"""
import json
import os
import struct
import time

from probe_common import attach

from sqreader.squad.snapshot import read_fstring
from sqreader.ue.reflection import (find_field_by_name_with_super, get_class_layout,
                                    read_farrayproperty_inner, read_fproperty,
                                    read_fstructproperty_struct, read_ustruct_header,
                                    struct_layout_for_field)
from sqreader.ue.uobject import UOBJ_CLASS_PRIVATE, UOBJ_NAME_PRIVATE

WATCH = ("command", "vote", "uav", "drone", "wasp",
         "cas_", "arty", "barrage", "airstrike", "a10", "su25",
         "handheldat", "recondrone")
STATE_CLS = "SQCommanderState"
# (label, field on SQCommanderState, the FastArray's inner struct field or
# None for a plain TArray)
AUX_FIELDS = (("categories", "CommanderCategories", None),
              ("intervals", "CommandIntervals", "Items"),
              ("nominees", "NomineeStatus", "Items"),
              ("lastUse", "LastCategoryGameTime", None))
CDO_PREFIX = "Default__CommandAction"
CDO_FIELDS = ("CategoryId", "EnrouteDuration", "ActiveDuration", "CooldownDuration",
              "DisplayName", "Description", "MapMarkerClass", "CommandActor")
RAW_BYTES = 0x800          # covers every watched class's layout (max seen 0x5a0)
MAX_RAW_PER_TICK = 32
AUX_MAX_BYTES = 1024
SCAN = 1.0
HEARTBEAT = 30.0
OUT = "/tmp/command_assets.jsonl"
LAYOUT_DIR = "/tmp/cmd_layouts"

_a = attach()
pm, arr, alloc = _a.pm, _a.arr, _a.alloc
os.makedirs(LAYOUT_DIR, exist_ok=True)
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


# --- startup: the commander state's arrays, strides from reflection -------------

# Sizes of the scalar property types a TArray may hold — the type's own size,
# not a layout guess (the header's ElementSize reads 0 on the box's build for
# a plain float array, 2026-09-07 smoke test).
_SCALAR_SIZES = {"FloatProperty": 4, "IntProperty": 4, "UInt32Property": 4,
                 "DoubleProperty": 8, "Int64Property": 8, "ByteProperty": 1,
                 "BoolProperty": 1, "ObjectProperty": 8, "ClassProperty": 8,
                 "WeakObjectProperty": 8, "NameProperty": 8, "StrProperty": 16}


def _elem_size(array_field_addr):
    """Element size of a TArray property: the inner struct's reflected size, or
    the scalar type's own size for a plain array."""
    inner = read_farrayproperty_inner(pm, array_field_addr)
    info = read_fproperty(pm, inner, alloc) if inner else None
    if info is None:
        raise RuntimeError("array inner property unreadable")
    if info.type_name == "StructProperty":
        st = read_fstructproperty_struct(pm, inner)
        size = read_ustruct_header(pm, st, alloc).properties_size if st else 0
    else:
        size = _SCALAR_SIZES.get(info.type_name, 0) or info.element_size
    if size <= 0:
        raise RuntimeError(f"element size of {info.type_name} read as {size}")
    return size


found = arr.find_all_by_names({STATE_CLS: "Class"}, alloc=alloc)
if STATE_CLS not in found:
    raise SystemExit(f"{STATE_CLS} is not loaded; nothing to watch")
STATE_ADDR = found[STATE_CLS][1]
_state_layout = get_class_layout(pm, STATE_ADDR, alloc)
AUX = []   # (label, header offset in the instance, element size)
for label, fname, inner_field in AUX_FIELDS:
    if fname not in _state_layout:
        raise SystemExit(f"{STATE_CLS}.{fname} not found by reflection")
    try:
        if inner_field is None:
            ff = find_field_by_name_with_super(pm, STATE_ADDR, fname, alloc)
            AUX.append((label, _state_layout[fname].offset, _elem_size(ff)))
        else:
            items = struct_layout_for_field(pm, STATE_ADDR, fname, alloc)[inner_field]
            AUX.append((label, _state_layout[fname].offset + items.offset,
                        _elem_size(items.addr)))
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"{STATE_CLS}.{fname}: stride unresolved ({e!r})") from e
log(event="start", pid=_a.pid, state_cls=f"{STATE_ADDR:#x}",
    aux={label: {"off": f"{off:#x}", "elem": size} for label, off, size in AUX})


# --- classes ----------------------------------------------------------------

_cname: dict = {}       # class addr -> name
_watched: dict = {}     # class addr -> bool
_layout: dict = {}      # class addr -> layout (dumped once)


def cname(ca):
    if ca not in _cname:
        _cname[ca] = uname(ca) or f"cls_{ca:x}"
    return _cname[ca]


def watched(ca):
    if ca not in _watched:
        n = cname(ca)
        # UI widget templates (map-vote screens etc.) match "vote" and would
        # flood the raw-dump cap; the state lives on the SQ actors.
        _watched[ca] = (not n.startswith(("UMG_", "UI_", "W_"))
                        and any(k in n.lower() for k in WATCH))
    return _watched[ca]


def layout(ca):
    """The class's reflected layout, dumped to the archive shape at first sight."""
    if ca in _layout:
        return _layout[ca]
    n = cname(ca)
    try:
        lay = get_class_layout(pm, ca, alloc)
        js = {k: {"offset": p.offset, "type": p.type_name} for k, p in lay.items()}
        with open(f"{LAYOUT_DIR}/{n}.json", "w") as f:
            json.dump(js, f, indent=1)
        log(event="layout", cls=n, fields=len(js))
    except Exception as e:  # noqa: BLE001
        lay = {}
        log(event="layout-error", cls=n, err=repr(e)[:100])
    _layout[ca] = lay
    return lay


def priority(n):
    """Raw dumps are capped per tick; the state actors, the command markers
    and the per-call actors must never be crowded out by zone or vehicle
    floods (the 09-02 mortar raws were)."""
    if (STATE_CLS in n or "MapMarker_Command" in n or "CommandActor" in n
            or "500lb" in n):
        return 0
    return 1 if n.startswith("SQ") else 2


# --- action configs (test T13) -----------------------------------------------

_cdo_last: dict = {}    # class addr -> values last written


def read_cdo(oa, ca):
    lay = layout(ca)
    values, missing = {}, []
    for name in CDO_FIELDS:
        p = lay.get(name)
        if p is None:
            missing.append(name)
            continue
        v = None
        try:
            if p.type_name == "ByteProperty":
                b = pm.try_read(oa + p.offset, 1)
                v = b[0] if b else None
            elif p.type_name == "FloatProperty":
                b = pm.try_read(oa + p.offset, 4)
                v = round(struct.unpack("<f", b)[0], 2) if b else None
            elif p.type_name == "StrProperty":
                v = read_fstring(pm, oa + p.offset)
            elif p.type_name in ("ClassProperty", "ObjectProperty"):
                ptr = u64(oa + p.offset)
                v = uname(ptr) if ptr else None
            else:
                v = f"<{p.type_name}>"
        except Exception:  # noqa: BLE001
            v = None
        values[name] = v
    if _cdo_last.get(ca) != values:
        _cdo_last[ca] = values
        log(event="action-config", cls=cname(ca), addr=f"{oa:#x}",
            values=values, missing=missing)


# --- main loop --------------------------------------------------------------

prev_live: dict = {}
last_beat = 0.0
while True:
    t0 = time.time()
    t = round(t0, 2)
    try:
        live = {}
        for _i, oa in arr.iter_object_addrs():
            if not oa:
                continue
            ca = cls_of(oa)
            if not ca or not watched(ca):
                continue
            nm = uname(oa) or ""
            if nm.startswith("Default__"):
                if nm.startswith(CDO_PREFIX):
                    read_cdo(oa, ca)
                continue          # a CDO, not a live instance
            live[oa] = (ca, nm)
        for oa, (ca, nm) in live.items():
            if oa not in prev_live:
                log(event="new", cls=cname(ca), name=nm, addr=f"{oa:#x}")
                layout(ca)
        for oa, (ca, nm) in prev_live.items():
            if oa not in live:
                log(event="gone", cls=cname(ca), name=nm, addr=f"{oa:#x}")
        # raw snapshot of every live watched instance, capped and priority-
        # ordered so the state actors always make the cut
        ordered = sorted(live.items(), key=lambda kv: priority(cname(kv[1][0])))
        for oa, (ca, _nm) in ordered[:MAX_RAW_PER_TICK]:
            n = cname(ca)
            raw = pm.try_read(oa, RAW_BYTES)
            if raw:
                out.write(json.dumps({"t": t, "addr": f"{oa:#x}", "cls": n,
                                      "raw": raw.hex()}) + "\n")
            if ca != STATE_ADDR:
                continue
            for label, off, elem in AUX:
                hdr = pm.try_read(oa + off, 12)
                if not hdr or len(hdr) != 12:
                    continue
                ptr, cnt = struct.unpack("<Qi", hdr)
                if ptr and 0 < cnt <= 64:
                    data = pm.try_read(ptr, min(cnt * elem, AUX_MAX_BYTES))
                    if data:
                        out.write(json.dumps({"t": t, "addr": f"{oa:#x}", "aux": label,
                                              "n": cnt, "elem": elem,
                                              "truncated": cnt * elem > AUX_MAX_BYTES,
                                              "raw": data.hex()}) + "\n")
        prev_live = live
        if t0 - last_beat > HEARTBEAT:
            log(event="heartbeat", live=len(live), cdos=len(_cdo_last))
            last_beat = t0
    except Exception as e:  # noqa: BLE001 — the probe must never die
        log(event="error", err=repr(e)[:160])
    time.sleep(max(0.0, SCAN - (time.time() - t0)))
