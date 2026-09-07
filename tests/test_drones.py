"""`drones` — one entry per live drone pawn (spec §7).

Membership is the only thing a class decides here: a pawn is in the list when
its SuperStruct chain reaches the native `SQFlyingDrone`. What the entry
CARRIES is read off the pawn's own class layout, so the image below carries
real reflection data — UClass objects with linked FProperty chains and the
chain the box reflected on 2026-09-05, `BP_FlyingDrone_Recoverable_C` ->
`BP_FlyingDrone_C` -> `SQFlyingDrone` -> `Pawn`. Each class declares its own
names at its own level: `PlayerState` and `LastHitBy` are `Pawn`'s and reach
the drone by inheritance, `SQFlyingDrone` adds none, the commander drone's
Blueprint declares the owner, the health component, the death flag and the
calling action, and only the recon subclass declares a battery.

Both drones are laid out over the SAME bytes, battery included. What comes out
is decided by the class's properties alone — which is why the commander drone
emits no `batteryLifetimeMax` while the value sits in its instance at the very
offset the recon drone reads it from.

The absence rules of spec §2 are the other half. A key is emitted when the read
succeeds, `null` when the game's own value is empty (a drone nobody is flying,
a recon drone's `Command Action`, a pawn nothing has hit yet), and ABSENT when
the recorder could not read — a name the class does not carry, or a pointer
that reaches no player state. So the rename tests drop the property from the
class rather than a key from a dict.
"""
from __future__ import annotations

import math
import struct
from types import SimpleNamespace

import pytest

from sqreader.squad import snapshot as sn
from sqreader.ue.uobject import UOBJ_CLASS_PRIVATE, UOBJ_NAME_PRIVATE

from conftest import FakeProcessMemory

# ---- the reflection object layouts the walker reads ------------------------

USTRUCT_SUPER = 0x40
USTRUCT_CHILD_PROPS = 0x50
USTRUCT_PROPS_SIZE = 0x58
USTRUCT_MIN_ALIGN = 0x5C
FFIELD_CLASS = 0x08
FFIELD_NEXT = 0x18
FFIELD_NAME = 0x20
FPROP_ARRAY_DIM = 0x30
FPROP_ELEM_SIZE = 0x34
FPROP_OFFSET = 0x44
FPROP_BOOL = 0x70            # FBoolProperty's FieldSize/ByteOffset/masks

# ---- this fixture's own instance layout ------------------------------------

DRONE_SIZE = 0x800
ROOT_COMPONENT_OFF = 0x160

PLAYER_STATE_OFF = 0x2D8     # Pawn's three, inherited by every drone
CONTROLLER_OFF = 0x2E8       # read by nobody: the pilot is `PlayerState`
LAST_HIT_BY_OFF = 0x300
SQ_PC_OFF = 0x700            # BP_FlyingDrone_C's four
HEALTH_COMPONENT_OFF = 0x708
DEAD_OFF = 0x710
COMMAND_ACTION_OFF = 0x718
MAX_FLY_HEIGHT_OFF = 0x720   # read by nobody: spec §10 leaves it out
BATTERY_OFF = 0x7C0          # BP_FlyingDrone_Recoverable_C's one

COMPONENT_SIZE = 0x200
TO_WORLD_ROTATION_OFF = 0x80
TO_WORLD_TRANSLATION_OFF = 0xA0
RELATIVE_ROTATION_OFF = 0xC0
ATTACH_PARENT_OFF = 0xE0

HEALTH_OFF = 0xB4            # on the health component, not on the pawn
MAX_HEALTH_OFF = 0x120

PS_NAME_OFF = 0x10
PS_EOS_OFF = 0x30
CTRL_PS_OFF = 0x50

POSITION = (12345.5, -6789.25, 4200.0)
YAW = 45.0

# ---- the values every drone's bytes carry ----------------------------------

