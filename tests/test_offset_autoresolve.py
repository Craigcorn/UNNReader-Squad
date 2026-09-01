"""Re-deriving drifted offsets from the running binary.

Squad v10.5.3 moved most of the struct fields these constants point at. The
reader did not crash, did not warn, and did not stop recording — it wrote
matches in which every position was `{x: junk, y: 0, z: 1}`, for eleven hours,
while /health stayed green and the tick loop reported 100+ players. These
tests exist because "wrong but plausible" is the failure mode that costs the
most and announces itself the least.
"""
import struct

import pytest

from sqreader.squad import snapshot as sn


class _Field:
    """What get_class_layout yields — only .offset is read here."""

    def __init__(self, offset):
        self.offset = offset


@pytest.fixture(autouse=True)
def _restore_offsets():
    """Every test here rewrites module globals. Put them back."""
    sn._bake_offsets()
    yield
    sn.revert_offset_overrides()


def _layouts(monkeypatch, table):
    """Pretend the live binary reports `table` = {class: {field: offset}}."""
    seen = {}

    def fake_layout(pm, addr, alloc):
        return {n: _Field(o) for n, o in table.get(seen.get(addr, ""), {}).items()}

    found = {}
    for i, cls in enumerate(table, start=1):
        addr = 0x1000 + i
        found[cls] = (None, addr)
        seen[addr] = cls
    monkeypatch.setattr(sn, "get_class_layout", fake_layout)
    return found


# --- what the binary says wins -------------------------------------------
#
# The pretend "moved" layouts are derived from the CURRENT constants rather
# than written as literals. This fork refreshes its source constants to the
# live layout after every drift report, and a literal equal to the refreshed
# value is a test that moves nothing and asserts nothing — upstream's
# originals went vacuous here the day of the 2026-08-31 refresh.

def test_a_moved_field_is_followed(monkeypatch):
    to = sn.SQ_VEHCOMP_HEALTH_OFFSET + 0x30
    found = _layouts(monkeypatch, {
        "SQVehicleComponent": {"Health": to},
    })
    moved = sn.autoresolve_offsets(None, found, None)
    assert sn.SQ_VEHCOMP_HEALTH_OFFSET == to
    assert moved["SQ_VEHCOMP_HEALTH_OFFSET"][1] == to, \
        "the correction has to be reported — a silent one is unreviewable"


def test_dict_offsets_follow_too(monkeypatch):
    before = dict(sn.RALLY_OFFSETS)
    found = _layouts(monkeypatch, {
        "SQSquadRallyPoint": {k: v + 0x30 for k, v in before.items()},
    })
    sn.autoresolve_offsets(None, found, None)
    assert sn.RALLY_OFFSETS["Team"] == before["Team"] + 0x30
    assert sn.RALLY_OFFSETS["NumberOfSpawns"] == before["NumberOfSpawns"] + 0x30


def test_nothing_moves_when_nothing_moved(monkeypatch):
    before = dict(sn.RALLY_OFFSETS)
    found = _layouts(monkeypatch, {"SQSquadRallyPoint": dict(before)})
    assert sn.autoresolve_offsets(None, found, None) == {}
    assert sn.RALLY_OFFSETS == before


# --- the fields reflection cannot see ------------------------------------

def test_an_unreflected_neighbour_travels_with_its_anchor(monkeypatch):
    """SQVehicleSeatComponent's anim-state int is a C++ member 8 bytes below
    SeatPawn. Reflection never reports it, so it moves by the declared gap
    when its anchor moves — otherwise it would be read out of whatever
    field the update slid underneath it."""
    to = sn.SQ_SEATCOMP_SEAT_PAWN_OFFSET + 0x30
    found = _layouts(monkeypatch, {"SQVehicleSeatComponent": {"SeatPawn": to}})
    sn.autoresolve_offsets(None, found, None)
    assert sn.SQ_SEATCOMP_ANIM_STATE_OFFSET == to - 0x8


