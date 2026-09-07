"""Position sampler: correct pos/health extraction from a cached entity set, and
each staleness gate (no soldier / freed ClassPrivate / out-of-range health / no
position) silently OMITS exactly that entity and never raises.

Drones sample under those same gates plus the (0, 0, 0) drop, and carry two
keys nothing else does: `dead` and `lastHitBy`, each written only once it is
set (spec §7, decision D19). Absence there means "not set", never "unknown" —
the one place in the recording where those two are not the same thing, and the
reason the whole sample fits in ~115 B per drone.
"""
import struct
from types import SimpleNamespace

import pytest

from sqreader.squad import possample
from sqreader.squad.possample import SampledEntities, sample_positions
from sqreader.ue.uobject import UOBJ_CLASS_PRIVATE

PS = 0x1000
SOLDIER = 0x2000
CLASS = 0x9000        # a plausible heap class pointer
VH = 0x3000
DRONE = 0x4000
CTRL = 0x5000         # the drone's last hitter, a controller
HITTER_PS = 0x6000
SOLDIER_OFF = 0x10
HEALTH_OFF = 0x20
VH_HEALTH_OFF = 0x30
TEAM_OFF = 0x40
DEAD_OFF = 0x50       # `Dead`'s effective byte, sharing it with other bools
DEAD_MASK = 0x04
LAST_HIT_OFF = 0x60
CTRL_PS_OFF = 0x70
EOS_OFF = 0x80

HITTER_EOS = "eos-000000000000000000000000hitter"


def _paths():
    return SimpleNamespace(
        ps_offsets={"Soldier": SOLDIER_OFF, "OnlineUserId": EOS_OFF},
        soldier_offsets={"Health": HEALTH_OFF},
        vehicle_offsets={"Health": VH_HEALTH_OFF},
        sq_pawn_team_off=TEAM_OFF,
        pc_playerstate_off=CTRL_PS_OFF,
        drone_dead_mask=(DEAD_OFF, DEAD_MASK),
        drone_last_hit_by_off=LAST_HIT_OFF,
    )


class FakePM:
    def __init__(self, mem):
        self.mem = mem

    def try_read(self, addr, size):
        b = self.mem.get(addr)
        return b[:size] if b is not None and len(b) >= size else None

    def read_u64(self, addr):
        """The reader's controller hop reads through this, as the real
        ProcessMemory does; an unmapped address raises, as it does there."""
        b = self.try_read(addr, 8)
        if b is None:
            raise OSError(f"unmapped read at 0x{addr:x}")
        return struct.unpack("<Q", b)[0]


def _base_mem():
    return {
        PS + SOLDIER_OFF: struct.pack("<Q", SOLDIER),         # ps -> soldier
        SOLDIER + UOBJ_CLASS_PRIVATE: struct.pack("<Q", CLASS),
        SOLDIER + HEALTH_OFF: struct.pack("<f", 87.5),
        VH + UOBJ_CLASS_PRIVATE: struct.pack("<Q", CLASS),
        VH + VH_HEALTH_OFF: struct.pack("<f", 950.0),
        VH + TEAM_OFF: bytes([1]),
        DRONE + UOBJ_CLASS_PRIVATE: struct.pack("<Q", CLASS),
        DRONE + DEAD_OFF: bytes([0x00]),
        DRONE + LAST_HIT_OFF: struct.pack("<Q", 0),           # nothing has hit
        CTRL + CTRL_PS_OFF: struct.pack("<Q", HITTER_PS),
        HITTER_PS + EOS_OFF: struct.pack("<Qii", 0x7000, len(HITTER_EOS) + 1,
                                         len(HITTER_EOS) + 1),
        0x7000: (HITTER_EOS + "\0").encode("utf-16-le"),
    }


@pytest.fixture(autouse=True)
def _fake_pos(monkeypatch):
    # Isolate the gate logic from the (separately-tested) position read path.
    monkeypatch.setattr(possample, "read_root_pos_yaw",
                        lambda pm, addr, paths: {"position": {"x": 100.0, "y": 200.0, "z": 5.0},
                                                 "yaw": 90.0})


def _ents(drones=((DRONE, "0x4000"),)):
    return SampledEntities(full_tick=7, players=((PS, "eos-bob"),),
                           vehicles=((VH, "0x3000"),), drones=tuple(drones))


