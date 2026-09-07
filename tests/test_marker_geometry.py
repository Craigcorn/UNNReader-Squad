"""`markers[].distance`, `.addDistance`, `.action` and `.yaw` — read off
each marker's OWN class layout (spec §5).

The whole rule of this surface is that the fields are found BY NAME on
whatever class the marker happens to be, never by matching that class's
name, so the image here carries real reflection data: UClass objects with
linked FProperty chains AND a SuperStruct chain. The subclasses declare
nothing of their own and inherit the master's properties, exactly as the
archived layouts show them — which is also why two doctor rows on the two
masters cover every marker family.

Its own image builder rather than the commander block's: this needs a
super chain and nothing else (no structs, no FastArrays, no FText), and
that fixture needs the reverse.

The absence rules of spec §2 are the other half. A key is emitted when the
read succeeds, `null` when the game's own value is empty — a request
marker's `Action` pointer reads null — and ABSENT when the recorder could
not read, which is what a Squad rename looks like. So the rename tests
drop the property from the class, never a key from a dict.
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

MARKER_SIZE = 0x340
DISTANCE_OFF = 0x300
ADD_DISTANCE_OFF = 0x308
ACTION_OFF = 0x310
REQUEST_OFF = 0x318          # read by nobody: spec §10 keeps `Request` out

ROOT_COMPONENT_OFF = 0x160
COMPONENT_SIZE = 0x200
TO_WORLD_ROTATION_OFF = 0x80
TO_WORLD_TRANSLATION_OFF = 0xA0
RELATIVE_ROTATION_OFF = 0xC0

POSITION = (12345.5, -6789.25, 42.0)
YAW = 45.0

MASTER = "BP_MapMarker_CommandMaster_C"
FOOTPRINT = "BP_MapMarker_CommandRadius_Friendly_C"
REQUEST = "BP_MapMarker_Command_SLRequest_C"
DIRECTOR_MASTER = "BP_MapMarker_DirectorMaster_C"
DIRECTOR = "BP_MapMarker_Director_Attack_C"
PLAIN = "BP_AAS_AttackMarker_C"
CONFIG = "CommandAction_UAV_MQ9_USMC_C"


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
               size: int = MARKER_SIZE) -> int:
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


# ---- the marker classes, as §13 reflects them ------------------------------

#: The four names the Command family declares on its master. `Request` is
#: reflected and deliberately not recorded (spec §10); it sits here so the
#: fixture's class is the class the box reports, not a trimmed one.
COMMAND_MASTER_PROPS = [
    {"name": "Distance", "type_name": "DoubleProperty", "offset": DISTANCE_OFF},
    {"name": "AddDistance", "type_name": "DoubleProperty",
     "offset": ADD_DISTANCE_OFF},
    {"name": "Action", "type_name": "ClassProperty", "offset": ACTION_OFF},
    {"name": "Request", "type_name": "BoolProperty", "offset": REQUEST_OFF,
     "bits": (0, 0x01)},
]
DIRECTOR_MASTER_PROPS = [
    {"name": "Distance", "type_name": "DoubleProperty", "offset": DISTANCE_OFF},
]


def _quat_yaw(deg: float) -> bytes:
    """ComponentToWorld's FQuat for a pure yaw — the transform the position
    is read from, so the heading has to come from the same one."""
    half = math.radians(deg) / 2.0
    return struct.pack("<dddd", 0.0, 0.0, math.sin(half), math.cos(half))


class Fixture(SimpleNamespace):
    def marker(self, class_name: str, **kw):
        """`read_marker`'s output for one of the markers built below."""
        addr, cls_addr = self.markers[class_name]
        return sn.read_marker(self.pm, self.names, self.paths, addr,
                              class_name, cls_addr,
                              kw.pop("caches", self.caches), **kw)


