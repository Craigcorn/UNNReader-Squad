"""Phase 0 agent telemetry: the run_doctor health classifier, the shared offset
tables, build detection, the restart counter, telemetry assembly, and that a
check-in seals correctly. No live process — reflection is faked/monkeypatched."""
from sqreader import __version__, fleet, health, squad_build


# ---- health.run_doctor classification ------------------------------------

class _OkAlloc:
    def fname_to_str(self, ci=0, num=0):
        return "None"


class _Arr:
    """A GUObjectArray whose batched lookup resolves exactly `resolves`.

    The doctor resolves every name it needs in ONE `find_all_by_names` walk,
    so that — not `find_by_name` — is what a mock has to answer."""
    num_elements = 1000
    base = 0x1000

    def __init__(self, resolves=(), addr=0xDEAD):
        self._resolves = set(resolves)
        self._addr = addr

    def find_all_by_names(self, targets, *, alloc=None):
        return {n: (0, self._addr) for n in targets if n in self._resolves}

    def find_by_name(self, name, **k):
        return [(0, self._addr)] if name in self._resolves else []


class _AbsentArr(_Arr):
    def __init__(self):
        super().__init__(resolves=())     # nothing resolves → "unknown"


def _no_drift(*a, **k):
    return [], []


def test_run_doctor_unknown_when_core_class_absent():
    doc = health.run_doctor(pm=None, arr=_AbsentArr(), alloc=_OkAlloc())
    assert doc["state"] == "unknown" and doc["ok"] is True and doc["drift"] == []


def test_run_doctor_unknown_when_anchors_bad():
    class BadAlloc:
        def fname_to_str(self, *a):
            return "xxx"                 # index0 != "None"

    class BadArr(_Arr):
        num_elements = 0                 # out of the sane range

    assert health.run_doctor(None, BadArr(), BadAlloc())["state"] == "unknown"


def test_run_doctor_ok_and_drift(monkeypatch):
    arr = _Arr(resolves={"SQPlayerState"})
    monkeypatch.setattr(health, "check_offset_drift", _no_drift)
    monkeypatch.setattr(health, "check_required_names", _no_drift)
    monkeypatch.setattr(health, "check_struct_fields", _no_drift)
    monkeypatch.setattr(health, "check_reflection_anchors",
                        lambda *a: health.CheckOutcome([], [], []))
    assert health.run_doctor(None, arr, _OkAlloc())["state"] == "ok"

    monkeypatch.setattr(health, "check_offset_drift",
                        lambda *a: ([{"class": "SQDeployable", "field": "Team",
                                      "problem": "offset drift"}], []))
    d = health.run_doctor(None, arr, _OkAlloc())
    assert d["state"] == "drift" and d["ok"] is False and len(d["drift"]) == 1


def test_required_names_drift_when_a_loaded_type_lost_a_name(monkeypatch):
    """The rename that silently darkens a capture: the type is right there,
    the property it used to declare is not. Without this test the drift path
    is exercised nowhere — the run_doctor tests' mock resolves no classes, so
    every row would silently take the absent branch."""
    names = {row[0] for row in health.required_reflection_names()}
    arr = _Arr(resolves=names)

    import sqreader.ue.reflection as refl
    # Layout carries the commander names but NOT CurrentHeldItem.
    monkeypatch.setattr(refl, "get_class_layout",
                        lambda pm, addr, alloc: {"CommanderState": object(),
                                                 "CurrentCommander": object(),
                                                 "HealedTarget": object(),
                                                 "ItemCount": object()})
    drift, skipped = health.check_required_names(None, arr, None)
    assert skipped == []
    assert [(d["class"], d["field"]) for d in drift] == [
        ("SQSoldier", "CurrentHeldItem")]
    assert drift[0]["problem"] == "required name not reflected"


def test_required_names_still_skip_an_optional_row(monkeypatch):
    """No row is optional today, but the semantics stay wired: mark one
    optional with an observed reason and its absence must skip, not alarm."""
    monkeypatch.setattr(health, "required_reflection_names",
                        lambda: [("SomeContentClass", "Class", True, ["Foo"])])
    drift, skipped = health.check_required_names(None, _Arr(), None)
    assert drift == [] and skipped[0]["class"] == "SomeContentClass"


