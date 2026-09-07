"""`commandActions` — one entry per live command actor (spec §6).

Membership is the only thing a class decides here: an actor is in the list
when its SuperStruct chain reaches the native `SQCommandActor`. What the
entry CARRIES is read off the actor's own class layout, so the image below
carries real reflection data — UClass objects with linked FProperty chains
and the chain the box reflected on 2026-09-07,
`BP_CommandActor_ArtilleryBase_C` -> `BP_CommandActor_C` -> `SQCommandActor`
-> `Actor`. The families declare their own names at their own level, and
nothing anywhere matches a class name.

Every actor in the fixture is laid out over the SAME bytes: the strike
aircraft's four values, the artillery's ten and the drone call actor's two
sit at the same offsets in every instance. What comes out is decided by the
class's properties alone — which is the whole reason no offset is hardcoded
and no class name is tested — so the UAV, whose class declares nothing of
its own, emits the common fields and not one field more.

The absence rules of spec §2 are the other half. A key is emitted when the
read succeeds, `null` when the game's own value is empty (a template actor's
`Action` and `DamageInstigatorController` both read null at a layer load),
and ABSENT when the recorder could not read — which is what a Squad rename
looks like, so the rename tests drop the property from the class rather than
a key from a dict.
"""
from __future__ import annotations

import math
import struct
from types import SimpleNamespace

import pytest

from sqreader.squad import snapshot as sn
from sqreader.ue.uobject import UOBJ_NAME_PRIVATE

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

ACTOR_SIZE = 0x400
ROOT_COMPONENT_OFF = 0x160

DISTANCE_OFF = 0x200         # SQCommandActor: the four common names
TEAM_OFF = 0x208
INSTIGATOR_OFF = 0x210
ACTION_OFF = 0x218
DESTROYED_OFF = 0x220        # BP_CommandActor_C
DESTROY_DELAY_OFF = 0x228    # read by nobody: spec §10 keeps the delay out
ORIGIN_OFF = 0x230           # BP_CommandActor_ArtilleryBase_C: the ten
TARGET_OFF = 0x248
MAX_DROP_OFF = 0x260
PRE_SHELLS_OFF = 0x268
PRE_DELAY_OFF = 0x270
SHELLS_PER_OFF = 0x278
BARRAGE_COUNT_OFF = 0x27C
CUR_PRE_SHELLS_OFF = 0x280
CUR_BARRAGE_OFF = 0x284
PROJECTILE_OFF = 0x288
SHOTS_MADE_OFF = 0x290       # the strike family
MAX_SHOTS_OFF = 0x294
SPLINE_DISTANCE_OFF = 0x298
HEALTH_OFF = 0x2A0           # BP_CommandActor_Drone_C
SQ_PC_OFF = 0x2A8

COMPONENT_SIZE = 0x200
TO_WORLD_ROTATION_OFF = 0x80
TO_WORLD_TRANSLATION_OFF = 0xA0
RELATIVE_ROTATION_OFF = 0xC0
ATTACH_PARENT_OFF = 0xE0

PS_NAME_OFF = 0x10
PS_EOS_OFF = 0x30
CTRL_PS_OFF = 0x50

POSITION = (12345.5, -6789.25, 42.0)
YAW = 45.0
#: The drone's call actor sits at (0, 0, z) and means nothing by it — the
#: position exclusion is the raw triple, so z keeps it in the list (spec §6).
CALL_ACTOR_POSITION = (0.0, 0.0, 850.0)

# ---- the values every actor's bytes carry ----------------------------------

DISTANCE = 45000.0           # the creep's own length figure (08-30)
TEAM = 1
ORIGIN = (1000.0, 2000.0, 30.0)
TARGET = (4000.0, 5000.0, 60.0)
MAX_DROP_RADIUS = 1.0        # the mortar's, read 2026-09-07
PRE_WARNING_SHELLS = 2
PRE_WARNING_DELAY = 12.0
SHELLS_PER_BARRAGE = 10
BARRAGE_COUNT = 8
CURRENT_PREWARNING_SHELLS = 1
CURRENT_BARRAGE = 3
SHOTS_MADE = 4
MAX_SHOTS = 12
SPLINE_DISTANCE = 6000.0
HEALTH = 100.0               # the call actor's, read throughout a call
DESTROY_DELAY = 30.0