def test_an_anchor_that_did_not_move_leaves_its_neighbour_alone(monkeypatch):
    before = sn.SQ_SEATCOMP_ANIM_STATE_OFFSET
    found = _layouts(monkeypatch, {
        "SQVehicleSeatComponent": {"SeatPawn": sn.SQ_SEATCOMP_SEAT_PAWN_OFFSET}})
    sn.autoresolve_offsets(None, found, None)
    assert sn.SQ_SEATCOMP_ANIM_STATE_OFFSET == before


def test_a_dict_anchored_constant_rides_its_entry(monkeypatch):
    """The deployable placer slots are unnamed privates a fixed distance past
    DEPLOYABLE_OFFSETS["Health"]. When the dict entry moves, they move by the
    declared gap — this is the fork's replacement for upstream's runtime
    baked-vs-live shift, which is zero by definition in a refreshed table."""
    to = sn.DEPLOYABLE_OFFSETS["Health"] + 0x30
    ps_before = sn.SQ_DEPLOYABLE_PLACER_PS_OFFSET
    found = _layouts(monkeypatch, {"SQDeployable": {"Health": to}})
    sn.autoresolve_offsets(None, found, None)
    assert sn.SQ_DEPLOYABLE_PLACER_PS_OFFSET == ps_before + 0x30
    assert sn.SQ_DEPLOYABLE_PLACER_CTRL_OFFSET == \
        sn.SQ_DEPLOYABLE_PLACER_PS_OFFSET - 0x8


def test_a_named_field_needs_no_anchor(monkeypatch):
    """VehicleComponentState looked like a raw member until live reflection
    named it (2026-09-01). As a reflected scalar the binary answers for the
    field itself — even when its old anchor, Health, is absent entirely."""
    to = sn.SQ_VEHCOMP_STATE_OFFSET + 0x30
    found = _layouts(monkeypatch, {
        "SQVehicleComponent": {"VehicleComponentState": to}})
    moved = sn.autoresolve_offsets(None, found, None)
    assert sn.SQ_VEHCOMP_STATE_OFFSET == to
    assert moved["SQ_VEHCOMP_STATE_OFFSET"][1] == to


def test_declared_gaps_still_match_the_table():
    """Generation consistency: every anchored constant must sit exactly its
    declared gap from its anchor IN THE SOURCE TABLE. A source refresh that
    moves an anchor without its never-read neighbour reopens the trap this
    fork already fell into once (a stale ANIM_STATE landing on the refreshed
    SeatedPlayer offset) — this is the test that makes a half-refresh loud."""
    g = vars(sn)
    for name, anchor, gap in sn._ANCHORED_SCALARS:
        assert g[name] - g[anchor] == gap, (name, anchor)
    for name, dict_name, key, gap in sn._ANCHORED_TO_DICT_ENTRY:
        assert g[name] - g[dict_name][key] == gap, (name, dict_name, key)


# --- a served pack outranks the binary -----------------------------------

def test_a_pack_override_outranks_the_binary(monkeypatch):
    """A pack is operator intent, served precisely for the cases reflection
    answers wrongly. autoresolve must not rewrite a packed name — the
    self-heal gates judge whether the PACK cleared the drift, and that
    verdict is unreadable if the binary's answer lands underneath it."""
    packed = sn.SQ_VEHCOMP_HEALTH_OFFSET + 0x100
    sn.apply_offset_overrides({"SQ_VEHCOMP_HEALTH_OFFSET": packed,
                               "RALLY_OFFSETS.Team": 0x999})
    found = _layouts(monkeypatch, {
        "SQVehicleComponent": {"Health": packed + 0x30},
        "SQSquadRallyPoint": {"Team": 0x444},
    })
    moved = sn.autoresolve_offsets(None, found, None)
    assert sn.SQ_VEHCOMP_HEALTH_OFFSET == packed
    assert sn.RALLY_OFFSETS["Team"] == 0x999
    assert "SQ_VEHCOMP_HEALTH_OFFSET" not in moved
    assert "RALLY_OFFSETS.Team" not in moved


