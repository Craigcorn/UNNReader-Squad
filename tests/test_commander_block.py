"""The commander block, the rules, and the seat identity — read off a memory
image shaped like the live one.

Everything here goes through the REAL reflection walk: the fake process holds
UClass and UScriptStruct objects with linked FProperty chains, so
`resolve_commander_paths` resolves the offsets, the FastArray `Items` hops and
the strides exactly as it does on the box, and the readers then run against
instances laid out at those offsets. Two things in particular are only worth
testing this way:

  * the strides. `CommandIntervals` and `NomineeStatus` are FastArrays whose
    entries sit in the `Items` array inside the wrapper struct, and the
    element size is the inner struct's REFLECTED size — a guessed stride is
    what cost the 09-02 probe a sample. `LastCategoryGameTime` is the other
    case: its array property reports ElementSize 0 on this build, so the
    stride has to come from the element type instead (the image reproduces
    that zero).
  * the emission rules of the spec's §2. A key is emitted when the read
    succeeds, `null` when the game's own value is empty (an unclaimed seat, a
    category with no stamp yet), and ABSENT when the recorder could not read
    — which is what a renamed property looks like, so the "missing name"
    tests drop the property from the class rather than from a dict.
"""
from __future__ import annotations

import struct
from types import SimpleNamespace

from sqreader.squad import snapshot as sn
from sqreader.ue.uobject import UOBJ_NAME_PRIVATE

from conftest import FakeProcessMemory

# ---- a process image with reflection data in it ----------------------------

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
FPROP_STRUCT = 0x70          # FStructProperty.Struct / FBoolProperty's bytes
FPROP_INNER = 0x78           # FArrayProperty.Inner


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
    """The object array, for the `Default__<class>` lookups only."""

    def __init__(self, objects: dict[str, int]) -> None:
        self.objects = objects
        self.walks = 0

    def find_all_by_names(self, targets, *, alloc):
        self.walks += 1
        return {n: (0, self.objects[n]) for n in targets if n in self.objects}


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
             elem_size: int = 0, struct_ptr: int = 0, inner: int = 0,
             bits: tuple[int, int] | None = None) -> int:
        b = bytearray(0x80)
        struct.pack_into("<Q", b, FFIELD_CLASS, self.field_class(type_name))
        struct.pack_into("<Q", b, FFIELD_NEXT, nxt)
        struct.pack_into("<II", b, FFIELD_NAME, self.names.ci(name), 0)
        struct.pack_into("<i", b, FPROP_ARRAY_DIM, 1)
        struct.pack_into("<i", b, FPROP_ELEM_SIZE, elem_size)
        struct.pack_into("<i", b, FPROP_OFFSET, offset)
        if bits is not None:
            byte_off, mask = bits
            b[FPROP_STRUCT] = 1            # FieldSize
            b[FPROP_STRUCT + 1] = byte_off  # ByteOffset
            b[FPROP_STRUCT + 2] = mask      # ByteMask
            b[FPROP_STRUCT + 3] = mask      # FieldMask
        else:
            struct.pack_into("<Q", b, FPROP_STRUCT, struct_ptr)
            struct.pack_into("<Q", b, FPROP_INNER, inner)
        return self.alloc(bytes(b))

    def type_obj(self, name: str, specs: list[dict], *, size: int) -> int:
        """A UClass / UScriptStruct with `specs` as its property chain."""
        nxt = 0
        for spec in reversed(specs):
            nxt = self.prop(nxt=nxt, **spec)
        b = bytearray(0x60)
        struct.pack_into("<II", b, UOBJ_NAME_PRIVATE, self.names.ci(name), 0)
        struct.pack_into("<Q", b, USTRUCT_SUPER, 0)
        struct.pack_into("<Q", b, USTRUCT_CHILD_PROPS, nxt)
        struct.pack_into("<i", b, USTRUCT_PROPS_SIZE, size)
        struct.pack_into("<i", b, USTRUCT_MIN_ALIGN, 8)
        return self.alloc(bytes(b))

    def uclass_named(self, name: str, specs: list[dict], *, size: int) -> int:
        return self.type_obj(name, specs, size=size)

    def fstring(self, text: str | None) -> bytes:
        """The 16-byte FString header, with its buffer allocated."""
        if text is None:
            return struct.pack("<Qii", 0, 0, 0)
        ptr = self.alloc((text + "\0").encode("utf-16-le"))
        return struct.pack("<Qii", ptr, len(text) + 1, len(text) + 1)

    def ftext(self, text: str) -> bytes:
        """The 16 bytes an FText occupies: a pointer to its text data."""
        data = bytearray(0x40)
        data[0x20:0x30] = self.fstring(text)
        return struct.pack("<Q", self.alloc(bytes(data))) + bytes(8)