CALLER_EOS = "eos-000000000000000000000000commander"
OWNER_EOS = "eos-0000000000000000000000000000owner"

# ---- the classes, as §13 reflects them -------------------------------------

BASE = "SQCommandActor"
BP_BASE = "BP_CommandActor_C"
ARTILLERY_BASE = "BP_CommandActor_ArtilleryBase_C"
CREEP = "BP_CommandActor_Artillery_Creep_C"
STRIKE = "BP_CommandActor_FA18_Strafe_C"
UAV = "BP_CommandActor_UAV_MQ9_C"
CALL_ACTOR = "BP_CommandActor_Drone_C"
OUTSIDER = "BP_Deployable_DroneSpawner_C"   # an Actor that is not one of ours
CONFIG = "CommandAction_Artillery_Creep_USMC_C"
PROJECTILE = "BP_Projectile_155mm_C"

BASE_PROPS = [
    {"name": "Distance", "type_name": "FloatProperty", "offset": DISTANCE_OFF},
    {"name": "Team", "type_name": "IntProperty", "offset": TEAM_OFF},
    {"name": "DamageInstigatorController", "type_name": "WeakObjectProperty",
     "offset": INSTIGATOR_OFF},
    {"name": "Action", "type_name": "ClassProperty", "offset": ACTION_OFF},
]
BP_BASE_PROPS = [
    {"name": "Action Destroyed", "type_name": "BoolProperty",
     "offset": DESTROYED_OFF, "bits": (0, 0x02)},
    {"name": "Destroy Delay after Action Destroyed",
     "type_name": "DoubleProperty", "offset": DESTROY_DELAY_OFF},
]
ARTILLERY_PROPS = [
    {"name": "Origin Location", "type_name": "StructProperty",
     "offset": ORIGIN_OFF},
    {"name": "target location", "type_name": "StructProperty",
     "offset": TARGET_OFF},
    {"name": "Max Drop Radius", "type_name": "DoubleProperty",
     "offset": MAX_DROP_OFF},
    {"name": "Pre Warning Shells", "type_name": "IntProperty",
     "offset": PRE_SHELLS_OFF},
    {"name": "Pre Warning Delay", "type_name": "DoubleProperty",
     "offset": PRE_DELAY_OFF},
    {"name": "Shells Per Barrage", "type_name": "IntProperty",
     "offset": SHELLS_PER_OFF},
    {"name": "Barrage Count", "type_name": "IntProperty",
     "offset": BARRAGE_COUNT_OFF},
    {"name": "Current Prewarning Shells", "type_name": "IntProperty",
     "offset": CUR_PRE_SHELLS_OFF},
    {"name": "Current Barrage", "type_name": "IntProperty",
     "offset": CUR_BARRAGE_OFF},
    {"name": "Projectile", "type_name": "ClassProperty",
     "offset": PROJECTILE_OFF},
]
STRIKE_PROPS = [
    {"name": "CurrentShotsMade", "type_name": "IntProperty",
     "offset": SHOTS_MADE_OFF},
    {"name": "MaxShots", "type_name": "IntProperty", "offset": MAX_SHOTS_OFF},
    {"name": "Spline Distance", "type_name": "DoubleProperty",
     "offset": SPLINE_DISTANCE_OFF},
    {"name": "Origin Location", "type_name": "StructProperty",
     "offset": ORIGIN_OFF},
]
CALL_ACTOR_PROPS = [
    {"name": "Health", "type_name": "DoubleProperty", "offset": HEALTH_OFF},
    {"name": "SQ PC", "type_name": "ObjectProperty", "offset": SQ_PC_OFF},
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


class _Arr:
    """GUObjectArray, for the weak-pointer resolve and nothing else.

    Only the three calls `_resolve_weak_obj` makes: the bound, the address in
    a slot, and the slot's live serial — the check that makes a stale weak
    pointer resolve to nothing instead of a ghost."""

    def __init__(self, slots: dict[int, tuple[int, int]]) -> None:
        self.slots = slots                    # index -> (addr, serial)
        self.num_elements = 64

    def get_object_addr(self, idx: int) -> int:
        return self.slots.get(idx, (0, 0))[0]

    def get_serial_number(self, idx: int) -> int:
        return self.slots.get(idx, (0, 0))[1]


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
               size: int = ACTOR_SIZE) -> int:
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

    def named_object(self, name: str, data: bytes) -> int:
        b = bytearray(data)
        struct.pack_into("<II", b, UOBJ_NAME_PRIVATE, self.names.ci(name), 0)
        return self.alloc(bytes(b))


