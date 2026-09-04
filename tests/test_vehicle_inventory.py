"""The seat-inventory walk: every weapon a vehicle seat holds, not only the
one CurrentWeapon names.

Laid out from the live Loach CAS Small (Squad v10.5.3, 2026-09-03):
SQPawnInventoryComponent.Inventory is a TArray<FSQWeaponGroupData> (40-byte
elements: Weapons TArray<SQEquipableItem*> @+0x10, Index @+0x20), and each
weapon carries Magazines as TArray<FMagData{Max, Cur}> at
SQ_VWEAPON_MAGAZINES_OFFSET. The fake
memory below is that shape byte for byte; the reads go through the same
FakeProcessMemory the rest of the reader tests use."""
from __future__ import annotations

import struct
from types import SimpleNamespace

from sqreader.squad import snapshot as sn
from sqreader.ue.uobject import UOBJ_CLASS_PRIVATE, UOBJ_NAME_PRIVATE

from conftest import FakeProcessMemory


class _Pm(FakeProcessMemory):
    """FakeProcessMemory plus the typed reads _safe() lambdas use."""

    def read_u64(self, addr: int) -> int:
        return struct.unpack("<Q", self.read(addr, 8))[0]


class _Alloc:
    """FName pool stand-in: comparison index -> string."""

    def __init__(self, names: dict[int, str]) -> None:
        self._names = names

    def fname_to_str(self, ci: int, num: int = 0) -> str:
        return self._names.get(ci, "None")


INV = 0x100000          # the SQPawnInventoryComponent instance
GROUPS = 0x200000       # Inventory.data (FSQWeaponGroupData elements)
WPTR_A = 0x300000       # group 0's Weapons.data
WPTR_B = 0x300100       # group 1's Weapons.data
GUN_A = 0x400000        # SQEquipableItem instances (GUN_A is 0x800 bytes wide,
GUN_B = 0x410000        # so GUN_B sits clear of it — segments must not overlap)
CLS_A = 0x500000        # their UClass objects
CLS_B = 0x500100
MAGS_A = 0x600000       # GUN_A's Magazines.data
VEHICLE = 0x700000


def _tarray(ptr: int, count: int, cap: int | None = None) -> bytes:
    return struct.pack("<Qii", ptr, count, count if cap is None else cap)


def _uobject(class_addr: int) -> bytes:
    """Enough of a UObject header to resolve its class: ClassPrivate @+0x10."""
    b = bytearray(0x20)
    struct.pack_into("<Q", b, UOBJ_CLASS_PRIVATE, class_addr)
    return bytes(b)


def _uclass(name_ci: int) -> bytes:
    b = bytearray(0x20)
    struct.pack_into("<II", b, UOBJ_NAME_PRIVATE, name_ci, 0)
    return bytes(b)


def _group(weapons_ptr: int, count: int, index: int, stride: int = 40,
           weapons_off: int = 0x10, index_off: int = 0x20) -> bytes:
    b = bytearray(stride)
    b[weapons_off:weapons_off + 16] = _tarray(weapons_ptr, count, 4)
    struct.pack_into("<i", b, index_off, index)
    return bytes(b)


def _memory(stride: int = 40, weapons_off: int = 0x10,
            index_off: int = 0x20, inventory_off: int | None = None) -> _Pm:
    # Buffer sizes and write positions derive from the live constants, so a
    # doctor-dictated refresh never silently writes the fake structures past
    # a fixed-size buffer's end (slice assignment clamps, and every read
    # comes back None) — the 2026-09-04 refresh did exactly that to the
    # original literals.
    if inventory_off is None:
        inventory_off = sn.SQ_INV_INVENTORY_OFFSET
    inv = bytearray(max(inventory_off, sn.SQ_INV_CURRENT_WEAPON_OFFSET) + 0x20)
    inv[inventory_off:inventory_off + 16] = _tarray(GROUPS, 2, 4)
    struct.pack_into("<Q", inv, sn.SQ_INV_CURRENT_WEAPON_OFFSET, GUN_A)
    groups = (_group(WPTR_A, 1, 0, stride, weapons_off, index_off)
              + _group(WPTR_B, 2, 3, stride, weapons_off, index_off))
    gun_a = bytearray(sn.SQ_VWEAPON_MAGAZINES_OFFSET + 0x20)
    gun_a[:0x20] = _uobject(CLS_A)
    gun_a[sn.SQ_VWEAPON_MAGAZINES_OFFSET:sn.SQ_VWEAPON_MAGAZINES_OFFSET + 16] = \
        _tarray(MAGS_A, 2)
    vehicle = bytearray(sn.SQ_TURRET_INVENTORY_OFFSET + 0x10)
    struct.pack_into("<Q", vehicle, sn.SQ_TURRET_INVENTORY_OFFSET, INV)
    return _Pm({
        INV: bytes(inv),
        GROUPS: groups,
        WPTR_A: struct.pack("<Q", GUN_A),
        # A null slot in the middle of a group is skipped, not a crash.
        WPTR_B: struct.pack("<QQ", GUN_B, 0),
        GUN_A: bytes(gun_a),
        GUN_B: _uobject(CLS_B),            # no Magazines mapped -> no ammo keys
        CLS_A: _uclass(1),
        CLS_B: _uclass(2),
        MAGS_A: struct.pack("<iiii", 7, 0, 7, 7),   # (Max, Cur) x 2
        VEHICLE: bytes(vehicle),
    })