def test_run_doctor_reports_required_name_drift(monkeypatch):
    """A missing required name must surface as state=drift through run_doctor
    — it rides the same alarm the offset tables do."""
    monkeypatch.setattr(health, "check_offset_drift", _no_drift)
    monkeypatch.setattr(health, "check_reflection_anchors",
                        lambda *a: health.CheckOutcome([], [], []))
    monkeypatch.setattr(
        health, "check_required_names",
        lambda *a: ([{"class": "SQSoldier", "field": "CurrentHeldItem",
                      "expected": None, "live": None,
                      "problem": "required name not reflected"}], []))
    d = health.run_doctor(None, _Arr(resolves={"SQPlayerState"}), _OkAlloc())
    assert d["state"] == "drift" and d["ok"] is False
    assert d["drift"][0]["field"] == "CurrentHeldItem"


# ---- the checks the human command used to own alone ----------------------
#
# Each one gets a drift path and a skip path: a check that cannot fire is a
# hole, and a check that fires when there was nothing to measure is the false
# alarm that gets the whole signal ignored.

class _Prop:
    """Stand-in for reflection's FPropertyInfo."""

    def __init__(self, offset=0, type_name="ObjectProperty", addr=0x7000):
        self.offset = offset
        self.type_name = type_name
        self.addr = addr


class _Pm:
    """A /proc reader that answers only the reads it was given."""

    def __init__(self, reads=None):
        self.reads = dict(reads or {})

    def try_read(self, addr, n):
        return self.reads.get((addr, n))

    def read_u64(self, addr):
        b = self.try_read(addr, 8)
        return int.from_bytes(b, "little") if b else 0


def _targets(layouts, pm=None):
    """DoctorTargets over canned layouts: {class name: {field: _Prop}}."""
    addrs = {name: 0x1000 + i * 0x10
             for i, name in enumerate(sorted(layouts)) if layouts[name] is not None}
    tg = health.DoctorTargets(pm, None, addrs, complete=True)
    for name, layout in layouts.items():
        if layout is not None:
            tg._layouts[tg.addr(name)] = layout
    return tg


def test_reflection_anchors_drift_and_skip(monkeypatch):
    """The reflection walker's own assumptions. Nothing else the doctor says
    means anything if these moved, so they have no skip rule beyond the core
    class being absent."""
    import sqreader.ue.reflection as refl
    monkeypatch.setattr(refl, "find_field_by_name_with_super",
                        lambda *a: 0x99)
    monkeypatch.setattr(refl, "read_fstructproperty_struct", lambda *a: 0x99)
    monkeypatch.setattr(health, "_object_name", lambda *a: "SomethingElse")
    monkeypatch.setattr(refl, "bool_property_mask", lambda *a: (0x2c2, 0x10))
    monkeypatch.setattr(refl, "struct_layout_for_field", lambda *a: {})
    out = health.check_reflection_anchors(None, None,
                                          _targets({"SQPlayerState": {}}))
    problems = {d["field"] for d in out.drift}
    assert problems == {"PlayerStateData", "bIsABot", "NumKills/RevivedPoints"}
    assert all(n["ok"] is False for n in out.notes)

    # Core class absent → skipped, never drift.
    out = health.check_reflection_anchors(None, None,
                                          _targets({"SQPlayerState": None}))
    assert out.drift == [] and out.skipped[0]["check"] == "reflection anchors"


def test_lane_graph_drift_and_skips():
    from sqreader.squad.snapshot import (
        LANE_GRAPH_OFFSETS, LANE_VISUALIZER_ROUTE_INDEX_OFF,
    )
    moved = LANE_GRAPH_OFFSETS["DesignOutgoingLinks"] + 8
    out = health.check_lane_graph(_Pm(), None, _targets({
        "SQGraphInitializerComponent": {
            "DesignOutgoingLinks": _Prop(moved, "ArrayProperty")},
        "SQGraphRAASVisualizerComponent": {
            "RouteIndex": _Prop(LANE_VISUALIZER_ROUTE_INDEX_OFF + 4,
                                "IntProperty")},
    }))
    fields = {d["field"] for d in out.drift}
    assert "DesignOutgoingLinks" in fields and "RouteIndex" in fields

    # A layer with no lane graph, and an AAS layer with no RAAS visualizer:
    # both are content, not breakage. Absent means skipped.
    out = health.check_lane_graph(_Pm(), None, _targets({
        "SQGraphInitializerComponent": None,
        "SQGraphRAASVisualizerComponent": None}))
    assert out.drift == []
    assert {s["check"] for s in out.skipped} == {"lane graph", "lane RouteIndex"}