def _quat_yaw(deg: float) -> bytes:
    """ComponentToWorld's FQuat for a pure yaw — the transform the position
    is read from, so the heading has to come from the same one."""
    half = math.radians(deg) / 2.0
    return struct.pack("<dddd", 0.0, 0.0, math.sin(half), math.cos(half))


CALLER_SLOT = 7
CALLER_SERIAL = 3


class Fixture(SimpleNamespace):
    def actor(self, class_name: str, **kw):
        """`read_command_actor`'s output for one of the actors built below."""
        addr, cls_addr = self.actors[class_name]
        return sn.read_command_actor(
            self.pm, self.names, kw.pop("arr", self.arr), self.paths, addr,
            class_name, cls_addr, kw.pop("caches", self.caches), **kw)

    def actions(self, order: tuple[str, ...] | None = None, **kw):
        """`build_command_actions` over the fixture's actors, in the order the
        object walk would hand them over."""
        names = order if order is not None else self.order
        return sn.build_command_actions(
            self.pm, self.names, kw.pop("arr", self.arr), self.paths,
            [self.actors[n] for n in names],
            kw.pop("caches", self.caches), **kw)


def build(*, omit: frozenset[str] = frozenset(), destroyed: bool = False,
          action_null: bool = False, caller_null: bool = False,
          caller_stale: bool = False,
          strike_props: list[dict] | None = None) -> Fixture:
    """One actor of each family over identical bytes.

    `omit` drops a property from the class that declares it, the way a Squad
    rename does — from the CLASS, so the reader looks for a name that is not
    there; `action_null` empties the creep's `Action` pointer; `caller_null`
    empties its instigator; `caller_stale` points that weak pointer at a slot
    whose serial has moved on; `strike_props` re-declares the strike class, to
    show that what an actor emits follows its class's properties, never its
    class's name.
    """
    img = _Image()

    def keep(specs):
        return [s for s in specs if s["name"] not in omit]

    actor_cls = img.uclass("Actor", [])
    base = img.uclass(BASE, keep(BASE_PROPS), super_addr=actor_cls)
    bp_base = img.uclass(BP_BASE, keep(BP_BASE_PROPS), super_addr=base)
    artillery_base = img.uclass(ARTILLERY_BASE, keep(ARTILLERY_PROPS),
                                super_addr=bp_base)
    classes = {
        CREEP: img.uclass(CREEP, [], super_addr=artillery_base),
        STRIKE: img.uclass(
            STRIKE,
            keep(STRIKE_PROPS if strike_props is None else strike_props),
            super_addr=bp_base),
        UAV: img.uclass(UAV, [], super_addr=bp_base),
        CALL_ACTOR: img.uclass(CALL_ACTOR, keep(CALL_ACTOR_PROPS),
                               super_addr=bp_base),
        OUTSIDER: img.uclass(OUTSIDER, [], super_addr=actor_cls),
    }
    config_cls = img.uclass(CONFIG, [])
    projectile_cls = img.uclass(PROJECTILE, [])

    caller_ctrl = img.controller(img.player_state("Six", CALLER_EOS))
    owner_ctrl = img.controller(img.player_state("Ruby", OWNER_EOS))
    slots = {CALLER_SLOT: (caller_ctrl, CALLER_SERIAL)}

    component = img.component(POSITION)
    call_component = img.component(CALL_ACTOR_POSITION)
    origin_component = img.component((0.0, 0.0, 0.0))

    def instance(name: str, cls_addr: int, root: int) -> tuple[int, int]:
        b = bytearray(ACTOR_SIZE)
        struct.pack_into("<Q", b, ROOT_COMPONENT_OFF, root)
        struct.pack_into("<f", b, DISTANCE_OFF, DISTANCE)
        struct.pack_into("<i", b, TEAM_OFF, TEAM)
        struct.pack_into("<ii", b, INSTIGATOR_OFF,
                         *((0, 0) if caller_null else
                           (CALLER_SLOT,
                            CALLER_SERIAL + 1 if caller_stale
                            else CALLER_SERIAL)))
        if not action_null:
            struct.pack_into("<Q", b, ACTION_OFF, config_cls)
        b[DESTROYED_OFF] = 0x02 if destroyed else 0x00
        struct.pack_into("<d", b, DESTROY_DELAY_OFF, DESTROY_DELAY)
        struct.pack_into("<ddd", b, ORIGIN_OFF, *ORIGIN)
        struct.pack_into("<ddd", b, TARGET_OFF, *TARGET)
        struct.pack_into("<d", b, MAX_DROP_OFF, MAX_DROP_RADIUS)
        struct.pack_into("<i", b, PRE_SHELLS_OFF, PRE_WARNING_SHELLS)
        struct.pack_into("<d", b, PRE_DELAY_OFF, PRE_WARNING_DELAY)
        struct.pack_into("<i", b, SHELLS_PER_OFF, SHELLS_PER_BARRAGE)
        struct.pack_into("<i", b, BARRAGE_COUNT_OFF, BARRAGE_COUNT)
        struct.pack_into("<i", b, CUR_PRE_SHELLS_OFF,
                         CURRENT_PREWARNING_SHELLS)
        struct.pack_into("<i", b, CUR_BARRAGE_OFF, CURRENT_BARRAGE)
        struct.pack_into("<Q", b, PROJECTILE_OFF, projectile_cls)
        struct.pack_into("<i", b, SHOTS_MADE_OFF, SHOTS_MADE)
        struct.pack_into("<i", b, MAX_SHOTS_OFF, MAX_SHOTS)
        struct.pack_into("<d", b, SPLINE_DISTANCE_OFF, SPLINE_DISTANCE)
        struct.pack_into("<d", b, HEALTH_OFF, HEALTH)
        struct.pack_into("<Q", b, SQ_PC_OFF, owner_ctrl)
        return img.named_object(name, bytes(b)), cls_addr

    actors = {
        CREEP: instance(f"{CREEP}_2111986534", classes[CREEP], component),
        STRIKE: instance(f"{STRIKE}_2111986535", classes[STRIKE], component),
        UAV: instance(f"{UAV}_2111986536", classes[UAV], component),
        CALL_ACTOR: instance(f"{CALL_ACTOR}_2111986537", classes[CALL_ACTOR],
                             call_component),
        # The class default object of the UAV, which every live match carries
        # beside the real actors and which is never an entry (spec §6).
        "cdo": instance(f"Default__{UAV}", classes[UAV], component),
        # An actor parked at the world origin — the junk-actor rule of §2.
        "origin": instance(f"{UAV}_2111986539", classes[UAV],
                           origin_component),
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
                   caches=sn.SnapshotCaches(), arr=_Arr(slots), actors=actors,
                   classes=classes, config_cls=config_cls,
                   projectile_cls=projectile_cls, caller_ctrl=caller_ctrl,
                   owner_ctrl=owner_ctrl,
                   order=(CREEP, STRIKE, UAV, CALL_ACTOR))