def _tarray(ptr: int, count: int) -> bytes:
    return struct.pack("<QII", ptr, count, count)


# ---- the classes, laid out the way §13 reflects them -----------------------

# Offsets are this fixture's own; nothing reads them but the image itself.
CS_CURRENT = 0x2C0
CS_FLAGS = 0x2C8
CS_VOTE_TIMER = 0x2D0
CS_VOTE_STAMP = 0x2D4
CS_CD_TIMER = 0x2D8
CS_CD_STAMP = 0x2DC
CS_CATEGORIES = 0x2E0
CS_LAST_USE = 0x2F0
CS_INTERVALS = 0x300
CS_NOMINEES = 0x340
FAS_ITEMS_OFF = 0x10         # Items, inside either FastArray wrapper
FAS_CONTENT_OFF = 0x10       # Content, inside SQCommandActionDataFASItem
PS_NAME = 0x30
PS_EOS = 0x50
TS_ID = 0x10
TS_COMMANDER_STATE = 0x20
MGR_FLAGS = 0x100
MGR_VOTING = 0x104
MGR_COOLDOWN = 0x108
MGR_EXTENSION = 0x10C
MGR_MIN_SIZE = 0x110
MGR_MIN_SQUADS = 0x114
CFG_CATEGORY_ID = 0x30
CFG_ENROUTE = 0x34
CFG_ACTIVE = 0x38
CFG_COOLDOWN = 0x3C
CFG_DISPLAY = 0x40

CATEGORY_STRIDE = 24
NOMINEE_STRIDE = 32
INTERVAL_STRIDE = 40

UAV = "CommandAction_UAV_MQ9_USMC_C"
CREEP = "CommandAction_Artillery_Creep_USMC_C"
CONFIGS = {
    UAV: (1, 60.0, 330.0, 600.0, "MQ-9 UAV Recon"),
    CREEP: (1, 60.0, 120.0, 900.0, "Creeping Barrage"),
}