def test_marker_stride_drift_and_skip():
    from sqreader.squad.snapshot import MARKER_MGR_MARKER_ARRAY_OFFSET
    out = health.check_marker_stride(_Pm(), None, _targets({
        "SQMapMarkerManagerComponent": {
            "MarkerArray": _Prop(MARKER_MGR_MARKER_ARRAY_OFFSET + 8,
                                 "StructProperty")}}))
    assert [d["field"] for d in out.drift] == ["MarkerArray"]

    out = health.check_marker_stride(_Pm(), None,
                                     _targets({"SQMapMarkerManagerComponent": None}))
    assert out.drift == [] and out.skipped[0]["check"] == "marker FastArray stride"


# -- ComponentToWorld: the value check, and the thresholds that keep it quiet

_ROOT_OFF, _REL_OFF, _ATT_OFF = 0x10, 0x20, 0x30


def _c2w_case(monkeypatch, n_actors, n_bad, *, unreadable=0):
    """n_actors vehicles, n_bad of them disagreeing, `unreadable` of them with
    no root pointer at all."""
    actors = [0x100000 + i * 0x1000 for i in range(n_actors)]
    reads = {}
    roots = {}
    for i, a in enumerate(actors):
        if i < unreadable:
            continue
        root = a + 0x800
        roots[a] = root
        reads[(a + _ROOT_OFF, 8)] = root.to_bytes(8, "little")
        reads[(root + _ATT_OFF, 8)] = (0).to_bytes(8, "little")  # unattached

    class _V:
        def __init__(self, x):
            self.x = self.y = self.z = x

    bad_roots = {roots[a] for a in actors[len(actors) - n_bad:] if a in roots}

    def fake_fvector(pm, addr):
        for root in roots.values():
            if addr == root + health.SCENE_COMPONENT_TO_WORLD_TRANSLATION_OFF:
                return _V(9.0 if root in bad_roots else 1.0)
            if addr == root + _REL_OFF:
                return _V(1.0)
        return None

    import sqreader.ue.value as value
    monkeypatch.setattr(value, "read_fvector", fake_fvector)
    tg = _targets({"Actor": {"RootComponent": _Prop(_ROOT_OFF)},
                   "SceneComponent": {"RelativeLocation": _Prop(_REL_OFF),
                                      "AttachParent": _Prop(_ATT_OFF)}})
    return health.check_component_to_world(_Pm(reads), None, tg, actors)


def test_component_to_world_tolerates_a_straggler(monkeypatch):
    """One vehicle caught mid-teleport writes its transform a frame late. That
    is not a moved constant, and alarming on it would teach an operator to
    ignore the check that matters."""
    out = _c2w_case(monkeypatch, 5, 1)
    assert out.drift == [] and out.skipped == []
    assert out.notes[0]["ok"] is True and "4/5" in out.notes[0]["detail"]


def test_component_to_world_reports_a_majority_mismatch(monkeypatch):
    out = _c2w_case(monkeypatch, 5, 4)
    assert len(out.drift) == 1
    assert out.drift[0]["field"] == "ComponentToWorld.Translation"


def test_component_to_world_skips_below_quorum(monkeypatch):
    """Two readable vehicles is not a sample. Nothing to measure is never
    drift."""
    out = _c2w_case(monkeypatch, 2, 2)
    assert out.drift == [] and out.skipped[0]["check"] == "ComponentToWorld"
    assert "need 3" in out.skipped[0]["reason"]


def test_component_to_world_skips_unreadable_actors(monkeypatch):
    """A poisoned sample reads nothing back — that is a skip, not an alarm."""
    out = _c2w_case(monkeypatch, 5, 0, unreadable=5)
    assert out.drift == [] and out.skipped[0]["check"] == "ComponentToWorld"


def test_component_to_world_skips_without_a_sample():
    out = health.check_component_to_world(_Pm(), None, _targets({}), None)
    assert out.drift == []
    assert out.skipped[0]["reason"] == "no actor sample supplied"


def test_collector_fields_only_fail_is_drift():
    """A sibling that read while this one did not is layout drift; everything
    else these counters do is event-gating."""
    players = [{"stats": {"fobsBuilt": 3, "captures": 2, "defenses": 0}}]
    out = health.check_collector_fields(players)
    assert [d["field"] for d in out.drift] == ["suppliesDelivered"]
    assert any("vehicleDamage" in s["reason"] for s in out.skipped)
    # An empty server measures nothing; no snapshot at all says nothing.
    assert health.check_collector_fields([]).drift == []
    assert health.check_collector_fields(None) == health.CheckOutcome([], [], [])