HEALTH = 15.0                # 15 / 15 on every live recon row (09-05)
MAX_HEALTH = 15.0
BATTERY = 100.0              # the recon kit's flight budget, in seconds
MAX_FLY_HEIGHT = 2200.0      # constant, meaning unresolved, never recorded

PILOT_EOS = "eos-0000000000000000000000000pilot"
OWNER_EOS = "eos-0000000000000000000000000owner"
HITTER_EOS = "eos-000000000000000000000000hitter"

# ---- the classes, as §13 reflects them -------------------------------------

PAWN = "Pawn"
BASE = "SQFlyingDrone"
COMMANDER_DRONE = "BP_FlyingDrone_C"
RECON_DRONE = "BP_FlyingDrone_Recoverable_C"
COMPONENT_CLASS = "HealthComponent_C"
ACTION = "CommandAction_Drone_C"
OUTSIDER = "BP_Soldier_C"    # a Pawn that is not one of ours

PAWN_PROPS = [
    {"name": "PlayerState", "type_name": "ObjectProperty",
     "offset": PLAYER_STATE_OFF},
    {"name": "Controller", "type_name": "ObjectProperty",
     "offset": CONTROLLER_OFF},
    {"name": "LastHitBy", "type_name": "ObjectProperty",
     "offset": LAST_HIT_BY_OFF},
]
#: `SQFlyingDrone` adds none of its own (spec §7) — the base is the membership
#: test and the class the doctor watches the two inherited names on.
BASE_PROPS: list[dict] = []
COMMANDER_PROPS = [
    {"name": "SQ PC", "type_name": "ObjectProperty", "offset": SQ_PC_OFF},
    {"name": "HealthComponent", "type_name": "ObjectProperty",
     "offset": HEALTH_COMPONENT_OFF},
    {"name": "Dead", "type_name": "BoolProperty", "offset": DEAD_OFF,
     "bits": (0, 0x04)},
    {"name": "Command Action", "type_name": "ClassProperty",
     "offset": COMMAND_ACTION_OFF},
    {"name": "Max Fly Height", "type_name": "DoubleProperty",
     "offset": MAX_FLY_HEIGHT_OFF},
]
RECON_PROPS = [
    {"name": "BatteryLifetimeMax", "type_name": "DoubleProperty",
     "offset": BATTERY_OFF},
]
COMPONENT_PROPS = [
    {"name": "Health", "type_name": "FloatProperty", "offset": HEALTH_OFF},
    {"name": "Max Health", "type_name": "DoubleProperty",
     "offset": MAX_HEALTH_OFF},
]


class _Pm(FakeProcessMemory):
    """FakeProcessMemory plus the typed reads the reader's lambdas use."""

    def read_u8(self, addr: int) -> int:
        return self.read(addr, 1)[0]

    def read_i32(self, addr: int) -> int:
        return struct.unpack("<i", self.read(addr, 4))[0]

    def read_u64(self, addr: int) -> int:
        return struct.unpack("<Q", self.read(addr, 8))[0]


class _Names:
    """The FName pool: text <-> comparison index, and the allocator API."""

    def __init__(self) -> None:
        self._to_ci: dict[str, int] = {}
        self._to_name: dict[int, str] = {}

    def ci(self, name: str) -> int:
        if name not in self._to_ci:
            idx = len(self._to_ci) + 1
            self._to_ci[name] = idx
            self._to_name[idx] = name
        return self._to_ci[name]

    def fname_to_str(self, ci: int, num: int = 0) -> str:
        return self._to_name.get(ci, "None")