def _classes(img: _Image, omit: frozenset[str]) -> tuple[int, int, dict[str, int]]:
    """The commander classes and the two action configs. `omit` drops a
    property from its class the way a Squad rename does."""
    def keep(specs):
        return [s for s in specs if s["name"] not in omit]

    category = img.type_obj("CommanderCategory", keep([
        {"name": "Name", "type_name": "TextProperty", "offset": 0},
        {"name": "CooldownDuration", "type_name": "FloatProperty", "offset": 16},
    ]), size=CATEGORY_STRIDE)
    nominee = img.type_obj("CommanderVoteNominee", keep([
        {"name": "NomineeState", "type_name": "ObjectProperty", "offset": 0},
        {"name": "VoteCount", "type_name": "IntProperty", "offset": 8},
    ]), size=NOMINEE_STRIDE)
    action_data = img.type_obj("SQCommandActionData", keep([
        {"name": "CommandActionData", "type_name": "ClassProperty", "offset": 0},
        {"name": "GameTimeAtCreation", "type_name": "FloatProperty", "offset": 8},
        {"name": "CooldownTimeRemaining", "type_name": "FloatProperty", "offset": 12},
        {"name": "IsDestroyedDuringActive", "type_name": "BoolProperty",
         "offset": 16, "bits": (0, 0x01)},
    ]), size=24)
    fas_item = img.type_obj("SQCommandActionDataFASItem", keep([
        {"name": "Content", "type_name": "StructProperty",
         "offset": FAS_CONTENT_OFF, "struct_ptr": action_data},
    ]), size=INTERVAL_STRIDE)
    intervals = img.type_obj("SQCommanderActionDataArray", keep([
        {"name": "Items", "type_name": "ArrayProperty", "offset": FAS_ITEMS_OFF,
         "inner": img.prop("Items", "StructProperty", 0, struct_ptr=fas_item)},
    ]), size=56)
    nominees = img.type_obj("CommanderNomineeArray", keep([
        {"name": "Items", "type_name": "ArrayProperty", "offset": FAS_ITEMS_OFF,
         "inner": img.prop("Items", "StructProperty", 0, struct_ptr=nominee)},
    ]), size=56)
    state = img.uclass_named("SQCommanderState", keep([
        {"name": "CurrentCommander", "type_name": "ObjectProperty",
         "offset": CS_CURRENT},
        {"name": "bCommanderIsActive", "type_name": "BoolProperty",
         "offset": CS_FLAGS, "bits": (0, 0x01)},
        {"name": "bActionsEnabled", "type_name": "BoolProperty",
         "offset": CS_FLAGS, "bits": (0, 0x02)},
        {"name": "bVoteInProgress", "type_name": "BoolProperty",
         "offset": CS_FLAGS, "bits": (0, 0x04)},
        {"name": "bVoteCooldownActive", "type_name": "BoolProperty",
         "offset": CS_FLAGS, "bits": (0, 0x08)},
        {"name": "CommanderVoteTimer", "type_name": "IntProperty",
         "offset": CS_VOTE_TIMER},
        {"name": "CommanderVoteTimestamp", "type_name": "IntProperty",
         "offset": CS_VOTE_STAMP},
        {"name": "VoteCooldownTimer", "type_name": "IntProperty",
         "offset": CS_CD_TIMER},
        {"name": "VoteCooldownTimestamp", "type_name": "IntProperty",
         "offset": CS_CD_STAMP},
        {"name": "CommanderCategories", "type_name": "ArrayProperty",
         "offset": CS_CATEGORIES,
         "inner": img.prop("CommanderCategories", "StructProperty", 0,
                           struct_ptr=category)},
        # The array property whose ElementSize reads 0 on this build: the
        # stride has to come from the element TYPE, not from the header.
        {"name": "LastCategoryGameTime", "type_name": "ArrayProperty",
         "offset": CS_LAST_USE,
         "inner": img.prop("LastCategoryGameTime", "FloatProperty", 0,
                           elem_size=0)},
        {"name": "CommandIntervals", "type_name": "StructProperty",
         "offset": CS_INTERVALS, "struct_ptr": intervals},
        {"name": "NomineeStatus", "type_name": "StructProperty",
         "offset": CS_NOMINEES, "struct_ptr": nominees},
    ]), size=0x400)
    manager = img.uclass_named("SQCommanderManager", keep([
        {"name": "bCommanderActive", "type_name": "BoolProperty",
         "offset": MGR_FLAGS, "bits": (0, 0x01)},
        {"name": "VotingTimeSeconds", "type_name": "IntProperty",
         "offset": MGR_VOTING},
        {"name": "VoteCooldownTimeSeconds", "type_name": "IntProperty",
         "offset": MGR_COOLDOWN},
        {"name": "ActionCooldownExtensionOnNewCommander",
         "type_name": "FloatProperty", "offset": MGR_EXTENSION},
        {"name": "MinimumSquadSizeForVoting", "type_name": "IntProperty",
         "offset": MGR_MIN_SIZE},
        {"name": "MinimumSquadsRequiredForVoting", "type_name": "IntProperty",
         "offset": MGR_MIN_SQUADS},
    ]), size=0x200)
    config_classes = {
        name: img.uclass_named(name, keep([
            {"name": "CategoryId", "type_name": "ByteProperty",
             "offset": CFG_CATEGORY_ID},
            {"name": "EnrouteDuration", "type_name": "FloatProperty",
             "offset": CFG_ENROUTE},
            {"name": "ActiveDuration", "type_name": "FloatProperty",
             "offset": CFG_ACTIVE},
            {"name": "CooldownDuration", "type_name": "FloatProperty",
             "offset": CFG_COOLDOWN},
            {"name": "DisplayName", "type_name": "StrProperty",
             "offset": CFG_DISPLAY},
        ]), size=0x100)
        for name in CONFIGS
    }
    return state, manager, config_classes