# ---- run_doctor composition ----------------------------------------------

def test_run_doctor_carries_a_moved_checks_drift(monkeypatch):
    """A check that used to be human-only must reach the machine verdict —
    that is the entire point of moving it."""
    monkeypatch.setattr(health, "check_offset_drift", _no_drift)
    monkeypatch.setattr(health, "check_required_names", _no_drift)
    monkeypatch.setattr(health, "check_struct_fields", _no_drift)
    monkeypatch.setattr(
        health, "check_reflection_anchors",
        lambda *a: health.CheckOutcome(
            [{"class": "SQPlayerState", "field": "bIsABot", "expected": 8,
              "live": 16, "problem": "FBoolProperty byte-mask layout moved"}],
            [], [{"label": "FBoolProperty.ByteMask layout", "ok": False,
                  "detail": "got (706, 16)"}]))
    d = health.run_doctor(None, _Arr(resolves={"SQPlayerState"}), _OkAlloc())
    assert d["state"] == "drift" and d["drift"][0]["field"] == "bIsABot"
    assert {"check": "FBoolProperty.ByteMask layout", "state": "failed",
            "reason": "got (706, 16)"} in d["checks"]


def test_run_doctor_skips_land_in_checks_not_drift(monkeypatch):
    """Silence and "could not measure" are different things. A skip is
    reported, and it is never drift."""
    monkeypatch.setattr(health, "check_offset_drift", _no_drift)
    monkeypatch.setattr(health, "check_required_names", _no_drift)
    monkeypatch.setattr(health, "check_struct_fields", _no_drift)
    monkeypatch.setattr(health, "check_reflection_anchors",
                        lambda *a: health.CheckOutcome([], [], []))
    d = health.run_doctor(None, _Arr(resolves={"SQPlayerState"}), _OkAlloc())
    assert d["state"] == "ok" and d["drift"] == []
    skipped = {c["check"] for c in d["checks"] if c["state"] == "skipped"}
    # No sample was passed and no lane/marker class resolved in the mock.
    assert {"ComponentToWorld", "lane graph", "marker FastArray stride"} <= skipped
    assert all(c.get("reason") for c in d["checks"] if c["state"] == "skipped")


def test_c2w_sample_takes_only_unattached_placed_vehicles():
    """The sample the serve loop hands over: attached vehicles carry a
    parent-relative location, so comparing them would compare two different
    things."""
    from sqreader.cli import _c2w_sample

    snap = {"vehicles": [
        {"id": "7f0000000010", "position": [1, 2, 3]},
        {"id": "7f0000000020", "position": [1, 2, 3], "attached": True},
        {"id": "7f0000000030"},                         # no position
        {"position": [1, 2, 3]},                        # no id
        {"id": "7f0000000040", "position": [1, 2, 3]},
    ]}
    assert _c2w_sample(snap) == [0x7F0000000010, 0x7F0000000040]
    assert _c2w_sample(snap, limit=1) == [0x7F0000000010]
    assert _c2w_sample(None) == [] and _c2w_sample({}) == []


def test_hardcoded_offset_tables_shape():
    tables = health.hardcoded_offset_tables()
    names = {c for c, _k, _o, _t in tables}
    assert {"SQDeployable", "SQVehicleWeapon", "SQMapMarker"} <= names
    for _cls, kind, optional, tbl in tables:
        assert kind in {"Class", "ScriptStruct", "BlueprintGeneratedClass"}
        assert isinstance(optional, bool)
        assert tbl and all(isinstance(v, int) for v in tbl.values())


def test_only_content_loaded_types_are_optional():
    """Optional means "this level may genuinely not have loaded it", and after
    2026-08-30 that is a much smaller set than it looked. Native SQ* classes
    are registered when the C++ module loads, not when content spawns — all of
    them answered on a 0-player server with nothing built — so their absence
    is a rename and must be drift. Only blueprints load with content."""
    tables = {c: opt for c, _k, opt, _t in health.hardcoded_offset_tables()}
    assert tables.get("BP_BaseFobCreator_C") is True
    for native in ("SQDeployableVehicle", "SQVehicleSeatConfig",
                   "SQPlayerController", "SQMapMarkerManagerComponent",
                   "SQDeployable", "SQVehicle"):
        assert tables.get(native) is False, native
    assert all(not opt for _c, _k, opt, _n in health.required_reflection_names())