class _Image:
    def __init__(self) -> None:
        self.pm = _Pm()
        self.names = _Names()
        self._next = 0x10_0000
        self._field_classes: dict[str, int] = {}

    def alloc(self, data: bytes) -> int:
        addr = self._next
        self.pm.add(addr, bytes(data))
        self._next = (addr + len(data) + 0xFF) & ~0xFF
        return addr

    def field_class(self, type_name: str) -> int:
        """The FFieldClass singleton for a property type (its name at +0)."""
        if type_name not in self._field_classes:
            self._field_classes[type_name] = self.alloc(
                struct.pack("<II", self.names.ci(type_name), 0) + bytes(8))
        return self._field_classes[type_name]

    def prop(self, name: str, type_name: str, offset: int, *, nxt: int = 0,
             bits: tuple[int, int] | None = None) -> int:
        b = bytearray(0x80)
        struct.pack_into("<Q", b, FFIELD_CLASS, self.field_class(type_name))
        struct.pack_into("<Q", b, FFIELD_NEXT, nxt)
        struct.pack_into("<II", b, FFIELD_NAME, self.names.ci(name), 0)
        struct.pack_into("<i", b, FPROP_ARRAY_DIM, 1)
        struct.pack_into("<i", b, FPROP_ELEM_SIZE, 8)
        struct.pack_into("<i", b, FPROP_OFFSET, offset)
        if bits is not None:
            byte_off, mask = bits
            b[FPROP_BOOL] = 1                 # FieldSize
            b[FPROP_BOOL + 1] = byte_off      # ByteOffset
            b[FPROP_BOOL + 2] = mask          # ByteMask
            b[FPROP_BOOL + 3] = mask          # FieldMask
        return self.alloc(bytes(b))

    def uclass(self, name: str, specs: list[dict], *, super_addr: int = 0,
               size: int = DRONE_SIZE) -> int:
        """A UClass with `specs` as its own property chain, deriving from
        `super_addr`. Inherited properties are never restated: the walker
        finds them by climbing, which is the behaviour under test."""
        nxt = 0
        for spec in reversed(specs):
            nxt = self.prop(nxt=nxt, **spec)
        b = bytearray(0x60)
        struct.pack_into("<II", b, UOBJ_NAME_PRIVATE, self.names.ci(name), 0)
        struct.pack_into("<Q", b, USTRUCT_SUPER, super_addr)
        struct.pack_into("<Q", b, USTRUCT_CHILD_PROPS, nxt)
        struct.pack_into("<i", b, USTRUCT_PROPS_SIZE, size)
        struct.pack_into("<i", b, USTRUCT_MIN_ALIGN, 8)
        return self.alloc(bytes(b))

    def fstring(self, text: str) -> bytes:
        """The 16-byte FString header, with its buffer allocated."""
        ptr = self.alloc((text + "\0").encode("utf-16-le"))
        return struct.pack("<Qii", ptr, len(text) + 1, len(text) + 1)

    def player_state(self, name: str, eos: str) -> int:
        b = bytearray(0x80)
        b[PS_NAME_OFF:PS_NAME_OFF + 16] = self.fstring(name)
        b[PS_EOS_OFF:PS_EOS_OFF + 16] = self.fstring(eos)
        return self.alloc(bytes(b))

    def controller(self, ps_addr: int) -> int:
        b = bytearray(0x80)
        struct.pack_into("<Q", b, CTRL_PS_OFF, ps_addr)
        return self.alloc(bytes(b))

    def component(self, position: tuple[float, float, float]) -> int:
        b = bytearray(COMPONENT_SIZE)
        b[TO_WORLD_ROTATION_OFF:TO_WORLD_ROTATION_OFF + 32] = _quat_yaw(YAW)
        struct.pack_into("<ddd", b, TO_WORLD_TRANSLATION_OFF, *position)
        struct.pack_into("<ddd", b, RELATIVE_ROTATION_OFF, 0.0, YAW, 0.0)
        return self.alloc(bytes(b))

    def health_component(self, cls_addr: int) -> int:
        """The separate object the health figures live on — its own class, its
        own layout, reached through the pawn's `HealthComponent` pointer."""
        b = bytearray(0x200)
        struct.pack_into("<Q", b, UOBJ_CLASS_PRIVATE, cls_addr)
        struct.pack_into("<f", b, HEALTH_OFF, HEALTH)
        struct.pack_into("<d", b, MAX_HEALTH_OFF, MAX_HEALTH)
        return self.alloc(bytes(b))

    def named_object(self, name: str, data: bytes) -> int:
        b = bytearray(data)
        struct.pack_into("<II", b, UOBJ_NAME_PRIVATE, self.names.ci(name), 0)
        return self.alloc(bytes(b))