def _player_state(img: _Image, name: str, eos: str) -> int:
    b = bytearray(0x80)
    b[PS_NAME:PS_NAME + 16] = img.fstring(name)
    b[PS_EOS:PS_EOS + 16] = img.fstring(eos)
    return img.alloc(bytes(b))


DEFAULT_ACTIONS = [
    # (config class name or None, createdGameTime, remaining, destroyed)
    (UAV, 100.5, 0.0, False),
    (CREEP, 250.0, 42.5, True),
]
DEFAULT_CATEGORIES = [("Air Support", 300.0), ("Artillery", 600.0)]
DEFAULT_LAST_USE = [120.5]          # only the first category has been called


class Fixture(SimpleNamespace):
    pass


def build(*, seated: bool = True, omit: frozenset[str] = frozenset(),
          categories=DEFAULT_CATEGORIES, last_use=DEFAULT_LAST_USE,
          actions=DEFAULT_ACTIONS, nominees=(("Ruby", 3), ("Doc", 1)),
          cdos: frozenset[str] | None = None) -> Fixture:
    img = _Image()
    state_cls, manager_cls, config_classes = _classes(img, omit)
    commander = _player_state(img, "Six", "eos-" + "6" * 28)
    nominee_states = [_player_state(img, nm, f"eos-{nm.lower()}") if nm else 0
                      for nm, _votes in nominees]

    cats = bytearray()
    for name, interval in categories:
        e = bytearray(CATEGORY_STRIDE)
        e[0:16] = img.ftext(name)
        struct.pack_into("<f", e, 16, interval)
        cats += e
    cats_ptr = img.alloc(bytes(cats)) if cats else 0
    last_ptr = img.alloc(struct.pack(f"<{len(last_use)}f", *last_use)) if last_use else 0

    items = bytearray()
    for cls_name, created, remaining, destroyed in actions:
        e = bytearray(INTERVAL_STRIDE)
        base = FAS_CONTENT_OFF
        struct.pack_into("<Q", e, base + 0,
                         config_classes[cls_name] if cls_name else 0)
        struct.pack_into("<f", e, base + 8, created)
        struct.pack_into("<f", e, base + 12, remaining)
        e[base + 16] = 0x01 if destroyed else 0x00
        items += e
    items_ptr = img.alloc(bytes(items)) if items else 0

    noms = bytearray()
    for (_nm, votes), ps in zip(nominees, nominee_states, strict=True):
        e = bytearray(NOMINEE_STRIDE)
        struct.pack_into("<Q", e, 0, ps)
        struct.pack_into("<i", e, 8, votes)
        noms += e
    noms_ptr = img.alloc(bytes(noms)) if noms else 0

    st = bytearray(0x400)
    struct.pack_into("<Q", st, CS_CURRENT, commander if seated else 0)
    st[CS_FLAGS] = 0x01 | 0x02 | 0x04        # active, actions enabled, vote open
    struct.pack_into("<i", st, CS_VOTE_TIMER, 47)
    struct.pack_into("<i", st, CS_VOTE_STAMP, 1234)
    struct.pack_into("<i", st, CS_CD_TIMER, 0)
    struct.pack_into("<i", st, CS_CD_STAMP, 900)
    st[CS_CATEGORIES:CS_CATEGORIES + 16] = _tarray(cats_ptr, len(categories))
    st[CS_LAST_USE:CS_LAST_USE + 16] = _tarray(last_ptr, len(last_use))
    off = CS_INTERVALS + FAS_ITEMS_OFF
    st[off:off + 16] = _tarray(items_ptr, len(actions))
    off = CS_NOMINEES + FAS_ITEMS_OFF
    st[off:off + 16] = _tarray(noms_ptr, len(nominees))
    state = img.alloc(bytes(st))

    mgr = bytearray(0x200)
    mgr[MGR_FLAGS] = 0x01
    struct.pack_into("<i", mgr, MGR_VOTING, 60)
    struct.pack_into("<i", mgr, MGR_COOLDOWN, 300)
    struct.pack_into("<f", mgr, MGR_EXTENSION, 300.0)
    struct.pack_into("<i", mgr, MGR_MIN_SIZE, 2)
    struct.pack_into("<i", mgr, MGR_MIN_SQUADS, 3)
    manager = img.alloc(bytes(mgr))

    ts = bytearray(0x40)
    struct.pack_into("<i", ts, TS_ID, 1)
    struct.pack_into("<Q", ts, TS_COMMANDER_STATE, state)
    team = img.alloc(bytes(ts))

    # The class default objects the action configs are read from. `cdos`
    # names which ones exist; by default both do.
    objects: dict[str, int] = {}
    for name in config_classes:
        if cdos is not None and name not in cdos:
            continue
        cdo = bytearray(0x100)
        cat_id, enroute, active, cooldown, display = CONFIGS[name]
        cdo[CFG_CATEGORY_ID] = cat_id
        struct.pack_into("<f", cdo, CFG_ENROUTE, enroute)
        struct.pack_into("<f", cdo, CFG_ACTIVE, active)
        struct.pack_into("<f", cdo, CFG_COOLDOWN, cooldown)
        cdo[CFG_DISPLAY:CFG_DISPLAY + 16] = img.fstring(display)
        objects[f"Default__{name}"] = img.alloc(bytes(cdo))

    cp = sn.resolve_commander_paths(img.pm, img.names, state_cls, manager_cls)
    paths = SimpleNamespace(
        commander=cp,
        ts_offsets={"ID": TS_ID, "CommanderState": TS_COMMANDER_STATE},
        ps_offsets={"PlayerNamePrivate": PS_NAME, "OnlineUserId": PS_EOS},
    )
    return Fixture(img=img, pm=img.pm, alloc=img.names, arr=_Arr(objects),
                   paths=paths, cp=cp, state=state, manager=manager, team=team,
                   commander_ps=commander, config_classes=config_classes)