def test_required_name_row_absence_is_drift_now(monkeypatch):
    """The blind spot Item B closes: a class-level rename of
    SQHealingEquipableItem used to look exactly like "no medic item in this
    level" and skipped forever."""
    arr = _Arr(resolves=set())
    drift, skipped = health.check_required_names(None, arr, None)
    assert skipped == []
    assert {d["class"] for d in drift} == {
        row[0] for row in health.required_reflection_names()}
    assert all(d["problem"] == "class not found" for d in drift)


# ---- struct-internal tier -------------------------------------------------

def test_struct_field_tables_shape():
    rows = health.struct_field_tables()
    owners = {owner for owner, _k, _p, _o, _t in rows}
    assert {"SQSoldier", "SQMapMarkerManagerComponent"} <= owners
    for _owner, kind, path, optional, tbl in rows:
        assert kind in {"Class", "ScriptStruct"}
        assert path and all(isinstance(h, str) for h in path)
        assert isinstance(optional, bool)
        assert tbl and all(isinstance(v, int) for v in tbl.values())


def test_struct_fields_report_drift_inside_the_struct(monkeypatch):
    """The damage-event internals are the point: LastTakeHitInfo.ActualDamage
    is not a field of SQSoldier, so nothing in the class tier can see it move."""
    import sqreader.ue.reflection as refl
    from sqreader.squad.snapshot import THI_ACTUAL_DAMAGE_OFFSET

    monkeypatch.setattr(refl, "find_field_by_name_with_super",
                        lambda *a: 0x77)
    monkeypatch.setattr(refl, "read_fstructproperty_struct", lambda *a: 0x88)
    # Every hop resolves; ActualDamage has moved 8 bytes on inside the struct.
    monkeypatch.setattr(
        refl, "get_class_layout",
        lambda pm, addr, alloc: {
            "ActualDamage": _Prop(THI_ACTUAL_DAMAGE_OFFSET + 8),
            "PointDamageEvent": _Prop(0x38), "HitInfo": _Prop(0x30),
            "Distance": _Prop(0x8), "BoneName": _Prop(0xF0),
            "Items": _Prop(0x108)})
    owners = {owner for owner, _k, _p, _o, _t in health.struct_field_tables()}
    arr = _Arr(resolves=owners)
    tg = health.DoctorTargets(None, None, {o: 0x1000 for o in owners},
                              complete=True)
    tg._layouts[0x1000] = {"LastTakeHitInfo": _Prop(0x24E8),
                           "MarkerArray": _Prop(0xB8)}
    drift, skipped = health.check_struct_fields(None, arr, None, tg)
    assert skipped == []
    assert any(d["field"] == "ActualDamage" and d["problem"] == "offset drift"
               for d in drift)


def test_struct_fields_call_a_renamed_hop_drift_and_a_dead_read_a_skip(
        monkeypatch):
    """A hop whose name is gone is a Squad rename — drift. A hop whose name is
    there but will not read is /proc having a bad moment — skip, because
    doctor is what you run when reads are failing."""
    import sqreader.ue.reflection as refl
    tg = health.DoctorTargets(None, None, {"SQSoldier": 0x1000,
                                           "SQMapMarkerManagerComponent": 0x2000},
                              complete=True)
    tg._layouts[0x1000] = {}                      # LastTakeHitInfo gone
    tg._layouts[0x2000] = {"MarkerArray": _Prop(0xB8)}
    monkeypatch.setattr(refl, "find_field_by_name_with_super", lambda *a: None)
    monkeypatch.setattr(refl, "read_fstructproperty_struct", lambda *a: 0)
    drift, skipped = health.check_struct_fields(None, _Arr(), None, tg)
    assert any(d["field"] == "LastTakeHitInfo"
               and d["problem"] == "struct field not reflected" for d in drift)
    assert any(s["class"] == "SQMapMarkerManagerComponent.MarkerArray"
               and "transient" in s["reason"] for s in skipped)