def _common(fx, class_name: str, position=POSITION) -> dict:
    """The fields every command actor carries, off the native base."""
    return {
        "id": f"{fx.actors[class_name][0]:#x}",
        "class": class_name,
        "team": TEAM,
        "action": CONFIG,
        "callerEosId": CALLER_EOS,
        "position": {"x": position[0], "y": position[1], "z": position[2]},
        "yaw": pytest.approx(YAW),
        "actionDestroyed": False,
        "distance": DISTANCE,
    }


# ---- what each family emits ------------------------------------------------

def test_the_artillery_actor_carries_its_whole_fire_plan():
    """The ten artillery fields are declared on `ArtilleryBase`, two steps up
    the chain from the creep — the reader climbs to them, so the parent's row
    in the doctor watches every artillery actor at once (spec §8)."""
    fx = build()
    assert fx.actor(CREEP) == _common(fx, CREEP) | {
        "originLocation": {"x": ORIGIN[0], "y": ORIGIN[1], "z": ORIGIN[2]},
        "targetLocation": {"x": TARGET[0], "y": TARGET[1], "z": TARGET[2]},
        "maxDropRadius": MAX_DROP_RADIUS,
        "preWarningShells": PRE_WARNING_SHELLS,
        "preWarningDelaySec": PRE_WARNING_DELAY,
        "shellsPerBarrage": SHELLS_PER_BARRAGE,
        "barrageCount": BARRAGE_COUNT,
        "currentPrewarningShells": CURRENT_PREWARNING_SHELLS,
        "currentBarrage": CURRENT_BARRAGE,
        "projectile": PROJECTILE,
    }