def _quat_yaw(deg: float) -> bytes:
    """ComponentToWorld's FQuat for a pure yaw — the transform the position is
    read from, so the heading has to come from the same one."""
    half = math.radians(deg) / 2.0
    return struct.pack("<dddd", 0.0, 0.0, math.sin(half), math.cos(half))


class Fixture(SimpleNamespace):
    def drone(self, class_name: str, **kw):
        """`read_drone`'s output for one of the pawns built below."""
        addr, cls_addr = self.pawns[class_name]
        return sn.read_drone(self.pm, self.names, self.paths, addr, class_name,
                             cls_addr, kw.pop("caches", self.caches), **kw)

    def drones(self, order: tuple[str, ...] | None = None, **kw):
        """`build_drones` over the fixture's pawns, in the order the object
        walk would hand them over."""
        names = order if order is not None else self.order
        return sn.build_drones(self.pm, self.names, self.paths,
                               [self.pawns[n] for n in names],
                               kw.pop("caches", self.caches), **kw)


def build(*, omit: frozenset[str] = frozenset(), dead: bool = False,
          pilot: str = "player", owner: str = "controller",
          hitter: str = "controller", action_null: bool = False,
          component: str = "real",
          component_props: list[dict] | None = None,
          recon_props: list[dict] | None = None) -> Fixture:
    """One drone of each class over identical bytes.

    `omit` drops a property from the class that declares it, the way a Squad
    rename does — from the CLASS, so the reader looks for a name that is not
    there. Each of `pilot`, `owner` and `hitter` is `"player"` / `"controller"`
    (a pointer that resolves), `"null"` (the game's own empty) or `"stranger"`
    (a pointer that reaches something which is not, or does not reach, a player
    state). `component` is `"real"`, `"null"` or `"stranger"` the same way.
    """
    img = _Image()

    def keep(specs):
        return [s for s in specs if s["name"] not in omit]

    pawn_cls = img.uclass(PAWN, keep(PAWN_PROPS))
    base = img.uclass(BASE, keep(BASE_PROPS), super_addr=pawn_cls)
    commander_cls = img.uclass(COMMANDER_DRONE, keep(COMMANDER_PROPS),
                               super_addr=base)
    classes = {
        COMMANDER_DRONE: commander_cls,
        RECON_DRONE: img.uclass(
            RECON_DRONE,
            keep(RECON_PROPS if recon_props is None else recon_props),
            super_addr=commander_cls),
        OUTSIDER: img.uclass(OUTSIDER, [], super_addr=pawn_cls),
    }
    component_cls = img.uclass(
        COMPONENT_CLASS,
        COMPONENT_PROPS if component_props is None else component_props)
    action_cls = img.uclass(ACTION, [])

    # A player state the pilot pointer names directly; controllers for the
    # owner and the last hitter, which are one hop short of one. The
    # "stranger" objects are zeroed: a controller that reaches no player
    # state, and a pawn pointer that lands on something with no identity.
    pilot_ps = img.player_state("Ruby", PILOT_EOS)
    owner_ctrl = img.controller(img.player_state("Six", OWNER_EOS))
    hitter_ctrl = img.controller(img.player_state("Nine", HITTER_EOS))
    stranger = img.alloc(bytes(0x80))
    stranger_ctrl = img.controller(0)
    comp_addr = img.health_component(component_cls)
    strange_comp = img.alloc(bytes(0x200))

    # `PlayerState` names a player state directly; `SQ PC` and `LastHitBy` are
    # controllers, one hop short of one — which is why the "stranger" case
    # differs between them: an object with no identity on the pawn's pointer,
    # a controller that reaches no player state on the other two.
    pilot_ptr = {"player": pilot_ps, "null": 0, "stranger": stranger}[pilot]
    owner_ptr = {"controller": owner_ctrl, "null": 0,
                 "stranger": stranger_ctrl}[owner]
    hitter_ptr = {"controller": hitter_ctrl, "null": 0,
                  "stranger": stranger_ctrl}[hitter]
    comp_ptr = {"real": comp_addr, "null": 0, "stranger": strange_comp}[
        component]

    component_obj = img.component(POSITION)
    origin_component = img.component((0.0, 0.0, 0.0))

    def instance(name: str, cls_addr: int, root: int) -> tuple[int, int]:
        b = bytearray(DRONE_SIZE)
        struct.pack_into("<Q", b, ROOT_COMPONENT_OFF, root)
        struct.pack_into("<Q", b, PLAYER_STATE_OFF, pilot_ptr)
        struct.pack_into("<Q", b, CONTROLLER_OFF, owner_ctrl)
        struct.pack_into("<Q", b, LAST_HIT_BY_OFF, hitter_ptr)
        struct.pack_into("<Q", b, SQ_PC_OFF, owner_ptr)
        struct.pack_into("<Q", b, HEALTH_COMPONENT_OFF, comp_ptr)
        b[DEAD_OFF] = 0x04 if dead else 0x00
        if not action_null:
            struct.pack_into("<Q", b, COMMAND_ACTION_OFF, action_cls)
        struct.pack_into("<d", b, MAX_FLY_HEIGHT_OFF, MAX_FLY_HEIGHT)
        # The battery sits in EVERY drone's bytes; only the recon class
        # declares the name, and only that class emits the key.
        struct.pack_into("<d", b, BATTERY_OFF, BATTERY)
        return img.named_object(name, bytes(b)), cls_addr

    pawns = {
        COMMANDER_DRONE: instance(f"{COMMANDER_DRONE}_2111986534",
                                  classes[COMMANDER_DRONE], component_obj),
        RECON_DRONE: instance(f"{RECON_DRONE}_2111986535",
                              classes[RECON_DRONE], component_obj),
        # The class default object every match carries beside the real pawns,
        # and which is never an entry (spec §7).
        "cdo": instance(f"Default__{RECON_DRONE}", classes[RECON_DRONE],
                        component_obj),
        # A dead pawn's final tick, zeroed to the map origin before the pawn
        # is freed (09-05) — the junk rule of §2.
        "origin": instance(f"{RECON_DRONE}_2111986536",
                           classes[RECON_DRONE], origin_component),
    }
    paths = SimpleNamespace(
        actor_root_component_off=ROOT_COMPONENT_OFF,
        scene_component_to_world_translation_off=TO_WORLD_TRANSLATION_OFF,
        scene_component_to_world_rotation_off=TO_WORLD_ROTATION_OFF,
        scene_relative_rotation_off=RELATIVE_ROTATION_OFF,
        scene_attach_parent_off=ATTACH_PARENT_OFF,
        pc_playerstate_off=CTRL_PS_OFF,
        ps_offsets={"PlayerNamePrivate": PS_NAME_OFF,
                    "OnlineUserId": PS_EOS_OFF},
    )
    return Fixture(img=img, pm=img.pm, names=img.names, paths=paths,
                   caches=sn.SnapshotCaches(), pawns=pawns, classes=classes,
                   component_cls=component_cls, comp_addr=comp_addr,
                   action_cls=action_cls, owner_ctrl=owner_ctrl,
                   order=(COMMANDER_DRONE, RECON_DRONE))