def test_struct_field_drift_reaches_run_doctor(monkeypatch):
    monkeypatch.setattr(health, "check_offset_drift", _no_drift)
    monkeypatch.setattr(health, "check_required_names", _no_drift)
    monkeypatch.setattr(health, "check_struct_fields", _no_drift)
    monkeypatch.setattr(health, "check_reflection_anchors",
                        lambda *a: health.CheckOutcome([], [], []))
    monkeypatch.setattr(
        health, "check_struct_fields",
        lambda *a: ([{"class": "SQSoldier.LastTakeHitInfo",
                      "field": "ActualDamage", "expected": 0, "live": 8,
                      "problem": "offset drift"}], []))
    d = health.run_doctor(None, _Arr(resolves={"SQPlayerState"}), _OkAlloc())
    assert d["state"] == "drift" and d["drift"][0]["field"] == "ActualDamage"


def test_every_readable_hardcoded_offset_is_watched():
    """A hardcoded offset the reader can read THROUGH — used directly or as a
    reflection fallback — must be verifiable, or a Squad update moves the
    struct and the reader ships neighbouring bytes as data with doctor still
    green. Everything left out is named in `hardcoded_offset_tables`' register
    WITH a reason; this test is what keeps that register honest."""
    from sqreader.squad import snapshot as snap

    # Membership is by VALUE, which carries a caveat worth naming: two
    # constants that happen to share a number mask each other, so a new
    # constant whose value coincides with a watched one passes this test
    # without being watched at all. The register is what catches those; this
    # test only guarantees nobody adds an offset in silence.
    watched = {v for _c, _k, _o, tbl in health.hardcoded_offset_tables()
               for v in tbl.values()}
    watched |= {v for _c, _k, _p, _o, tbl in health.struct_field_tables()
                for v in tbl.values()}
    # Each of these is in the register in health.hardcoded_offset_tables,
    # with the reason it cannot (yet) be checked by name.
    exempt = {
        # Private C++ members, not UPROPERTIES — reflection cannot see them;
        # validated by the value-based collector verdicts instead.
        "COLLECTOR_OFFSETS",
        # Verified by cmd_doctor's mode-aware lane-graph section instead.
        "LANE_GRAPH_OFFSETS", "LANE_LINK_NODEA_OFF", "LANE_LINK_NODEB_OFF",
        "LANE_LINK_SIZE", "LANE_VISUALIZER_ROUTE_INDEX_OFF",
        # Declared but never read — nothing can drift through them.
        "MARKER_ITEM_OFFSETS", "SQ_SEATCOMP_ANIM_STATE_OFFSET",
        "SQ_SEATCOMP_FORCE_OCCUPIED_OFFSET", "SQ_VEHCOMP_STATE_OFFSET",
        # Derived: MARKER_MGR_MARKER_ARRAY_OFFSET + MARKER_ARRAY_ITEMS_OFFSET,
        # and both halves are watched (class tier + struct tier).
        "MARKER_ITEMS_ABS_OFFSET",
    }
    missing = []
    for name in dir(snap):
        if name.startswith("_") or name in exempt:
            continue
        if not name.endswith(("_OFF", "_OFFSET", "_OFFSETS")):
            continue
        val = getattr(snap, name)
        if isinstance(val, int):
            if val not in watched:
                missing.append(name)
        elif isinstance(val, dict):
            # Only flat {field: offset} tables are checkable this way; a
            # nested table (COLLECTOR_OFFSETS) is exempt by name above.
            flat = {v for v in val.values() if isinstance(v, int)}
            if flat and not (flat & watched):
                missing.append(name)
    assert not missing, (
        "hardcoded offsets not verified by doctor — add them to "
        f"health.hardcoded_offset_tables, or to its register with a "
        f"reason and to this test's exempt set: {missing}")


def test_required_reflection_names_cover_the_fallbackless_reads():
    rows = health.required_reflection_names()
    by_cls = {c: set(names) for c, _k, _o, names in rows}
    # Medical capture and the commander-identity hop have no fallback
    # constants: a rename is invisible without these rows.
    assert "CurrentHeldItem" in by_cls["SQSoldier"]
    assert {"HealedTarget", "ItemCount"} <= by_cls["SQHealingEquipableItem"]
    assert "CurrentCommander" in by_cls["SQCommanderState"]
    for _cls, kind, optional, names in rows:
        assert kind in {"Class", "ScriptStruct"} and names
        assert isinstance(optional, bool)


# ---- build detection + restart counter -----------------------------------

def test_build_sha256_off_proc_is_none():
    # No readable /proc/<pid>/exe on the test host → None, never raises.
    assert squad_build.build_sha256(2**31 - 1) is None