def test_healthy_player_and_vehicle_emitted():
    pm = FakePM(_base_mem())
    f = sample_positions(pm, _paths(), _ents(), tick=30, ts="2026-01-01T00:00:00Z")
    assert f["t"] == "pos" and f["tick"] == 30 and f["fullTick"] == 7
    assert f["players"] == [{"id": "eos-bob", "x": 100.0, "y": 200.0, "z": 5.0,
                             "h": 87.5, "yaw": 90.0}]
    # Vehicles carry z like soldiers do — a 3-D consumer without it renders
    # every vehicle glued to the ground plane.
    assert f["vehicles"] == [{"id": "0x3000", "x": 100.0, "y": 200.0, "z": 5.0,
                              "h": 950.0, "yaw": 90.0, "team": 1}]


def test_no_soldier_pointer_omits_player():
    mem = _base_mem()
    mem[PS + SOLDIER_OFF] = struct.pack("<Q", 0)          # ps -> null
    f = sample_positions(FakePM(mem), _paths(), _ents(), 30, "t")
    assert f["players"] == []


def test_freed_classprivate_omits_player():
    mem = _base_mem()
    mem[SOLDIER + UOBJ_CLASS_PRIVATE] = struct.pack("<Q", 0)   # freed slot
    f = sample_positions(FakePM(mem), _paths(), _ents(), 30, "t")
    assert f["players"] == []


def test_out_of_range_health_omits_player():
    mem = _base_mem()
    mem[SOLDIER + HEALTH_OFF] = struct.pack("<f", 9999.0)     # impossible HP = stale
    f = sample_positions(FakePM(mem), _paths(), _ents(), 30, "t")
    assert f["players"] == []


def test_missing_position_omits(monkeypatch):
    monkeypatch.setattr(possample, "read_root_pos_yaw", lambda *a: {})   # root unreadable
    f = sample_positions(FakePM(_base_mem()), _paths(), _ents(), 30, "t")
    assert f["players"] == [] and f["vehicles"] == []


def test_insane_coord_omits(monkeypatch):
    monkeypatch.setattr(possample, "read_root_pos_yaw",
                        lambda *a: {"position": {"x": 9e9, "y": 1.0, "z": 0.0}})
    f = sample_positions(FakePM(_base_mem()), _paths(), _ents(), 30, "t")
    assert f["players"] == []


def test_from_snapshot_parses_addrs_and_keys():
    snap = {"tick": 42,
            "players": [{"_addr": "0x1000", "eosId": "eos-x", "name": "Bob"},
                        {"_addr": "0x2000", "name": "NoEos"},   # key falls back to name
                        {"name": "NoAddr"}],                     # dropped (no _addr)
            "vehicles": [{"id": "0x3000"}, {"foo": 1}],          # 2nd dropped (no id)
            "drones": [{"id": "0x4000"}, {"class": "no id"}]}    # 2nd dropped
    ent = SampledEntities.from_snapshot(snap)
    assert ent.full_tick == 42
    assert ent.players == ((0x1000, "eos-x"), (0x2000, "NoEos"))
    assert ent.vehicles == ((0x3000, "0x3000"),)
    assert ent.drones == ((0x4000, "0x4000"),)


def test_from_snapshot_of_a_frame_with_no_drones():
    """Almost every frame: the full build writes `drones` only while a pawn
    exists, so the key is simply not there and the sample has none to follow."""
    ent = SampledEntities.from_snapshot({"tick": 1, "players": [],
                                         "vehicles": []})
    assert ent.drones == ()


# ---- drones (spec §7) ------------------------------------------------------

def test_live_drone_carries_position_and_neither_conditional_key():
    """A drone in the air: `{id, x, y, z, yaw}` and nothing else. No `h` — its
    health changes only at death, which `dead` says — and no `team`, which the
    pawn does not carry. `dead` is absent while the bit reads clear and
    `lastHitBy` while nothing has hit it (every one of 1,619 live rows, 09-05):
    absence here is "not set", and the full frame carries the two-way reading."""
    f = sample_positions(FakePM(_base_mem()), _paths(), _ents(), 30, "t")
    assert f["drones"] == [{"id": "0x4000", "x": 100.0, "y": 200.0, "z": 5.0,
                            "yaw": 90.0}]