# ---- resolution ------------------------------------------------------------

def test_resolution_finds_the_offsets_the_hops_and_the_strides():
    cp = build().cp
    assert cp.state_offsets == {
        "CurrentCommander": CS_CURRENT,
        "CommanderVoteTimer": CS_VOTE_TIMER,
        "CommanderVoteTimestamp": CS_VOTE_STAMP,
        "VoteCooldownTimer": CS_CD_TIMER,
        "VoteCooldownTimestamp": CS_CD_STAMP,
        "CommanderCategories": CS_CATEGORIES,
        "LastCategoryGameTime": CS_LAST_USE,
        "CommandIntervals": CS_INTERVALS,
        "NomineeStatus": CS_NOMINEES,
    }
    assert cp.state_bool_masks == {
        "bCommanderIsActive": (CS_FLAGS, 0x01),
        "bActionsEnabled": (CS_FLAGS, 0x02),
        "bVoteInProgress": (CS_FLAGS, 0x04),
        "bVoteCooldownActive": (CS_FLAGS, 0x08),
    }
    # The FastArrays: Items INSIDE the wrapper struct, and the stride is the
    # inner struct's reflected size — never a constant.
    assert cp.intervals_items_off == FAS_ITEMS_OFF
    assert cp.interval_stride == INTERVAL_STRIDE
    assert cp.interval_content_off == FAS_CONTENT_OFF
    assert cp.nominees_items_off == FAS_ITEMS_OFF
    assert cp.nominee_stride == NOMINEE_STRIDE
    assert cp.category_stride == CATEGORY_STRIDE
    assert cp.action_data_offsets == {
        "CommandActionData": 0, "GameTimeAtCreation": 8,
        "CooldownTimeRemaining": 12, "IsDestroyedDuringActive": 16}
    assert cp.action_data_bool_masks == {"IsDestroyedDuringActive": (16, 0x01)}
    assert cp.nominee_offsets == {"NomineeState": 0, "VoteCount": 8}
    assert cp.category_offsets == {"Name": 0, "CooldownDuration": 16}
    assert cp.manager_offsets == {
        "VotingTimeSeconds": MGR_VOTING,
        "VoteCooldownTimeSeconds": MGR_COOLDOWN,
        "ActionCooldownExtensionOnNewCommander": MGR_EXTENSION,
        "MinimumSquadSizeForVoting": MGR_MIN_SIZE,
        "MinimumSquadsRequiredForVoting": MGR_MIN_SQUADS,
    }
    assert cp.manager_bool_masks == {"bCommanderActive": (MGR_FLAGS, 0x01)}