def test_a_reverted_pack_hands_the_name_back(monkeypatch):
    """Once the pack is rolled back the binary answers again — otherwise a
    bad pack would leave its names frozen at the baked values forever."""
    sn.apply_offset_overrides(
        {"SQ_VEHCOMP_HEALTH_OFFSET": sn.SQ_VEHCOMP_HEALTH_OFFSET + 0x100})
    sn.revert_offset_overrides()
    to = sn.SQ_VEHCOMP_HEALTH_OFFSET + 0x30
    found = _layouts(monkeypatch, {"SQVehicleComponent": {"Health": to}})
    moved = sn.autoresolve_offsets(None, found, None)
    assert sn.SQ_VEHCOMP_HEALTH_OFFSET == to
    assert "SQ_VEHCOMP_HEALTH_OFFSET" in moved


# --- refusing to make things worse ---------------------------------------

def test_an_absent_class_changes_nothing(monkeypatch):
    """Classes spawn per map. An early tick that sees none must not be read
    as 'every offset is now zero'."""
    before = sn.SQ_VEHCOMP_HEALTH_OFFSET
    monkeypatch.setattr(sn, "get_class_layout", lambda *a: {})
    assert sn.autoresolve_offsets(None, {}, None) == {}
    assert sn.SQ_VEHCOMP_HEALTH_OFFSET == before


def test_an_absurd_offset_is_refused(monkeypatch):
    before = sn.SQ_VEHCOMP_HEALTH_OFFSET
    found = _layouts(monkeypatch, {"SQVehicleComponent": {"Health": 0x9999999}})
    sn.autoresolve_offsets(None, found, None)
    assert sn.SQ_VEHCOMP_HEALTH_OFFSET == before, \
        "a torn read of the reflection data must not become the new truth"


def test_a_throwing_layout_is_survivable(monkeypatch):
    before = sn.SQ_VEHCOMP_HEALTH_OFFSET

    def boom(*a):
        raise OSError("EIO")
    monkeypatch.setattr(sn, "get_class_layout", boom)
    assert sn.autoresolve_offsets(None, {"SQVehicleComponent": (None, 1)},
                                  None) == {}
    assert sn.SQ_VEHCOMP_HEALTH_OFFSET == before


# --- ComponentToWorld, which has no reflection at all ---------------------

class _FakeMem:
    """A handful of USceneComponents laid out in a dict of bytes."""

    def __init__(self, objects):
        self.objects = objects        # {base: bytes}

    def _find(self, addr):
        for base, buf in self.objects.items():
            if base <= addr < base + len(buf):
                return buf, addr - base
        raise OSError("unmapped")

    def read(self, addr, n):
        buf, off = self._find(addr)
        if off + n > len(buf):
            raise OSError("short")
        return buf[off:off + n]

    def read_u64(self, addr):
        return struct.unpack("<Q", self.read(addr, 8))[0]


def _paths(rel=0x140, attach=0xC8, c2w=0x200):
    p = sn.SnapshotPaths.__new__(sn.SnapshotPaths)
    p.scene_relative_location_off = rel
    p.scene_attach_parent_off = attach
    p.scene_component_to_world_translation_off = c2w
    p.scene_component_to_world_rotation_off = c2w - 0x20
    p.actor_root_component_off = 0x1C0
    p.component_to_world_verified = False
    return p


def _component(pos, *, world_at, attached=False, size=0x700):
    buf = bytearray(size)
    struct.pack_into("<Q", buf, 0xC8, 0x4141 if attached else 0)
    struct.pack_into("<3d", buf, 0x140, *pos)
    struct.pack_into("<3d", buf, world_at, *pos)
    return bytes(buf)


def test_the_world_transform_is_found_where_it_actually_is():
    objs = {}
    for i in range(8):
        pos = (10000.0 + i * 777, -20000.0 - i * 333, 5000.0 + i)
        objs[0x100000 + i * 0x1000] = _component(pos, world_at=0x200)
    pm = _FakeMem(objs)
    got = sn.discover_component_to_world(pm, _paths(c2w=0x210), list(objs))
    assert got == 0x200