def test_a_dead_drone_and_its_killer_add_exactly_two_keys():
    """The kill: `Dead` set and `LastHitBy` naming the shooter's controller in
    the same 100 ms sample (2026-09-07). The killer is the `lastHitBy` of the
    first sample carrying `dead`, exact to a quarter second — a second shooter
    moved the pointer 1.1 s later, inside the full frame's one-second gap."""
    mem = _base_mem()
    mem[DRONE + DEAD_OFF] = bytes([DEAD_MASK])
    mem[DRONE + LAST_HIT_OFF] = struct.pack("<Q", CTRL)
    f = sample_positions(FakePM(mem), _paths(), _ents(), 30, "t")
    assert f["drones"] == [{"id": "0x4000", "x": 100.0, "y": 200.0, "z": 5.0,
                            "yaw": 90.0, "dead": True,
                            "lastHitBy": HITTER_EOS}]


def test_the_dead_bit_is_read_through_its_mask():
    """`Dead` shares its byte with the class's other bools, so a neighbour
    being set is not this drone being dead."""
    mem = _base_mem()
    mem[DRONE + DEAD_OFF] = bytes([0xFF ^ DEAD_MASK])
    f = sample_positions(FakePM(mem), _paths(), _ents(), 30, "t")
    assert "dead" not in f["drones"][0]


def test_a_hit_that_reaches_no_player_state_omits_the_key():
    """A pointer that does not resolve names nobody — never a guess, and never
    an empty string in the file."""
    mem = _base_mem()
    mem[DRONE + LAST_HIT_OFF] = struct.pack("<Q", CTRL)
    mem[CTRL + CTRL_PS_OFF] = struct.pack("<Q", 0)
    f = sample_positions(FakePM(mem), _paths(), _ents(), 30, "t")
    assert "lastHitBy" not in f["drones"][0]


def test_a_freed_drone_pawn_is_omitted_from_the_sample():
    """The same ClassPrivate gate every other entity passes: a drone that has
    been freed since the last full frame is left out, never nulled."""
    mem = _base_mem()
    mem[DRONE + UOBJ_CLASS_PRIVATE] = struct.pack("<Q", 0)
    f = sample_positions(FakePM(mem), _paths(), _ents(), 30, "t")
    assert "drones" not in f


def test_a_drone_zeroed_to_the_map_origin_is_dropped(monkeypatch):
    """A dead pawn's final tick reads exactly (0, 0, 0) before the pawn is
    freed (09-05) — the same junk rule the full frame's list applies, and it is
    the drones' own: the sampler's older gates are finiteness and magnitude,
    and a player or a vehicle at the origin still passes them here."""
    monkeypatch.setattr(possample, "read_root_pos_yaw",
                        lambda pm, addr, paths: {
                            "position": {"x": 0.0, "y": 0.0, "z": 0.0}})
    f = sample_positions(FakePM(_base_mem()), _paths(), _ents(), 30, "t")
    assert "drones" not in f
    assert len(f["players"]) == 1 and len(f["vehicles"]) == 1


def test_no_drones_means_no_key_at_all():
    """What every line recorded before this one says, and what almost every
    line records now: nothing in the air, so no key."""
    f = sample_positions(FakePM(_base_mem()), _paths(), _ents(drones=()),
                         30, "t")
    assert "drones" not in f
    assert f["players"] and f["vehicles"]        # the rest of the line stands


def test_unresolved_drone_offsets_leave_the_position_intact():
    """`Dead` and `LastHitBy` are resolved once at startup; a build where
    neither answered still samples the drone's position, and simply never
    writes the two keys."""
    paths = _paths()
    paths.drone_dead_mask = None
    paths.drone_last_hit_by_off = None
    mem = _base_mem()
    mem[DRONE + DEAD_OFF] = bytes([DEAD_MASK])
    mem[DRONE + LAST_HIT_OFF] = struct.pack("<Q", CTRL)
    f = sample_positions(FakePM(mem), paths, _ents(), 30, "t")
    assert f["drones"] == [{"id": "0x4000", "x": 100.0, "y": 200.0, "z": 5.0,
                            "yaw": 90.0}]