def test_the_float_arrays_stride_comes_from_its_element_type():
    """The array property header reads ElementSize 0 for LastCategoryGameTime
    on this build (2026-09-07), so the stride is the float's own size. Reading
    it from the header would walk the array at stride 0."""
    assert build().cp.last_use_stride == 4


def test_no_commander_classes_resolves_to_nothing():
    img = _Image()
    assert sn.resolve_commander_paths(img.pm, img.names, 0, 0) is None


# ---- the block -------------------------------------------------------------

def _block(fx) -> dict:
    return sn.read_commander_block(fx.pm, fx.alloc, fx.arr, fx.paths, fx.state)


def test_the_whole_block_is_exactly_what_the_spec_lists():
    fx = build()
    assert _block(fx) == {
        "enabled": True,
        "actionsEnabled": True,
        "vote": {
            "inProgress": True,
            "timer": 47,
            "endsGameTime": 1234,
            "nominees": [
                {"eosId": "eos-ruby", "name": "Ruby", "votes": 3},
                {"eosId": "eos-doc", "name": "Doc", "votes": 1},
            ],
            "cooldownActive": False,
            "cooldownTimer": 0,
            "cooldownEndsGameTime": 900,
        },
        "cooldowns": {
            "categories": [
                {"id": 0, "name": "Air Support", "intervalSec": 300.0,
                 "lastUseGameTime": 120.5},
                # No element at index 1 yet: read, and none — not unknown.
                {"id": 1, "name": "Artillery", "intervalSec": 600.0,
                 "lastUseGameTime": None},
            ],
            "actions": [
                {"action": UAV, "displayName": "MQ-9 UAV Recon",
                 "createdGameTime": 100.5, "remainingAtChange": 0.0,
                 "destroyedDuringActive": False, "categoryId": 1,
                 "enrouteSec": 60.0, "activeSec": 330.0, "cooldownSec": 600.0},
                {"action": CREEP, "displayName": "Creeping Barrage",
                 "createdGameTime": 250.0, "remainingAtChange": 42.5,
                 "destroyedDuringActive": True, "categoryId": 1,
                 "enrouteSec": 60.0, "activeSec": 120.0, "cooldownSec": 900.0},
            ],
        },
    }


def test_empty_arrays_are_empty_lists_not_missing_keys():
    """`[]` says "read, and none" — before a vote, before the first claim."""
    fx = build(nominees=(), actions=[], categories=[], last_use=[])
    block = _block(fx)
    assert block["vote"]["nominees"] == []
    assert block["cooldowns"] == {"categories": [], "actions": []}


def test_a_category_with_no_stamp_at_all_is_null_for_every_index():
    block = _block(build(last_use=[]))
    assert [c["lastUseGameTime"] for c in block["cooldowns"]["categories"]] == [
        None, None]


def test_an_entry_whose_action_class_is_null_carries_null_and_no_config():
    fx = build(actions=[(None, 12.0, 0.0, False)])
    assert _block(fx)["cooldowns"]["actions"] == [
        {"action": None, "createdGameTime": 12.0, "remainingAtChange": 0.0,
         "destroyedDuringActive": False}]


def test_a_nominee_whose_state_is_null_is_null_for_both_identity_fields():
    fx = build(nominees=((None, 0), ("Ruby", 2)))
    assert _block(fx)["vote"]["nominees"] == [
        {"eosId": None, "name": None, "votes": 0},
        {"eosId": "eos-ruby", "name": "Ruby", "votes": 2},
    ]