def _common(fx, class_name: str, position=POSITION) -> dict:
    """What both drone classes carry: everything but the recon battery."""
    return {
        "id": f"{fx.pawns[class_name][0]:#x}",
        "class": class_name,
        "position": {"x": position[0], "y": position[1], "z": position[2]},
        "yaw": pytest.approx(YAW),
        "dead": False,
        "health": HEALTH,
        "maxHealth": MAX_HEALTH,
        "pilotEosId": PILOT_EOS,
        "ownerEosId": OWNER_EOS,
        "commandAction": ACTION,
        "lastHitByEosId": HITTER_EOS,
    }


# ---- what each class emits -------------------------------------------------

def test_the_commander_drone_carries_everything_but_a_battery():
    """Spec §7: `BatteryLifetimeMax` "does not exist on the commander drone's
    class, whose budget is its action's `activeSec`". The value is in its bytes
    all the same — the key's absence is the class's layout, not the fixture's."""
    fx = build()
    entry = fx.drone(COMMANDER_DRONE)
    assert entry == _common(fx, COMMANDER_DRONE)
    addr = fx.pawns[COMMANDER_DRONE][0]
    assert struct.unpack("<d", fx.pm.read(addr + BATTERY_OFF, 8))[0] == BATTERY


def test_the_recon_drone_adds_its_flight_budget():
    """The one field the recon subclass declares of its own (09-05)."""
    fx = build()
    assert fx.drone(RECON_DRONE) == _common(fx, RECON_DRONE) | {
        "batteryLifetimeMax": BATTERY,
    }