def test_attached_components_are_not_used_as_evidence():
    """For an attached component the world transform is NOT the relative one,
    so it proves nothing and must not be allowed to vote."""
    objs = {0x100000 + i * 0x1000:
            _component((1000.0 + i, 2000.0, 3000.0), world_at=0x200,
                       attached=True)
            for i in range(8)}
    pm = _FakeMem(objs)
    assert sn.discover_component_to_world(pm, _paths(c2w=0x210),
                                          list(objs)) is None


def test_too_few_samples_is_not_an_answer():
    objs = {0x100000: _component((1.0e4, 2.0e4, 3.0e3), world_at=0x200)}
    pm = _FakeMem(objs)
    assert sn.discover_component_to_world(pm, _paths(c2w=0x210),
                                          list(objs)) is None


def test_disagreement_yields_nothing_rather_than_a_majority_guess():
    """Half the components saying 0x200 and half saying 0x280 means the search
    is wrong about something. Keeping the old offset is recoverable; writing a
    guess into an archive is not."""
    objs = {}
    for i in range(8):
        pos = (10000.0 + i * 777, -20000.0 - i * 333, 5000.0 + i)
        objs[0x100000 + i * 0x1000] = _component(
            pos, world_at=0x200 if i % 2 else 0x280)
    pm = _FakeMem(objs)
    assert sn.discover_component_to_world(pm, _paths(c2w=0x210),
                                          list(objs)) is None


def test_a_component_at_the_origin_is_ignored():
    """(0,0,0) matches every zeroed run of 24 bytes in the object."""
    objs = {0x100000 + i * 0x1000: _component((0.0, 0.0, 0.0), world_at=0x200)
            for i in range(8)}
    pm = _FakeMem(objs)
    assert sn.discover_component_to_world(pm, _paths(c2w=0x210),
                                          list(objs)) is None


def _world(actor_base, comp_base, comp_bytes, root_off=0x1C0):
    """An actor whose RootComponent points at a component."""
    actor = bytearray(0x200)
    struct.pack_into("<Q", actor, root_off, comp_base)
    return {actor_base: bytes(actor), comp_base: comp_bytes}


def test_a_correct_offset_is_confirmed_and_never_scanned_again():
    objs = {}
    actors = []
    for i in range(8):
        a, c = 0x200000 + i * 0x2000, 0x300000 + i * 0x2000
        pos = (10000.0 + i * 777, -20000.0 - i * 333, 5000.0 + i)
        objs.update(_world(a, c, _component(pos, world_at=0x200)))
        actors.append(a)
    paths = _paths(c2w=0x200)
    sn.verify_component_to_world(_FakeMem(objs), paths, actors)
    assert paths.component_to_world_verified
    assert paths.scene_component_to_world_translation_off == 0x200


def test_a_wrong_offset_is_corrected_end_to_end():
    objs = {}
    actors = []
    for i in range(8):
        a, c = 0x200000 + i * 0x2000, 0x300000 + i * 0x2000
        pos = (10000.0 + i * 777, -20000.0 - i * 333, 5000.0 + i)
        objs.update(_world(a, c, _component(pos, world_at=0x1F0)))
        actors.append(a)
    paths = _paths(c2w=0x200)          # stale: the field is at 0x1f0 now
    sn.verify_component_to_world(_FakeMem(objs), paths, actors)
    assert paths.scene_component_to_world_translation_off == 0x1F0
    assert paths.scene_component_to_world_rotation_off == 0x1F0 - 0x20, \
        "rotation must follow translation or yaw and position disagree"


def test_an_empty_world_defers_rather_than_deciding():
    """Called on the first tick of a map load there may be no actors yet.
    That is not evidence the offset is wrong."""
    paths = _paths(c2w=0x200)
    sn.verify_component_to_world(_FakeMem({}), paths, [])
    assert not paths.component_to_world_verified, \
        "deciding on no evidence would lock in whatever was there"
    assert paths.scene_component_to_world_translation_off == 0x200