def test_short_build():
    assert squad_build.short_build(None) is None
    assert squad_build.short_build("abcdef0123456789") == "abcdef012345"


def test_engine_version_is_none():
    assert squad_build.engine_version(None, None, None) is None


def test_bump_restarts_increments(tmp_path):
    assert fleet.bump_restarts(tmp_path) == 1
    assert fleet.bump_restarts(tmp_path) == 2
    assert fleet.bump_restarts(tmp_path) == 3


# ---- telemetry assembly + sealed check-in --------------------------------

def test_gather_telemetry(monkeypatch):
    monkeypatch.setattr(health, "run_doctor",
                        lambda *a, **k: {"state": "drift", "ok": False,
                                         "drift": [{"class": "X", "field": "y"}]})
    t = fleet.gather(pm=None, arr=None, alloc=None, build_sha="abc123",
                     restarts=2, uptime_sec=99.9, channel="beta")
    assert t["schema"] == "sqr-checkin-1"
    assert t["agent_version"] == __version__
    assert t["squad_build"] == "abc123"
    assert t["health"] == "drift" and t["restarts"] == 2 and t["channel"] == "beta"
    assert t["uptime_sec"] == 99                    # int-coerced
    assert len(t["drift"]) == 1


def test_gather_reports_which_checks_could_not_measure(monkeypatch):
    """A chronic skip is a coverage hole that looks exactly like a pass from
    central. The payload has to be able to tell them apart — additively, on
    the same schema string."""
    monkeypatch.setattr(
        health, "run_doctor",
        lambda *a, **k: {"state": "ok", "ok": True, "drift": [], "checks": [
            {"check": "ComponentToWorld", "state": "skipped",
             "reason": "no actor sample supplied"},
            {"check": "marker FastArray stride", "state": "passed"},
            {"check": "lane graph", "state": "skipped", "reason": "no lanes"},
        ]})
    t = fleet.gather(pm=None, arr=None, alloc=None, build_sha=None,
                     restarts=0, uptime_sec=1, channel="stable")
    assert t["schema"] == "sqr-checkin-1"           # additive, not a new schema
    assert t["skipped"] == ["ComponentToWorld", "lane graph"]


def test_gather_caps_the_skipped_list(monkeypatch):
    monkeypatch.setattr(
        health, "run_doctor",
        lambda *a, **k: {"state": "ok", "ok": True, "drift": [], "checks": [
            {"check": f"c{i}", "state": "skipped", "reason": "x"}
            for i in range(40)]})
    t = fleet.gather(pm=None, arr=None, alloc=None, build_sha=None,
                     restarts=0, uptime_sec=1, channel="stable")
    assert len(t["skipped"]) == 10


def test_gather_passes_the_actor_sample_through(monkeypatch):
    """The sample is the whole reason the machine doctor can check the
    position transform without building a snapshot of its own."""
    seen = {}

    def fake(pm, arr, alloc, *, sample_actors=None):
        seen["sample"] = sample_actors
        return {"state": "ok", "ok": True, "drift": [], "checks": []}

    monkeypatch.setattr(health, "run_doctor", fake)
    fleet.gather(pm=None, arr=None, alloc=None, build_sha=None, restarts=0,
                 uptime_sec=1, channel="stable", sample_actors=[0x10, 0x20])
    assert seen["sample"] == [0x10, 0x20]


def test_checkin_seals_and_posts(monkeypatch):
    import json

    from sqreader import ingest_client as ic
    from sqreader.crypto_envelope import open_envelope

    captured: dict = {}

    def fake_post(url, obj, *, timeout):
        captured["url"] = url
        captured["env"] = obj
        return {"ok": True}

    monkeypatch.setattr(ic, "_post_json", fake_post)
    secret = bytes(range(32))
    creds = {"SQREADER_AGENT_ID": "eu1",
             "SQREADER_AGENT_SECRET_HEX": secret.hex(),
             "SQREADER_PUSH_URL": "https://c.test"}
    ack = ic.checkin(creds, {"schema": "sqr-checkin-1", "health": "ok"}, seq=5)
    assert ack == {"ok": True}
    assert captured["url"] == "https://c.test/agent/checkin"
    payload = json.loads(open_envelope(captured["env"], secret=secret))
    assert payload["health"] == "ok" and payload["schema"] == "sqr-checkin-1"