def build(*, omit: frozenset[str] = frozenset(), action_null: bool = False,
          director_props: list[dict] | None = None,
          markers: tuple[str, ...] = (FOOTPRINT, REQUEST, DIRECTOR, PLAIN)
          ) -> Fixture:
    """One marker per class, all reading the same position and heading.

    `omit` drops a property from a master the way a Squad rename does —
    from the CLASS, so the reader looks for a name that is not there;
    `action_null` empties the footprint's `Action` pointer;
    `director_props` re-declares the Director master, to show that what a
    marker emits follows its class's properties and not its class's name.
    """
    img = _Image()

    def keep(specs):
        return [s for s in specs if s["name"] not in omit]

    root_cls = img.uclass("SQMapMarker", [])
    command_master = img.uclass(MASTER, keep(COMMAND_MASTER_PROPS),
                                super_addr=root_cls)
    director_master = img.uclass(
        DIRECTOR_MASTER,
        keep(DIRECTOR_MASTER_PROPS if director_props is None else director_props),
        super_addr=root_cls)
    classes = {
        FOOTPRINT: img.uclass(FOOTPRINT, [], super_addr=command_master),
        REQUEST: img.uclass(REQUEST, [], super_addr=command_master),
        DIRECTOR: img.uclass(DIRECTOR, [], super_addr=director_master),
        PLAIN: img.uclass(PLAIN, [], super_addr=root_cls),
    }
    # The CommandAction_* config class the footprint's `Action` points at.
    config_cls = img.uclass(CONFIG, [])

    comp = bytearray(COMPONENT_SIZE)
    comp[TO_WORLD_ROTATION_OFF:TO_WORLD_ROTATION_OFF + 32] = _quat_yaw(YAW)
    struct.pack_into("<ddd", comp, TO_WORLD_TRANSLATION_OFF, *POSITION)
    struct.pack_into("<ddd", comp, RELATIVE_ROTATION_OFF, 0.0, YAW, 0.0)
    component = img.alloc(bytes(comp))

    owner = img.alloc(bytes(0x40))
    built: dict[str, tuple[int, int]] = {}
    for name in markers:
        b = bytearray(MARKER_SIZE)
        b[sn.MARKER_OFFSETS["Team"]] = 1
        struct.pack_into("<i", b, sn.MARKER_OFFSETS["Squad"], 3)
        struct.pack_into("<i", b, sn.MARKER_OFFSETS["FireTeamId"], 0)
        struct.pack_into("<Q", b, sn.MARKER_OFFSETS["OwnerPlayerState"], owner)
        struct.pack_into("<Q", b, ROOT_COMPONENT_OFF, component)
        # Every marker but the request carries the same bytes at the three
        # offsets — the UAV coverage marker's chosen radius and the outer
        # band beside it (the figures of 2026-09-07), and a config pointer.
        # What comes out is decided by the class's properties alone, so the
        # marker that declares none of them has real values sitting there
        # and still emits nothing.
        if name != REQUEST:
            struct.pack_into("<d", b, DISTANCE_OFF, 16608.0)
            struct.pack_into("<d", b, ADD_DISTANCE_OFF, 7500.0)
            if not (name == FOOTPRINT and action_null):
                struct.pack_into("<Q", b, ACTION_OFF, config_cls)
        b[REQUEST_OFF] = 0x01 if name == REQUEST else 0x00
        built[name] = (img.alloc(bytes(b)), classes[name])

    paths = SimpleNamespace(
        actor_root_component_off=ROOT_COMPONENT_OFF,
        scene_component_to_world_translation_off=TO_WORLD_TRANSLATION_OFF,
        scene_component_to_world_rotation_off=TO_WORLD_ROTATION_OFF,
        scene_relative_rotation_off=RELATIVE_ROTATION_OFF,
    )
    return Fixture(img=img, pm=img.pm, names=img.names, paths=paths,
                   caches=sn.SnapshotCaches(), markers=built,
                   classes=classes, owner=owner, config_cls=config_cls)


# ---- what each family emits ------------------------------------------------

