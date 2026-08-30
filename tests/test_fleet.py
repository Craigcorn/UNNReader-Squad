"""Phase 0 agent telemetry: the run_doctor health classifier, the shared offset
tables, build detection, the restart counter, telemetry assembly, and that a
check-in seals correctly. No live process — reflection is faked/monkeypatched."""
from sqreader import __version__, fleet, health, squad_build


# ---- health.run_doctor classification ------------------------------------

class _OkAlloc:
    def fname_to_str(self, ci=0, num=0):
        return "None"


class _AbsentArr:
    num_elements = 1000

    def find_by_name(self, *a, **k):
        return []          # SQPlayerState unresolvable → "unknown", not drift


def test_run_doctor_unknown_when_core_class_absent():
    doc = health.run_doctor(pm=None, arr=_AbsentArr(), alloc=_OkAlloc())
    assert doc["state"] == "unknown" and doc["ok"] is True and doc["drift"] == []


def test_run_doctor_unknown_when_anchors_bad():
    class BadAlloc:
        def fname_to_str(self, *a):
            return "xxx"                 # index0 != "None"

    class BadArr:
        num_elements = 0                 # out of the sane range

        def find_by_name(self, *a, **k):
            return []

    assert health.run_doctor(None, BadArr(), BadAlloc())["state"] == "unknown"


def test_run_doctor_ok_and_drift(monkeypatch):
    class Arr:
        num_elements = 1000

        def find_by_name(self, name, **k):
            return [("addr", 0xDEAD)] if name == "SQPlayerState" else []

    monkeypatch.setattr(health, "check_offset_drift", lambda *a: [])
    assert health.run_doctor(None, Arr(), _OkAlloc())["state"] == "ok"

    monkeypatch.setattr(health, "check_offset_drift",
                        lambda *a: [{"class": "SQDeployable", "field": "Team",
                                     "problem": "offset drift"}])
    d = health.run_doctor(None, Arr(), _OkAlloc())
    assert d["state"] == "drift" and d["ok"] is False and len(d["drift"]) == 1


def test_required_names_drift_when_present_and_skip_when_absent(monkeypatch):
    """The tier's whole contract in one place: a loaded type missing a
    declared name is drift (the rename that silently darkens a capture); a
    type that is not loaded is skipped, never drift. Without this test the
    drift path is exercised nowhere — the run_doctor tests' mock resolves no
    classes, so every row silently takes the skip branch."""
    class Arr:
        def find_by_name(self, name, **k):
            if name == "SQHealingEquipableItem":
                return []                      # not loaded in this level
            return [(1, 0xBEEF)]

    import sqreader.ue.reflection as refl
    # Layout carries the commander names but NOT CurrentHeldItem.
    monkeypatch.setattr(refl, "get_class_layout",
                        lambda pm, addr, alloc: {"CommanderState": object(),
                                                 "CurrentCommander": object()})
    drift, skipped = health.check_required_names(None, Arr(), None)
    assert any(d["class"] == "SQSoldier" and d["field"] == "CurrentHeldItem"
               and d["problem"] == "required name not reflected"
               for d in drift)
    assert any(s["class"] == "SQHealingEquipableItem" for s in skipped)
    assert not any(d["class"] == "SQHealingEquipableItem" for d in drift)
    assert not any(d["class"] in ("SQTeamState", "SQCommanderState")
                   for d in drift)


def test_run_doctor_reports_required_name_drift(monkeypatch):
    """A missing required name must surface as state=drift through run_doctor
    — it rides the same alarm the offset tables do."""
    class Arr:
        num_elements = 1000

        def find_by_name(self, name, **k):
            return [("addr", 0xDEAD)] if name == "SQPlayerState" else []

    monkeypatch.setattr(health, "check_offset_drift", lambda *a: [])
    monkeypatch.setattr(
        health, "check_required_names",
        lambda *a: ([{"class": "SQSoldier", "field": "CurrentHeldItem",
                      "expected": None, "live": None,
                      "problem": "required name not reflected"}], []))
    d = health.run_doctor(None, Arr(), _OkAlloc())
    assert d["state"] == "drift" and d["ok"] is False
    assert d["drift"][0]["field"] == "CurrentHeldItem"


def test_hardcoded_offset_tables_shape():
    tables = health.hardcoded_offset_tables()
    names = {c for c, _k, _o, _t in tables}
    assert {"SQDeployable", "SQVehicleWeapon", "SQMapMarker"} <= names
    for _cls, kind, optional, tbl in tables:
        assert kind in {"Class", "ScriptStruct", "BlueprintGeneratedClass"}
        assert isinstance(optional, bool)
        assert tbl and all(isinstance(v, int) for v in tbl.values())