def test_the_strike_aircraft_carries_its_run_and_no_health():
    """Four family fields and nothing else. `Health` and `Dead_0` are not
    recorded on this family (decision D18) — neither name is declared on the
    strike class here, so neither is read."""
    fx = build()
    assert fx.actor(STRIKE) == _common(fx, STRIKE) | {
        "shotsMade": SHOTS_MADE,
        "maxShots": MAX_SHOTS,
        "splineDistance": SPLINE_DISTANCE,
        "originLocation": {"x": ORIGIN[0], "y": ORIGIN[1], "z": ORIGIN[2]},
    }


def test_the_uav_carries_the_common_fields_and_nothing_more():
    """Spec §6: "none beyond the common fields". The UAV's own class declares
    nothing, and the artillery and strike values are sitting in its bytes at
    the very offsets the other families read them from."""
    fx = build()
    assert fx.actor(UAV) == _common(fx, UAV)
    uav_addr = fx.actors[UAV][0]
    assert struct.unpack("<i", fx.pm.read(uav_addr + MAX_SHOTS_OFF, 4))[0] \
        == MAX_SHOTS


def test_the_drone_call_actor_carries_its_health_and_owner():
    """`SQ PC` is a controller, one hop short of the player state the identity
    comes off — the commander who called the drone (T14, 2026-09-07)."""
    fx = build()
    assert fx.actor(CALL_ACTOR) == _common(
        fx, CALL_ACTOR, CALL_ACTOR_POSITION) | {
        "health": HEALTH,
        "ownerEosId": OWNER_EOS,
    }


@pytest.mark.parametrize("class_name", [CREEP, STRIKE, UAV, CALL_ACTOR])
def test_the_delay_beside_the_destroyed_flag_is_never_recorded(class_name):
    """`Destroy Delay after Action Destroyed` is declared next to the flag and
    watched by the doctor, and deliberately unread (spec §10): the linger is
    observed from the actor still being there, not from its config. Its value
    sits in every actor's bytes and reaches no key at all."""
    entry = build().actor(class_name)
    assert DESTROY_DELAY not in entry.values()


# ---- the rules the fields are emitted under --------------------------------

def test_a_shot_down_actor_reads_the_flag_through_its_mask():
    """`actionDestroyed` is a bitfield bool sharing its byte, so it is read
    through the FBoolProperty mask rather than a `& 1` (spec §6)."""
    assert build(destroyed=True).actor(UAV)["actionDestroyed"] is True
    assert build().actor(UAV)["actionDestroyed"] is False


def test_a_template_actors_null_pointers_read_null_not_missing():
    """The level's template actors exist for a second at a layer load with
    null pointers, and are recorded as read: `null` is the game's own empty,
    which is a different answer from a key that is not there (spec §2, §6)."""
    entry = build(action_null=True, caller_null=True).actor(CREEP)
    assert "action" in entry and entry["action"] is None
    assert "callerEosId" in entry and entry["callerEosId"] is None
    # Everything else is untouched by an empty pointer.
    assert entry["team"] == TEAM and entry["distance"] == DISTANCE