def test_a_command_footprint_carries_all_four_fields():
    fx = build()
    assert fx.marker(FOOTPRINT) == {
        "id": f"{fx.markers[FOOTPRINT][0]:#x}",
        "type": FOOTPRINT,
        "team": 1,
        "squad": 3,
        "fireTeamId": 0,
        "ownerPlayerStateAddr": f"{fx.owner:#x}",
        "distance": 16608.0,
        "addDistance": 7500.0,
        # The join to `commander.cooldowns.actions[].action`: the config
        # class the footprint belongs to, named through the class cache.
        "action": CONFIG,
        "position": {"x": POSITION[0], "y": POSITION[1], "z": POSITION[2]},
        "yaw": pytest.approx(YAW),
    }


def test_the_director_family_carries_distance_and_yaw_alone():
    """`BP_MapMarker_DirectorMaster_C` declares `Distance` and nothing else,
    so the other two keys are absent — not null, absent: the class does not
    have the field to read."""
    m = build().marker(DIRECTOR)
    assert m["distance"] == 16608.0
    assert m["yaw"] == pytest.approx(YAW)
    assert "addDistance" not in m
    assert "action" not in m


def test_a_marker_outside_both_families_gets_no_geometry_at_all():
    """The ordinary actor markers every layer carries declare none of the
    three names, and nothing is invented for them. The squad-data markers'
    `arrowLength` / `arrowHeading` are a different surface entirely and are
    not reused here (spec §5)."""
    fx = build()
    assert fx.marker(PLAIN) == {
        "id": f"{fx.markers[PLAIN][0]:#x}",
        "type": PLAIN,
        "team": 1,
        "squad": 3,
        "fireTeamId": 0,
        "ownerPlayerStateAddr": f"{fx.owner:#x}",
        "position": {"x": POSITION[0], "y": POSITION[1], "z": POSITION[2]},
    }


def test_a_request_markers_action_reads_null_not_missing():
    """`null` and absent are different answers (spec §2). A request marker
    belongs to no config and its pointer reads null — that is the game's own
    empty, read successfully, so the key is there carrying null."""
    m = build().marker(REQUEST)
    assert "action" in m and m["action"] is None
    # Its geometry reads zero rather than being absent: the class carries
    # the fields, the game just has nothing in them.
    assert m["distance"] == 0.0
    assert m["addDistance"] == 0.0


def test_a_footprint_whose_pointer_empties_reads_null_too():
    m = build(action_null=True).marker(FOOTPRINT)
    assert m["action"] is None


# ---- the rules the fields are emitted under --------------------------------

@pytest.mark.parametrize("class_name", [FOOTPRINT, REQUEST, DIRECTOR, PLAIN])
def test_yaw_rides_exactly_where_distance_does(class_name):
    """Spec §5: `yaw` is emitted whenever `distance` is. Every marker here
    reads the same root transform, so the only thing deciding the heading is
    whether its class declares `Distance`."""
    m = build().marker(class_name)
    assert ("yaw" in m) == ("distance" in m)


def test_the_families_are_found_by_name_not_by_class_name():
    """A Director class that gains `AddDistance` and `Action` emits them, and
    a Command class that loses `Distance` stops emitting it — without either
    class's NAME changing. Nothing anywhere matches on a class name, so the
    day Squad moves a field between the families the recorder follows it."""
    grown = build(director_props=COMMAND_MASTER_PROPS).marker(DIRECTOR)
    assert grown["distance"] == 16608.0
    assert grown["addDistance"] == 7500.0
    assert grown["action"] == CONFIG
    shrunk = build(omit=frozenset({"Distance"})).marker(FOOTPRINT)
    assert "distance" not in shrunk
    assert shrunk["addDistance"] == 7500.0 and shrunk["action"] == CONFIG


def test_a_marker_declaring_nothing_emits_nothing_from_the_same_bytes():
    """The bytes at the three offsets are the same on every marker in the
    image; the plain marker's class simply does not name them, so no key
    appears. That is the difference between reading by name and reading a
    constant — and it is the whole reason no offset is hardcoded here."""
    fx = build()
    plain_addr, _cls = fx.markers[PLAIN]
    assert struct.unpack("<d", fx.pm.read(plain_addr + DISTANCE_OFF, 8))[0] \
        == 16608.0
    for key in ("distance", "addDistance", "action", "yaw"):
        assert key not in fx.marker(PLAIN)