def test_the_health_figures_come_off_the_component_not_the_pawn():
    """`HealthComponent` -> `Health` (float) and `Max Health` (double), read as
    the types reflection reports on the COMPONENT's own class (09-05). The pawn
    itself declares neither name."""
    fx = build()
    entry = fx.drone(RECON_DRONE)
    assert entry["health"] == HEALTH and entry["maxHealth"] == MAX_HEALTH
    assert "Health" not in [p["name"] for p in COMMANDER_PROPS + RECON_PROPS]


def test_the_flight_ceiling_beside_the_battery_is_never_recorded():
    """`Max Fly Height` reads a constant 2200 whose meaning is unresolved, and
    spec §10 keeps it out. It sits in every pawn's bytes and reaches no key."""
    assert MAX_FLY_HEIGHT not in build().drone(RECON_DRONE).values()


def test_a_dead_pawn_reads_the_flag_through_its_mask():
    """`Dead` is a bitfield bool sharing its byte, so it is read through the
    FBoolProperty mask rather than a `& 1` (spec §7)."""
    assert build(dead=True).drone(RECON_DRONE)["dead"] is True
    assert build().drone(RECON_DRONE)["dead"] is False


# ---- the rules the fields are emitted under --------------------------------

def test_an_unflown_drone_reads_a_null_pilot_not_a_missing_one():
    """48 s of a fresh deploy read no pilot (flight 4, 09-05), and a landed,
    exited drone reads none either. `null` is the game's own empty — a
    different answer from a key that is not there (spec §2)."""
    entry = build(pilot="null").drone(RECON_DRONE)
    assert "pilotEosId" in entry and entry["pilotEosId"] is None
    assert entry["ownerEosId"] == OWNER_EOS      # the owner persists (09-05)


def test_a_recon_drones_empty_calling_action_reads_null():
    """`Command Action` read null on every recon row (09-05): the drone was
    not called by a commander, which the game says by an empty pointer."""
    entry = build(action_null=True).drone(RECON_DRONE)
    assert "commandAction" in entry and entry["commandAction"] is None


def test_a_pawn_nothing_has_hit_reads_a_null_last_hitter():
    """`null` until something has hit it — every one of 1,619 live rows on
    09-05 — and the killer's id at the kill."""
    entry = build(hitter="null").drone(RECON_DRONE)
    assert "lastHitByEosId" in entry and entry["lastHitByEosId"] is None
    entry = build(hitter="controller", dead=True).drone(RECON_DRONE)
    assert entry["lastHitByEosId"] == HITTER_EOS and entry["dead"] is True