def test_a_stale_weak_pointer_omits_the_caller_rather_than_naming_a_ghost():
    """A recycled object-array slot bumps its serial. The weak pointer then
    reaches an object that is not the controller it named, so the key is
    absent — "unknown", never the wrong commander (spec §2)."""
    entry = build(caller_stale=True).actor(CREEP)
    assert "callerEosId" not in entry
    assert entry["action"] == CONFIG          # its neighbours are unharmed


def test_the_caller_is_resolved_through_the_object_array():
    """The pointer is a TWeakObjectPtr — an index and a serial, not an
    address — so nothing resolves without the array."""
    fx = build()
    assert fx.actor(CREEP)["callerEosId"] == CALLER_EOS
    assert "callerEosId" not in fx.actor(CREEP, arr=None)


def test_a_controller_that_reaches_no_player_state_omits_its_key():
    """Spec §2: a pointer that does not reach a player state gives an omitted
    field, where a pointer that reads null gives `null`."""
    fx = build()
    fx.paths.pc_playerstate_off = None
    entry = fx.actor(CALL_ACTOR)
    assert "callerEosId" not in entry and "ownerEosId" not in entry


# ---- the list ---------------------------------------------------------------

def test_the_list_holds_one_entry_per_live_actor():
    fx = build()
    assert [e["class"] for e in fx.actions()] == list(fx.order)


def test_a_class_default_object_is_never_an_entry():
    """Every match carries the CDO of each command-actor class beside the real
    ones; it is not a call and never reaches the list (spec §6)."""
    entries = build().actions(order=(CREEP, "cdo", UAV))
    assert [e["class"] for e in entries] == [CREEP, UAV]


def test_an_actor_at_the_world_origin_is_dropped():
    """The junk-actor rule of spec §2, the one the vehicles already use: a
    root position of exactly (0, 0, 0) is a freed or unplaced actor."""
    entries = build().actions(order=(CREEP, "origin"))
    assert [e["class"] for e in entries] == [CREEP]


def test_the_drone_call_actors_zeroed_x_and_y_keep_it_in_the_list():
    """It reads (0, 0, z) and means nothing by it; the exclusion is the raw
    triple, never a guess about which actor a position belongs to (spec §6)."""
    entries = build().actions(order=(CALL_ACTOR,))
    assert [e["class"] for e in entries] == [CALL_ACTOR]
    assert entries[0]["position"] == {"x": 0.0, "y": 0.0,
                                      "z": CALL_ACTOR_POSITION[2]}


def test_no_actors_means_an_empty_list_and_so_no_key():
    """`build_snapshot` writes `commandActions` only for a non-empty list, so
    a frame with no command asset in the air carries no key at all — which is
    what every recording made before this one says (spec §6)."""
    assert build().actions(order=()) == []
    assert build().actions(order=("cdo", "origin")) == []


# ---- reading by name, never by class name ----------------------------------

def test_the_families_are_found_by_name_not_by_class_name():
    """A strike class that gains the artillery names emits them, without its
    own name changing. Nothing anywhere matches on a class name, so the day
    Squad moves a field between the families the recorder follows it."""
    grown = build(strike_props=STRIKE_PROPS + ARTILLERY_PROPS).actor(STRIKE)
    assert grown["shotsMade"] == SHOTS_MADE
    assert grown["currentBarrage"] == CURRENT_BARRAGE
    assert grown["projectile"] == PROJECTILE