@pytest.mark.parametrize("renamed, gone, left", [
    ("Distance", ("distance", "yaw"), {"addDistance": 7500.0, "action": CONFIG}),
    ("AddDistance", ("addDistance",), {"distance": 16608.0, "action": CONFIG}),
    ("Action", ("action",), {"distance": 16608.0, "addDistance": 7500.0}),
])
def test_a_renamed_property_omits_its_key_and_leaves_the_rest(
        renamed, gone, left):
    """A Squad rename is the case these reads have no fallback for: the name
    is simply not on the class any more. The key vanishes — never a zero,
    never last frame's value — and its neighbours are untouched."""
    m = build(omit=frozenset({renamed})).marker(FOOTPRINT)
    for key in gone:
        assert key not in m
    for key, value in left.items():
        assert m[key] == value
    # The rest of the marker is unharmed either way.
    assert m["type"] == FOOTPRINT and m["team"] == 1
    assert m["position"] == {"x": POSITION[0], "y": POSITION[1],
                             "z": POSITION[2]}


def test_yaw_goes_with_distance_when_the_name_is_renamed_away():
    m = build(omit=frozenset({"Distance"})).marker(FOOTPRINT)
    assert "yaw" not in m and "position" in m


def test_a_figure_is_read_as_the_type_reflection_reports():
    """Spec §2, "Numbers": the type follows the reflected property. Both
    figures reflect as doubles on the box and are read as doubles here; a
    name that came back as something else entirely — the shape a repurpose
    would take — is omitted rather than decoded as a number anyway."""
    same_name_other_type = [
        {"name": "Distance", "type_name": "FloatProperty",
         "offset": DISTANCE_OFF},
        {"name": "AddDistance", "type_name": "ObjectProperty",
         "offset": ADD_DISTANCE_OFF},
    ]
    m = build(director_props=same_name_other_type).marker(DIRECTOR)
    # A float read of the low half of that double, not the double: the value
    # follows reflection wherever it leads.
    assert m["distance"] == pytest.approx(
        struct.unpack("<f", struct.pack("<d", 16608.0)[:4])[0])
    assert "addDistance" not in m


# ---- the cache -------------------------------------------------------------

def _count_layout_walks(monkeypatch):
    calls: list[int] = []
    real = sn.get_class_layout

    def counting(pm, class_addr, alloc):
        calls.append(class_addr)
        return real(pm, class_addr, alloc)

    monkeypatch.setattr(sn, "get_class_layout", counting)
    return calls


def test_a_marker_class_is_reflected_once_however_many_markers(monkeypatch):
    fx = build()
    calls = _count_layout_walks(monkeypatch)
    for _tick in range(3):
        for name in (FOOTPRINT, REQUEST, DIRECTOR, PLAIN):
            fx.marker(name)
    assert len(calls) == 4                     # one walk per class, ever
    assert len(fx.caches.marker_geometry) == 4


def test_the_absence_of_the_names_is_cached_too(monkeypatch):
    """Most of the 163 marker classes carry none of the three names, and
    re-walking them every tick to learn that again is the cost this cache
    exists to avoid — so the empty answer is stored like any other."""
    fx = build()
    calls = _count_layout_walks(monkeypatch)
    fx.marker(PLAIN)
    fx.marker(PLAIN)
    assert len(calls) == 1
    _gen, props = fx.caches.marker_geometry[fx.classes[PLAIN]]
    assert props == {}


def test_a_generation_bump_re_reflects_the_class(monkeypatch):
    """The rolling reset is the retry for a reflection walk that failed
    mid-tick: a cached empty answer must not outlive it."""
    fx = build()
    calls = _count_layout_walks(monkeypatch)
    fx.marker(FOOTPRINT)
    fx.caches._light_reset(tick=60, reason="rolling")
    fx.marker(FOOTPRINT)
    assert len(calls) == 2


def test_the_reader_runs_without_caches_at_all():
    """`caches` is optional on every other per-actor read; a marker read
    without one still emits, it just reflects each time."""
    fx = build()
    m = fx.marker(FOOTPRINT, caches=None)
    assert m["distance"] == 16608.0 and m["action"] == CONFIG