def test_a_config_whose_default_object_is_absent_omits_its_five_values():
    """The live values still ride; nothing is defaulted in their place."""
    fx = build(cdos=frozenset({CREEP}))
    first = _block(fx)["cooldowns"]["actions"][0]
    assert first == {"action": UAV, "createdGameTime": 100.5,
                     "remainingAtChange": 0.0, "destroyedDuringActive": False}


def test_a_renamed_property_omits_its_key_and_leaves_the_rest():
    """What a Squad rename looks like: the name is not in the layout, so the
    key is absent — never a read at a remembered offset."""
    block = _block(build(omit=frozenset({
        "bActionsEnabled", "CommanderVoteTimestamp", "CooldownDuration",
        "DisplayName", "VoteCount"})))
    assert "actionsEnabled" not in block
    assert "enabled" in block
    assert "endsGameTime" not in block["vote"] and block["vote"]["timer"] == 47
    assert all("intervalSec" not in c for c in block["cooldowns"]["categories"])
    assert all("votes" not in n for n in block["vote"]["nominees"])
    first = block["cooldowns"]["actions"][0]
    assert "displayName" not in first and "cooldownSec" not in first
    assert first["action"] == UAV and first["enrouteSec"] == 60.0


def test_a_renamed_array_omits_the_whole_list():
    block = _block(build(omit=frozenset({"NomineeStatus", "CommanderCategories"})))
    assert "nominees" not in block["vote"]
    assert "categories" not in block["cooldowns"]
    assert len(block["cooldowns"]["actions"]) == 2


def test_a_renamed_items_field_omits_the_entries_it_indexes():
    """The FastArray hop is the whole read: without `Items` there is no
    array address, and a guess at one would walk arbitrary memory. The
    categories are a plain array on the state and are unaffected."""
    block = _block(build(omit=frozenset({"Items"})))
    assert "nominees" not in block["vote"]
    assert "actions" not in block["cooldowns"]
    assert len(block["cooldowns"]["categories"]) == 2


def test_a_torn_array_header_omits_the_list_rather_than_truncating_it():
    """A count no commander array could hold is a torn read. A truncated list
    would look complete to a consumer, so the key goes instead."""
    fx = build()
    torn = bytearray(fx.pm.read(fx.state, 0x400))
    torn[CS_CATEGORIES:CS_CATEGORIES + 16] = struct.pack(
        "<QII", 0x99_0000, sn._COMMANDER_ARRAY_MAX + 1, sn._COMMANDER_ARRAY_MAX + 1)
    fx.pm.add(fx.state, bytes(torn))
    fx.pm._segments.reverse()               # the torn copy wins the lookup
    assert "categories" not in _block(fx)["cooldowns"]


def test_an_unreadable_state_gives_no_block():
    fx = build()
    assert sn.read_commander_block(fx.pm, fx.alloc, fx.arr, fx.paths, 0) is None
    bare = SimpleNamespace(commander=None, ts_offsets={}, ps_offsets={})
    assert sn.read_commander_block(fx.pm, fx.alloc, fx.arr, bare, fx.state) is None


# ---- the config default objects and their cache ----------------------------

def test_every_configs_default_object_is_found_in_one_walk():
    fx = build()
    caches = sn.SnapshotCaches()
    _ = sn.read_commander_block(fx.pm, fx.alloc, fx.arr, fx.paths, fx.state,
                                caches)
    assert fx.arr.walks == 1, "two action classes must not cost two walks"
    assert set(caches.command_action_configs) == set(fx.config_classes.values())