def test_every_name_the_reader_looks_for_reaches_a_key():
    """The layout walk looks for a fixed set of names; a name added to that
    set with no branch reading it would be a field silently missing from every
    recording. One actor declaring all of §6 at once catches it: one key out
    per name in, and no name listed twice."""
    every = (STRIKE_PROPS
             + [p for p in ARTILLERY_PROPS if p["name"] != "Origin Location"]
             + CALL_ACTOR_PROPS)
    entry = build(strike_props=every).actor(STRIKE)
    keys = set(entry) - {"id", "class", "position", "yaw"}
    assert len(sn.COMMAND_ACTOR_NAMES) == len(set(sn.COMMAND_ACTOR_NAMES))
    assert len(keys) == len(sn.COMMAND_ACTOR_NAMES)


@pytest.mark.parametrize("renamed, gone", [
    ("Distance", "distance"),
    ("Team", "team"),
    ("Action", "action"),
    ("DamageInstigatorController", "callerEosId"),
    ("Action Destroyed", "actionDestroyed"),
    ("Current Barrage", "currentBarrage"),
    ("Origin Location", "originLocation"),
    ("Projectile", "projectile"),
])
def test_a_renamed_property_omits_its_key_and_leaves_the_rest(renamed, gone):
    """A Squad rename is the case these reads have no fallback for: the name
    is simply not on the class any more. The key vanishes — never a zero,
    never last frame's value — and its neighbours are untouched."""
    fx = build(omit=frozenset({renamed}))
    entry = fx.actor(CREEP)
    assert gone not in entry
    assert entry["id"] == f"{fx.actors[CREEP][0]:#x}" and entry["class"] == CREEP
    assert entry["position"] == {"x": POSITION[0], "y": POSITION[1],
                                 "z": POSITION[2]}
    assert entry["maxDropRadius"] == MAX_DROP_RADIUS


def test_a_figure_is_read_as_the_type_reflection_reports():
    """Spec §2, "Numbers": the type follows the reflected property. `Distance`
    is a Float on the command actors where the markers' is a Double, and a
    name that came back as something else entirely — the shape a repurpose
    would take — is omitted rather than decoded as a number anyway."""
    odd = [
        {"name": "Spline Distance", "type_name": "ObjectProperty",
         "offset": SPLINE_DISTANCE_OFF},
        {"name": "Origin Location", "type_name": "DoubleProperty",
         "offset": ORIGIN_OFF},
    ]
    entry = build(strike_props=odd).actor(STRIKE)
    assert "splineDistance" not in entry
    # A vector is read as a vector only while reflection still says "struct":
    # this one comes back as the double sitting at the same address instead.
    assert "originLocation" not in entry
    assert entry["distance"] == DISTANCE      # still a Float, still read


# ---- the cache -------------------------------------------------------------

def _count_layout_walks(monkeypatch):
    calls: list[int] = []
    real = sn.get_class_layout

    def counting(pm, class_addr, alloc):
        calls.append(class_addr)
        return real(pm, class_addr, alloc)

    monkeypatch.setattr(sn, "get_class_layout", counting)
    return calls


def test_a_command_actor_class_is_reflected_once_however_many_actors(
        monkeypatch):
    fx = build()
    calls = _count_layout_walks(monkeypatch)
    for _tick in range(3):
        fx.actions()
    assert len(calls) == 4                     # one walk per class, ever
    assert len(fx.caches.command_actor_props) == 4


def test_a_generation_bump_re_reflects_the_class(monkeypatch):
    """The rolling reset is the retry for a reflection walk that failed
    mid-tick: a cached answer must not outlive it. The stale entry is dropped
    as it goes, so the classes of a call that ended do not sit here for the
    rest of the process's life — and they churn harder than most, existing
    only while a call is in the air."""
    fx = build()
    calls = _count_layout_walks(monkeypatch)
    fx.actor(CREEP)
    assert len(fx.caches.command_actor_props) == 1
    fx.caches._light_reset(tick=60, reason="rolling")
    assert fx.caches.command_actor_props == {}
    fx.actor(CREEP)
    assert len(calls) == 2


def test_the_reader_runs_without_caches_at_all():
    """`caches` is optional on every other per-actor read; a command actor read
    without one still emits, it just reflects each time."""
    entry = build().actor(CREEP, caches=None)
    assert entry["distance"] == DISTANCE and entry["action"] == CONFIG
    assert entry["callerEosId"] == CALLER_EOS