def test_types_resolve_paths_calls_optional_are_optional_here_too():
    """A type `resolve_paths` tolerates the absence of must not be reported as
    drift when it is absent — otherwise a layer with no emplacement built
    fails the health check and can trigger a pointless self-heal."""
    tables = {c: opt for c, _k, opt, _t in health.hardcoded_offset_tables()}
    assert tables.get("SQDeployableVehicle") is True
    assert tables.get("SQVehicleSeatConfig") is True
    # Core types stay required: their absence IS the drift signal.
    assert tables.get("SQDeployable") is False
    assert tables.get("SQVehicle") is False


def test_every_readable_hardcoded_offset_is_watched():
    """A hardcoded offset the reader can read THROUGH — used directly or as a
    reflection fallback — must be verifiable, or a Squad update moves the
    struct and the reader ships neighbouring bytes as data with doctor still
    green. Everything left out is named in `hardcoded_offset_tables`' register
    WITH a reason; this test is what keeps that register honest."""
    from sqreader.squad import snapshot as snap

    watched = {v for _c, _k, _o, tbl in health.hardcoded_offset_tables()
               for v in tbl.values()}
    # Each of these is in the register in health.hardcoded_offset_tables,
    # with the reason it cannot (yet) be checked by name.
    exempt = {
        # UPROPERTY names never seen in a reflection dump.
        "SQ_SEATCOMP_SEAT_CONFIG_OFFSET",
        "SQ_TURRET_INVENTORY_OFFSET",
        "SQ_INV_CURRENT_WEAPON_OFFSET",
        # Blueprint actors, no single UClass to check against.
        "AMMO_WEP_OFFSETS",
        # Private C++ members, not UPROPERTIES — reflection cannot see them;
        # validated by the value-based collector verdicts instead.
        "COLLECTOR_OFFSETS",
        # Verified by cmd_doctor's mode-aware lane-graph section instead.
        "LANE_GRAPH_OFFSETS", "LANE_LINK_NODEA_OFF", "LANE_LINK_NODEB_OFF",
        "LANE_LINK_SIZE", "LANE_VISUALIZER_ROUTE_INDEX_OFF",
        # Declared but never read — nothing can drift through them.
        "MARKER_ITEM_OFFSETS", "SQ_SEATCOMP_ANIM_STATE_OFFSET",
        "SQ_SEATCOMP_FORCE_OCCUPIED_OFFSET", "SQ_VEHCOMP_STATE_OFFSET",
        # Struct-internal: addressed relative to a struct, not a class, so a
        # class table cannot express them (see the register's last entry).
        "THI_ACTUAL_DAMAGE_OFFSET", "THI_SERVER_TIMESTAMP_OFFSET",
        "THI_DAMAGE_CAUSER_OFFSET", "THI_DAMAGE_TYPE_CLASS_OFFSET",
        "THI_FLAGS_OFFSET", "THI_PAWN_INSTIGATOR_OFFSET",
        "THI_POINT_DAMAGE_EVENT_OFFSET",
        "HR_BONE_NAME_OFFSET", "HR_DISTANCE_OFFSET",
        "MARKER_ARRAY_ITEMS_OFFSET", "MARKER_ITEMS_ABS_OFFSET",
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
    by_cls = {c: set(names) for c, _k, names in rows}
    # Medical capture and the commander-identity hop have no fallback
    # constants: a rename is invisible without these rows.
    assert "CurrentHeldItem" in by_cls["SQSoldier"]
    assert {"HealedTarget", "ItemCount"} <= by_cls["SQHealingEquipableItem"]
    assert "CurrentCommander" in by_cls["SQCommanderState"]
    for _cls, kind, names in rows:
        assert kind in {"Class", "ScriptStruct"} and names


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
                        lambda *a: {"state": "drift", "ok": False,
                                    "drift": [{"class": "X", "field": "y"}]})
    t = fleet.gather(pm=None, arr=None, alloc=None, build_sha="abc123",
                     restarts=2, uptime_sec=99.9, channel="beta")
    assert t["schema"] == "sqr-checkin-1"
    assert t["agent_version"] == __version__
    assert t["squad_build"] == "abc123"
    assert t["health"] == "drift" and t["restarts"] == 2 and t["channel"] == "beta"
    assert t["uptime_sec"] == 99                    # int-coerced
    assert len(t["drift"]) == 1


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