@pytest.mark.parametrize("kw, gone", [
    ({"pilot": "stranger"}, "pilotEosId"),
    ({"owner": "stranger"}, "ownerEosId"),
    ({"hitter": "stranger"}, "lastHitByEosId"),
])
def test_a_pointer_that_reaches_no_player_state_omits_its_key(kw, gone):
    """Spec §2: a pointer that does not reach a player state gives an omitted
    field, where a pointer that reads null gives `null`. "Unknown" and "none"
    are different answers and a viewer is entitled to tell them apart."""
    entry = build(**kw).drone(RECON_DRONE)
    assert gone not in entry
    assert entry["health"] == HEALTH          # its neighbours are unharmed


def test_a_null_health_component_omits_both_figures():
    """There is no component to read `Health` off, so the two keys are absent —
    the reader saying it could not read, not the game saying zero."""
    entry = build(component="null").drone(RECON_DRONE)
    assert "health" not in entry and "maxHealth" not in entry
    assert entry["dead"] is False and entry["pilotEosId"] == PILOT_EOS


def test_a_component_that_is_not_a_health_component_omits_both_figures():
    """The pointer reaches an object whose class declares neither name. Its
    bytes are not decoded as numbers anyway (spec §2)."""
    entry = build(component="stranger").drone(RECON_DRONE)
    assert "health" not in entry and "maxHealth" not in entry


def test_a_component_that_declares_one_name_emits_one_key():
    """Each figure stands on its own name: a component grown out of `Max
    Health` still reports `health`."""
    entry = build(component_props=[COMPONENT_PROPS[0]]).drone(RECON_DRONE)
    assert entry["health"] == HEALTH and "maxHealth" not in entry


# ---- the list ---------------------------------------------------------------

def test_the_list_holds_one_entry_per_live_pawn():
    fx = build()
    assert [e["class"] for e in fx.drones()] == list(fx.order)


def test_a_class_default_object_is_never_an_entry():
    """Every match carries the CDO of each drone class beside the real pawns;
    it is not a drone and never reaches the list (spec §7)."""
    entries = build().drones(order=(COMMANDER_DRONE, "cdo", RECON_DRONE))
    assert [e["class"] for e in entries] == [COMMANDER_DRONE, RECON_DRONE]


def test_the_zeroed_final_tick_of_a_dead_pawn_is_dropped():
    """A dead drone's last tick reads exactly (0, 0, 0) before the pawn is
    freed (09-05), which is the junk rule of spec §2 doing a second job here."""
    entries = build().drones(order=(RECON_DRONE, "origin"))
    assert [e["class"] for e in entries] == [RECON_DRONE]


def test_no_pawns_means_an_empty_list_and_so_no_key():
    """`build_snapshot` writes `drones` only for a non-empty list, so a frame
    with no drone in the air carries no key at all — which is what every
    recording made before this one says (spec §7)."""
    assert build().drones(order=()) == []
    assert build().drones(order=("cdo", "origin")) == []


# ---- reading by name, never by class name ----------------------------------

def test_a_commander_drone_that_gains_a_battery_emits_one():
    """Nothing is matched on a class name. The recon subclass is where the
    battery lives today; a class that gains or loses the name is followed
    without a code change, so declaring it on the commander drone's own class
    is enough."""
    grown = build(recon_props=[]).drone(COMMANDER_DRONE)
    assert "batteryLifetimeMax" not in grown
    fx = build()
    fx.pawns[COMMANDER_DRONE] = (fx.pawns[COMMANDER_DRONE][0],
                                 fx.classes[RECON_DRONE])
    assert fx.drone(COMMANDER_DRONE)["batteryLifetimeMax"] == BATTERY