def test_the_cached_config_survives_until_the_generation_moves():
    """Cached per class address: the walk is paid once, and only a generation
    bump — the rolling cache reset, or a layer roll — re-resolves it."""
    fx = build()
    caches = sn.SnapshotCaches()
    first = _block(fx)              # no caches: one walk for both classes
    for _ in range(3):
        sn.read_commander_block(fx.pm, fx.alloc, fx.arr, fx.paths, fx.state,
                                caches)
    # The first cached call walks, the two after it do not.
    assert fx.arr.walks == 2

    # The default object now reports a different display name. The cache keeps
    # answering with the old one until the generation moves.
    cdo = fx.arr.objects[f"Default__{UAV}"]
    changed = bytearray(fx.pm.read(cdo, 0x100))
    changed[CFG_DISPLAY:CFG_DISPLAY + 16] = fx.img.fstring("Renamed Asset")
    fx.pm.add(cdo, bytes(changed))
    fx.pm._segments.reverse()
    cached = sn.read_commander_block(fx.pm, fx.alloc, fx.arr, fx.paths,
                                     fx.state, caches)
    assert cached == first
    caches.reset()
    fresh = sn.read_commander_block(fx.pm, fx.alloc, fx.arr, fx.paths,
                                    fx.state, caches)
    assert fresh["cooldowns"]["actions"][0]["displayName"] == "Renamed Asset"


def test_without_an_object_array_the_config_values_are_simply_absent():
    fx = build()
    block = sn.read_commander_block(fx.pm, fx.alloc, None, fx.paths, fx.state)
    assert block["cooldowns"]["actions"][0] == {
        "action": UAV, "createdGameTime": 100.5, "remainingAtChange": 0.0,
        "destroyedDuringActive": False}


# ---- the rules -------------------------------------------------------------

def test_the_rules_are_the_managers_six_values():
    fx = build()
    assert sn.read_commander_rules(fx.pm, fx.paths, fx.manager) == {
        "enabled": True, "votingTimeSec": 60, "voteCooldownSec": 300,
        "newCommanderExtensionSec": 300.0, "minSquadSize": 2, "minSquads": 3}


def test_the_rules_omit_a_renamed_value_and_vanish_with_the_class():
    fx = build(omit=frozenset({"MinimumSquadsRequiredForVoting"}))
    rules = sn.read_commander_rules(fx.pm, fx.paths, fx.manager)
    assert "minSquads" not in rules and rules["minSquadSize"] == 2
    assert sn.read_commander_rules(fx.pm, fx.paths, 0) is None


# ---- the seat, and the identity fix (tracker W10) --------------------------

def _team(fx) -> dict:
    return sn.read_team_state(fx.pm, fx.alloc, fx.paths, fx.team, fx.arr)


def test_the_seat_is_read_through_the_hop_the_actor_always_had():
    """The defect W10 records: CommanderState points at the commander-state
    ACTOR, so reading a name straight off it emitted nothing. The seat is
    CurrentCommander on that actor."""
    fx = build()
    team = _team(fx)
    assert team["commanderName"] == "Six"
    assert team["commanderEosId"] == "eos-" + "6" * 28
    assert team["commanderStateAddr"] == f"{fx.state:#x}"
    assert team["id"] == 1
    assert set(team) == {"_addr", "id", "commanderStateAddr", "commanderName",
                         "commanderEosId", "commander"}
    # Reading the actor AS a player state — the shipped defect — finds nothing
    # at those offsets, which is why the fields never appeared.
    assert sn.read_fstring(fx.pm, fx.state + PS_NAME) != "Six"


def test_an_empty_seat_is_explicitly_null_on_both_fields():
    team = _team(build(seated=False))
    assert team["commanderName"] is None and team["commanderEosId"] is None
    assert "commander" in team, "the block is read whether or not it is claimed"


def test_a_team_with_no_commander_state_carries_nothing_about_one():
    fx = build()
    empty = bytearray(fx.pm.read(fx.team, 0x40))
    struct.pack_into("<Q", empty, TS_COMMANDER_STATE, 0)
    fx.pm.add(fx.team, bytes(empty))
    fx.pm._segments.reverse()
    team = _team(fx)
    assert set(team) == {"_addr", "id"}


def test_the_seat_identity_is_cached_by_player_state_like_every_other():
    fx = build()
    caches = sn.SnapshotCaches()
    sn.read_team_state(fx.pm, fx.alloc, fx.paths, fx.team, fx.arr, caches)
    assert caches.player_names[fx.commander_ps] == "Six"
    assert caches.player_eos_ids[fx.commander_ps] == "eos-" + "6" * 28