ALLOC = _Alloc({1: "BP_Hydra_Loach_Small_Single_Right_C",
                2: "BP_M134_Loach_Dual_C"})


def test_every_group_and_weapon_is_read_with_its_own_ammo():
    pm = _memory()
    got = sn.read_inventory_weapons(pm, ALLOC, INV, None, GUN_A)
    assert got == [
        {"weaponClass": "BP_Hydra_Loach_Small_Single_Right_C", "group": 0,
         "active": True, "magazines": [0, 7], "magazinesMax": [7, 7]},
        {"weaponClass": "BP_M134_Loach_Dual_C", "group": 3},
    ]


def test_active_is_only_stamped_on_the_current_weapon():
    pm = _memory()
    got = sn.read_inventory_weapons(pm, ALLOC, INV, None, GUN_B)
    assert [w.get("active") for w in got] == [None, True]
    got = sn.read_inventory_weapons(pm, ALLOC, INV, None, 0)
    assert all("active" not in w for w in got)


def test_the_live_struct_layout_outranks_the_constants():
    """resolve_paths reads the group struct's field offsets and size off the
    binary; a build that grew FSQWeaponGroupData must be walked at the live
    stride, not the baked one."""
    pm = _memory(stride=48, weapons_off=0x18, index_off=0x28,
                 inventory_off=0x1c0)
    paths = SimpleNamespace(inventory_offsets={"Inventory": 0x1c0},
                            weapon_group_offsets={"Weapons": 0x18,
                                                  "Index": 0x28},
                            weapon_group_size=48)
    got = sn.read_inventory_weapons(pm, ALLOC, INV, paths, GUN_A)
    assert [w["group"] for w in got] == [0, 3]
    # The baked layout read against the same memory finds nothing, which is
    # the honest answer (no-guess), not a shifted junk read.
    assert sn.read_inventory_weapons(pm, ALLOC, INV, None, GUN_A) == []


def test_class_names_are_cached_by_class_object_not_weapon_address():
    """A vehicle dies, its weapons are freed, and a respawn may reuse the
    address for a different gun before the rolling cache reset. The class
    pointer is therefore read live every tick; only the class object's own
    name is cached, keyed by the class address."""
    pm = _memory()
    caches = SimpleNamespace(class_name={})
    sn.read_inventory_weapons(pm, ALLOC, INV, None, GUN_A, caches=caches)
    assert caches.class_name == {CLS_A: "BP_Hydra_Loach_Small_Single_Right_C",
                                 CLS_B: "BP_M134_Loach_Dual_C"}
    # The same address now holds a gun of the other class (respawn reuse):
    # the record follows the live class pointer, not the stale address.
    pm.add(GUN_B, _uobject(CLS_A))
    pm._segments.reverse()               # the new segment must win the lookup
    got = sn.read_inventory_weapons(pm, ALLOC, INV, None, GUN_A, caches=caches)
    assert [w["weaponClass"] for w in got] == [
        "BP_Hydra_Loach_Small_Single_Right_C",
        "BP_Hydra_Loach_Small_Single_Right_C"]


def test_unreadable_inventory_is_empty_not_a_guess():
    pm = _Pm({INV: bytes(0x400)})           # zeroed header: no groups
    assert sn.read_inventory_weapons(pm, ALLOC, INV, None, 0) == []
    assert sn.read_inventory_weapons(_Pm(), ALLOC, INV, None, 0) == []


def test_driver_record_rides_the_vehicle_actor_inventory():
    """The pilot / driver seat IS the vehicle actor: its inventory hangs off
    the vehicle at CachedVehicleInventory, and the record is stamped as the
    driver seat so the viewer never pools it with the turrets."""
    pm = _memory()
    rec = sn.read_driver_weapons(pm, ALLOC, VEHICLE, None, "BP_Loach_CAS_Small_C")
    assert rec is not None
    assert rec["seat"] == "driver"
    assert rec["className"] == "BP_Loach_CAS_Small_C"
    assert rec["weaponClass"] == "BP_Hydra_Loach_Small_Single_Right_C"
    assert rec["magazines"] == [0, 7] and rec["magazinesMax"] == [7, 7]
    assert [w["weaponClass"] for w in rec["weapons"]] == [
        "BP_Hydra_Loach_Small_Single_Right_C", "BP_M134_Loach_Dual_C"]
    assert "yaw" not in rec


def test_no_inventory_means_no_driver_record():
    assert sn.read_driver_weapons(_Pm(), ALLOC, VEHICLE, None, "X") is None
    pm = _Pm({VEHICLE: bytes(0x500)})       # null CachedVehicleInventory
    assert sn.read_driver_weapons(pm, ALLOC, VEHICLE, None, "X") is None


def test_magazines_decode_as_current_and_max_lists():
    pm = _memory()
    assert sn._read_magazines(pm, GUN_A) == ([0, 7], [7, 7])
    assert sn._read_magazines(pm, GUN_B) is None