def test_every_name_the_reader_looks_for_reaches_a_key():
    """The layout walk looks for a fixed set of names; a name added to that set
    with no branch reading it would be a field silently missing from every
    recording. The recon drone declares all of §7 between it and its parents:
    one key out per name in, and no name listed twice."""
    entry = build().drone(RECON_DRONE)
    keys = set(entry) - {"id", "class", "position", "yaw", "maxHealth"}
    assert len(sn.DRONE_NAMES) == len(set(sn.DRONE_NAMES))
    assert len(keys) == len(sn.DRONE_NAMES)


@pytest.mark.parametrize("renamed, gone", [
    ("PlayerState", "pilotEosId"),
    ("SQ PC", "ownerEosId"),
    ("LastHitBy", "lastHitByEosId"),
    ("HealthComponent", "health"),
    ("Dead", "dead"),
    ("Command Action", "commandAction"),
    ("BatteryLifetimeMax", "batteryLifetimeMax"),
])
def test_a_renamed_property_omits_its_key_and_leaves_the_rest(renamed, gone):
    """A Squad rename is the case these reads have no fallback for: the name is
    simply not on the class any more. The key vanishes — never a zero, never
    last frame's value — and its neighbours are untouched."""
    fx = build(omit=frozenset({renamed}))
    entry = fx.drone(RECON_DRONE)
    assert gone not in entry
    assert entry["id"] == f"{fx.pawns[RECON_DRONE][0]:#x}"
    assert entry["class"] == RECON_DRONE
    assert entry["position"] == {"x": POSITION[0], "y": POSITION[1],
                                 "z": POSITION[2]}


def test_a_figure_is_read_as_the_type_reflection_reports():
    """Spec §2, "Numbers": the type follows the reflected property. A name that
    came back as something else entirely — the shape a repurpose would take —
    is omitted rather than decoded as a number anyway."""
    odd = [{"name": "BatteryLifetimeMax", "type_name": "ObjectProperty",
            "offset": BATTERY_OFF}]
    entry = build(recon_props=odd).drone(RECON_DRONE)
    assert "batteryLifetimeMax" not in entry
    assert entry["health"] == HEALTH           # still a Float, still read


# ---- the caches -------------------------------------------------------------

def _count_layout_walks(monkeypatch):
    calls: list[int] = []
    real = sn.get_class_layout

    def counting(pm, class_addr, alloc):
        calls.append(class_addr)
        return real(pm, class_addr, alloc)

    monkeypatch.setattr(sn, "get_class_layout", counting)
    return calls


def test_each_class_is_reflected_once_however_many_pawns(monkeypatch):
    """Two drone classes and one component class, walked once each and never
    again — the layout belongs to the class, not to the pawn."""
    fx = build()
    calls = _count_layout_walks(monkeypatch)
    for _tick in range(3):
        fx.drones()
    assert len(calls) == 3
    assert len(fx.caches.drone_props) == 2
    assert len(fx.caches.health_component_props) == 1


def test_a_generation_bump_re_reflects_both_layouts(monkeypatch):
    """The rolling reset is the retry for a reflection walk that failed
    mid-tick, so a cached answer must not outlive it — and the stale entries go
    as it passes, rather than sitting here for the rest of the process's life
    after a map change."""
    fx = build()
    calls = _count_layout_walks(monkeypatch)
    fx.drone(RECON_DRONE)
    assert len(calls) == 2                     # the pawn's class and the
    fx.caches._light_reset(tick=60, reason="rolling")   # component's
    assert fx.caches.drone_props == {}
    assert fx.caches.health_component_props == {}
    fx.drone(RECON_DRONE)
    assert len(calls) == 4


def test_the_reader_runs_without_caches_at_all():
    """`caches` is optional on every other per-actor read; a drone read without
    one still emits, it just reflects each time."""
    entry = build().drone(RECON_DRONE, caches=None)
    assert entry["health"] == HEALTH and entry["batteryLifetimeMax"] == BATTERY
    assert entry["ownerEosId"] == OWNER_EOS
    assert entry["commandAction"] == ACTION
