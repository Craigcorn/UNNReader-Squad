"""Structured reader-health check — the machine-readable "did a Squad update
break us?" signal.

`cmd_doctor` (cli.py) is the rich, human-facing command; this module is its
programmatic sibling. `run_doctor()` returns a plain dict that the fleet/update
system reports to central (Phase 0 telemetry) and, later, uses as the
post-offset-apply verification gate (Phase 1).

Both derive their expectations from the SAME module-level offset constants in
`squad.snapshot` via `hardcoded_offset_tables()`, and — since the checks below
moved here out of `cmd_doctor` — they now run THE SAME CHECK FUNCTIONS. That
matters because production is a managed instance where nobody can type
`sqreader doctor`: the machine signal is the only alarm there, so every check
a human could run has to be one the machine runs too. Anything that stays
human-only is a class of breakage that goes unnoticed on exactly the
deployment that cannot look for itself.

The core signal is cheap: for each Squad class we hardcode offsets for, walk its
live UClass layout by reflection and compare. A drifted or vanished field is the
fingerprint of a Squad patch that moved a struct. Reads are never fabricated
(the reader nulls bad reads), so drift shows up here as a mismatch, not a crash.

The failure mode this module fears is FALSE drift, not missed drift: a check-in
that cries wolf every 300 s trains an operator to ignore the one report that
mattered, and gates a pointless self-heal on top. So every check here has
explicit skip semantics — "nothing to measure" is never drift — and the
value-based check tolerates a straggler rather than alarming on it.
"""
from __future__ import annotations

from typing import Any, NamedTuple

# USceneComponent.ComponentToWorld.Translation. ComponentToWorld is a private
# C++ member — not a UPROPERTY — so reflection cannot resolve it by name and
# `resolve_paths` hardcodes this same +0x210. That is precisely why it needs a
# VALUE check instead of a table row: this one constant sits under every
# position the reader reports, and nothing else in the doctor would notice it
# moving. It lives here rather than in snapshot.py because a class table cannot
# express a private member, and the offset-pack mechanism (module-level globals
# in snapshot.py) never reaches the literal inside `resolve_paths` either.
SCENE_COMPONENT_TO_WORLD_TRANSLATION_OFF = 0x210

# The ComponentToWorld check judges live values, so it needs a quorum and a
# tolerance. Fewer than this many readable unattached vehicles means "nothing
# to measure" — skip, never drift. And a single straggler caught mid-teleport
# (the transform is written a frame after RelativeLocation) must not alarm, so
# only a MAJORITY disagreeing counts as the constant having moved.
C2W_MIN_SAMPLES = 3
# How many actors a caller may hand us. The sample exists to be cheap: eight
# vehicles is ~30 small reads, which is what keeps this check affordable on the
# 300 s check-in cadence.
C2W_MAX_SAMPLES = 8


class CheckOutcome(NamedTuple):
    """What one shared check reports, in the shapes both faces of the doctor
    already speak:

      * `drift`   — machine drift records {class, field, expected, live, problem}
      * `skipped` — {"check": label, "reason": ...}: nothing to measure, which
                    is a different thing from silence and from a failure
      * `notes`   — {"label", "ok", "detail"}: the human command's printed line

    Every failed assertion appends BOTH a drift record and a failed note, in
    the same breath, so the human command and the machine signal cannot come to
    different conclusions about the same memory.
    """
    drift: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    notes: list[dict[str, Any]]


def _empty() -> CheckOutcome:
    return CheckOutcome([], [], [])


def hardcoded_offset_tables() -> list[tuple[str, str, bool, dict[str, int]]]:
    """The offset tables both `cmd_doctor` and `run_doctor` verify against live
    reflection, as (type name, meta-class, optional?, {field: offset}).

    `meta-class` is what `find_by_name` filters on — "Class" for a UClass,
    "ScriptStruct" for a struct like SQVehicleSeatConfig. `optional` marks a
    type that legitimately may not be loaded in a given level: absent means
    SKIPPED, never drift, so an emplacement-free layer cannot raise a false
    alarm. A REQUIRED type that is missing IS drift — it was renamed away.

    Every value is imported from the single-source constants in `squad.snapshot`,
    so this function only names WHICH fields to check — never their values.

    THE RULE (see CONTRIBUTING.md "Reverse-engineered offsets"): every
    hardcoded offset constant the reader can read THROUGH — used directly or
    as a reflection fallback — belongs in a table here, in the same change
    that introduces it. A constant stays out only for a stated reason, and
    then it goes in this register:

      * AMMO_WEP_OFFSETS — read off `AmmoWep_*_C` blueprint actors; no single
        UClass to check against and the declaring base class is unverified.
        Verify the base class live, then promote here.
      * COLLECTOR_OFFSETS — private C++ members, not UPROPERTIES at all, so
        reflection cannot resolve them by name in any Squad build. Validated
        instead by the value-based collector verdicts
        (`collector_field_verdicts`, `tests/test_doctor_collectors.py`).
      * LANE_GRAPH_OFFSETS / LANE_LINK_* / LANE_VISUALIZER_ROUTE_INDEX_OFF —
        verified by the dedicated lane-graph check (`check_lane_graph`), which
        knows the AAS/RAAS mode differences a flat table cannot express (the
        RAAS visualizer is absent on AAS layers BY DESIGN — tabling it would
        report drift on half the map rotation).
      * MARKER_ITEM_OFFSETS, SQ_SEATCOMP_ANIM_STATE_OFFSET,
        SQ_SEATCOMP_FORCE_OCCUPIED_OFFSET, SQ_VEHCOMP_STATE_OFFSET — declared
        but never read, so nothing can drift through them. Watch them the day
        they are first read.
      * STRUCT-INTERNAL offsets with no property name — MARKER_ITEM_SIZE and
        the per-item MARKER_ITEM_OFFSETS are a brute-forced stride into an
        unnamed element struct, so there is nothing to resolve by name;
        `check_marker_stride` verifies the stride by value instead. The NAMED
        struct internals (THI_* / HR_* / PDE_HIT_INFO_OFFSET /
        MARKER_ARRAY_ITEMS_OFFSET) are no longer in this register: they are
        checked by `struct_field_tables` above. MARKER_ITEMS_ABS_OFFSET is
        the sum of two watched constants and needs no row of its own.
      * SCENE_COMPONENT_TO_WORLD_TRANSLATION_OFF (this module) — a private C++
        member with no property name to check; `check_component_to_world`
        verifies it by value against live vehicles instead."""
    from .squad.snapshot import (
        AACTOR_OWNER_OFFSET, DEPLOYABLE_OFFSETS, FOB_RESOURCE_OFFSETS,
        MARKER_MGR_MARKER_ARRAY_OFFSET, PC_PLAYER_STATE_OFFSET,
        PC_PLAYER_STATS_INDEX_OFFSET, PC_RECENT_VOICE_CHANNEL_OFFSET,
        VEHICLE_SPAWNER_OFFSETS,
        MARKER_OFFSETS, PROJECTILE_INSTIGATOR_CONTROLLER_OFFSET,
        PROJECTILE_OFFSETS, RALLY_OFFSETS,
        SQ_DEPLOYABLE_VEHICLE_GUN_MOUNT_OFF, SQ_DEPLOYABLE_VEHICLE_OWNING_OFF,
        SQ_DEPLOYABLE_VEHICLE_SWIVEL_OFF, SQ_SEATCFG_ATTACH_SOCKET_OFF,
        SQ_INV_CURRENT_WEAPON_OFFSET, SQ_SEATCOMP_SEAT_CONFIG_OFFSET,
        SQ_SEATCOMP_SEAT_PAWN_OFFSET, SQ_SEATCOMP_SEATED_PLAYER_OFFSET,
        SQ_SEATCOMP_SEATED_SOLDIER_OFFSET, SQ_SOLDIER_TAKE_HIT_INFO_OFFSET,
        SQ_TURRET_INVENTORY_OFFSET,
        SQ_VEHICLE_CACHED_ENGINE_OFFSET,
        SQ_VEHICLE_COMPONENTS_OFFSET, SQ_VEHICLE_SEATS_OFFSET,
        SQ_VEHICLE_TURRETS_OFFSET, SQ_VEHICLESEAT_SEAT_HEALTH_OFFSET,
        SQ_VEHCOMP_HEALTH_OFFSET, SQ_VEHCOMP_MAX_HEALTH_OFFSET,
        SQ_VEHCOMP_NORMALIZED_HEALTH_OFFSET,
        SQ_VWEAPON_MAGAZINES_OFFSET, SQ_VWEAPON_VEHICLE_TURRET_OFFSET,
    )
    return [
        ("SQDeployable",     "Class", False, dict(DEPLOYABLE_OFFSETS)),
        ("SQVehicleSpawner", "Class", False, dict(VEHICLE_SPAWNER_OFFSETS)),
        ("SQMapMarker",      "Class", False, dict(MARKER_OFFSETS)),
        # DamageInstigatorController is the projectile's firer — reflected at
        # resolve time with this constant as fallback, and it has ALREADY
        # drifted once, so leaving it unwatched was the sharpest edge here.
        ("SQProjectile",     "Class", False,
         dict(PROJECTILE_OFFSETS,
              DamageInstigatorController=PROJECTILE_INSTIGATOR_CONTROLLER_OFFSET)),
        ("SQSquadRallyPoint", "Class", False, dict(RALLY_OFFSETS)),
        # LastTakeHitInfo: read directly (no fallback path) for damage events.
        ("SQSoldier", "Class", False,
         {"LastTakeHitInfo": SQ_SOLDIER_TAKE_HIT_INFO_OFFSET}),
        # AActor.Owner — read directly to join ammo pools to their vehicle.
        ("Actor", "Class", False, {"Owner": AACTOR_OWNER_OFFSET}),
        ("SQVehicleSeatComponent", "Class", False, {
            "SeatPawn":      SQ_SEATCOMP_SEAT_PAWN_OFFSET,
            "SeatedPlayer":  SQ_SEATCOMP_SEATED_PLAYER_OFFSET,
            "SeatedSoldier": SQ_SEATCOMP_SEATED_SOLDIER_OFFSET,
            # Name verified live 2026-08-30 (SeatConfig @ +0x250) — promoted
            # out of the unverified register.
            "SeatConfig":    SQ_SEATCOMP_SEAT_CONFIG_OFFSET,
        }),
        ("SQVehicleSeat", "Class", False, {
            "SeatHealth": SQ_VEHICLESEAT_SEAT_HEALTH_OFFSET,
            # Name verified live 2026-08-30 — promoted out of the register.
            "CachedVehicleInventory": SQ_TURRET_INVENTORY_OFFSET,
        }),
        # The owning class was identified by following a live seat pawn's
        # inventory pointer (2026-08-30): SQVehicleInventoryComponent, with
        # CurrentWeapon reflected at +0x1b8.
        ("SQVehicleInventoryComponent", "Class", False,
         {"CurrentWeapon": SQ_INV_CURRENT_WEAPON_OFFSET}),
        ("SQVehicleComponent", "Class", False, {
            "Health":           SQ_VEHCOMP_HEALTH_OFFSET,
            "MaxHealth":        SQ_VEHCOMP_MAX_HEALTH_OFFSET,
            "NormalizedHealth": SQ_VEHCOMP_NORMALIZED_HEALTH_OFFSET,
        }),
        ("SQVehicleWeapon", "Class", False, {
            "Magazines":     SQ_VWEAPON_MAGAZINES_OFFSET,
            "VehicleTurret": SQ_VWEAPON_VEHICLE_TURRET_OFFSET,
        }),
        # The SQVehicle entity arrays — reflection-first at resolve time with
        # these constants as fallback. VehicleTurrets has already drifted once
        # (+0x8 at v10.4.1), which is exactly why the fallbacks are watched:
        # a drift report keeps the constants honest and lets a signed offset
        # pack refresh them.
        ("SQVehicle", "Class", False, {
            "VehicleSeats":        SQ_VEHICLE_SEATS_OFFSET,
            "VehicleTurrets":      SQ_VEHICLE_TURRETS_OFFSET,
            "VehicleComponents":   SQ_VEHICLE_COMPONENTS_OFFSET,
            "CachedVehicleEngine": SQ_VEHICLE_CACHED_ENGINE_OFFSET,
        }),
        # Emplacement gun joins/aim — same reflection-first-with-fallback
        # pattern. REQUIRED: `resolve_paths` tolerates the class being absent
        # because a level may have no emplacement BUILT, but the UCLASS is
        # registered by C++ module load, not by content — proved present on a
        # 0-player server with nothing built, 2026-08-30. Absence is therefore
        # a rename, which is exactly what this tier exists to catch.
        ("SQDeployableVehicle", "Class", False, {
            "OwningDeployable":    SQ_DEPLOYABLE_VEHICLE_OWNING_OFF,
            "SwivelMeshComponent": SQ_DEPLOYABLE_VEHICLE_SWIVEL_OFF,
            "GunMountComponent":   SQ_DEPLOYABLE_VEHICLE_GUN_MOUNT_OFF,
        }),
        # The seat's role-socket offset lives inside a ScriptStruct, not a
        # UClass — which is what the meta-class column is for. Native, so
        # required for the same reason as the row above.
        ("SQVehicleSeatConfig", "ScriptStruct", False,
         {"SeatAttachSocket": SQ_SEATCFG_ATTACH_SOCKET_OFF}),
        # FOB radio resource pool — reflection-first with these as fallback
        # (see `f_off` in read_deployable). The BP that declares them is a
        # BlueprintGeneratedClass, and optional: a level with no FOB built
        # has not loaded it.
        ("BP_BaseFobCreator_C", "BlueprintGeneratedClass", True,
         dict(FOB_RESOURCE_OFFSETS)),
        # Player-controller fields, reflection-first with these as fallback.
        # A dedicated server's controller INSTANCE is the blueprint variant
        # and `resolve_paths` accepts either — but the native SQPlayerController
        # UClass is registered regardless, so its absence here is a rename.
        ("SQPlayerController", "Class", False, {
            "PlayerStatsIndex":   PC_PLAYER_STATS_INDEX_OFFSET,
            "PlayerState":        PC_PLAYER_STATE_OFFSET,
            "RecentVoiceChannel": PC_RECENT_VOICE_CHANNEL_OFFSET,
        }),
        # The map-marker manager's MarkerArray struct; native, so required.
        # The FastArray `Items` offset INSIDE that struct is checked by the
        # struct tier (`struct_field_tables`).
        ("SQMapMarkerManagerComponent", "Class", False,
         {"MarkerArray": MARKER_MGR_MARKER_ARRAY_OFFSET}),
    ]


def struct_field_tables() -> list[tuple[str, str, tuple[str, ...], bool,
                                        dict[str, int]]]:
    """The STRUCT-INTERNAL tier: offsets addressed relative to a struct rather
    than a class, as (owner type, meta-class, hop path, optional?, {field:
    offset}).

    A class table cannot express these — `LastTakeHitInfo.ActualDamage` is not
    a field of SQSoldier, it is a field of the FSQTakeHitInfo it holds — which
    is why the damage-event internals sat in the register with "no way to check
    this" written next to them. `struct_layout_for_field`'s two moves (find the
    StructProperty, read the UScriptStruct it points at) compose down a path,
    so the FHitResult two hops in is reachable with the helpers that already
    exist.

    Same values, same single source: every offset is imported from
    `squad.snapshot`. Same optional policy as the class tables: an absent
    OWNER is drift unless the row says the type may legitimately not be
    loaded; a hop that resolves but lacks the field is always drift."""
    from .squad.snapshot import (
        HR_BONE_NAME_OFFSET, HR_DISTANCE_OFFSET, MARKER_ARRAY_ITEMS_OFFSET,
        PDE_HIT_INFO_OFFSET, THI_ACTUAL_DAMAGE_OFFSET, THI_DAMAGE_CAUSER_OFFSET,
        THI_DAMAGE_TYPE_CLASS_OFFSET, THI_FLAGS_OFFSET,
        THI_PAWN_INSTIGATOR_OFFSET, THI_POINT_DAMAGE_EVENT_OFFSET,
        THI_SERVER_TIMESTAMP_OFFSET,
    )
    return [
        # FSQTakeHitInfo — every damage event the reader enriches. This struct
        # has ALREADY moved once (its base shifted 0x20 and the reader spent
        # months reading past it), so its internals are the last place that
        # should be taken on trust. `bKilled` is the flags byte: the reader
        # masks bit 0 out of THI_FLAGS_OFFSET, and reflection puts bKilled,
        # bWounded and bEjectedFromVehicle at that same offset.
        ("SQSoldier", "Class", ("LastTakeHitInfo",), False, {
            "ActualDamage":     THI_ACTUAL_DAMAGE_OFFSET,
            "ServerTimestamp":  THI_SERVER_TIMESTAMP_OFFSET,
            "DamageTypeClass":  THI_DAMAGE_TYPE_CLASS_OFFSET,
            "PawnInstigator":   THI_PAWN_INSTIGATOR_OFFSET,
            "DamageCauser":     THI_DAMAGE_CAUSER_OFFSET,
            "bKilled":          THI_FLAGS_OFFSET,
            "PointDamageEvent": THI_POINT_DAMAGE_EVENT_OFFSET,
        }),
        # Two hops deeper: the FHitResult the bone name and hit distance come
        # out of. The reader reaches it as base + PointDamageEvent + HitInfo,
        # so both steps are watched.
        ("SQSoldier", "Class", ("LastTakeHitInfo", "PointDamageEvent"), False,
         {"HitInfo": PDE_HIT_INFO_OFFSET}),
        ("SQSoldier", "Class",
         ("LastTakeHitInfo", "PointDamageEvent", "HitInfo"), False,
         {"Distance": HR_DISTANCE_OFFSET, "BoneName": HR_BONE_NAME_OFFSET}),
        # The FastArraySerializer `Items` the marker walk indexes into.
        # Belt-and-braces with `check_marker_stride`, which reaches the same
        # field through the property chain instead.
        ("SQMapMarkerManagerComponent", "Class", ("MarkerArray",), False,
         {"Items": MARKER_ARRAY_ITEMS_OFFSET}),
    ]


def required_reflection_names() -> list[tuple[str, str, bool, list[str]]]:
    """Reflection-only reads with no fallback constant: a Squad rename makes
    them silently vanish from recordings (fail-safe, but dark). Listing a
    (type, meta-class, optional?, [property names]) row here turns that rename
    into a drift report instead.

    The `optional` column carries the same meaning it does in the class
    tables, and every row is currently REQUIRED. That is evidence, not
    optimism: these are all native SQ* classes, registered when the C++ module
    loads rather than when content spawns, and all four were present on a
    0-player server with nothing built (2026-08-30). Treating them as optional
    made a class-level rename of SQHealingEquipableItem or SQCommanderState an
    eternal silent skip — the exact blind spot this tier exists to close. Mark
    a row optional only with an observed reason for it."""
    return [
        # Medical capture (per-player `medical` dict).
        ("SQSoldier", "Class", False, ["CurrentHeldItem"]),
        ("SQHealingEquipableItem", "Class", False,
         ["HealedTarget", "ItemCount"]),
        # Commander identity: the team-state pointer and the hop the
        # identity read takes through the commander-state actor.
        ("SQTeamState", "Class", False, ["CommanderState"]),
        ("SQCommanderState", "Class", False, ["CurrentCommander"]),
    ]


# Which collector each spliced player-stat field is read from. The grouping is
# the load-bearing part: two fields from the same collector share ONE array
# entry, which is what makes a disagreement between them mean something.
COLLECTOR_FIELD_SOURCE: dict[str, str] = {
    "fobsBuilt":         "logistics",
    "suppliesDelivered": "logistics",
    "vehicleDamage":     "combat",
    "fobsDestroyed":     "combat",
    "captures":          "objective",
    "defenses":          "objective",
}


# ----- shared resolution (one walk for every check) -------------------------

def check_target_names() -> dict[str, str]:
    """Every class/struct name the checks below need, as {name: meta-class}.

    One dict so the whole doctor can resolve in ONE GUObjectArray walk.
    `resolve_paths` learned this trick first (snapshot.py): a `find_by_name`
    walks all ~223k objects resolving each FName, so twenty-odd sequential
    lookups pay for twenty-odd walks. Measured live 2026-08-31 on the test
    box: 148 ms sequential vs 71 ms batched for this exact name set."""
    names: dict[str, str] = {}
    for cls, kind, _optional, _table in hardcoded_offset_tables():
        names[cls] = kind
    for cls, kind, _optional, _fields in required_reflection_names():
        names[cls] = kind
    for cls, kind, _path, _optional, _table in struct_field_tables():
        names[cls] = kind
    # The core gate + the classes the moved checks need beyond the tables.
    names["SQPlayerState"] = "Class"
    names["SQGraphInitializerComponent"] = "Class"
    names["SQGraphRAASVisualizerComponent"] = "Class"
    names["SceneComponent"] = "Class"
    return names


class DoctorTargets:
    """Resolved class addresses plus a per-run layout cache.

    Layouts are cached only WITHIN one doctor run: they are the thing being
    checked, so re-reading them every run is the whole point. Addresses are a
    different matter — see `resolve_targets`."""

    def __init__(self, pm: Any, alloc: Any, addrs: dict[str, int], *,
                 complete: bool, from_cache: bool = False) -> None:
        self._pm = pm
        self._alloc = alloc
        self.addrs = addrs
        self.complete = complete
        self.from_cache = from_cache
        self._layouts: dict[int, dict[str, Any]] = {}

    def addr(self, name: str) -> int:
        return int(self.addrs.get(name) or 0)

    def layout(self, name: str) -> dict[str, Any]:
        """Live {field: FPropertyInfo} for a resolved class ({} if absent)."""
        a = self.addr(name)
        if not a:
            return {}
        cached = self._layouts.get(a)
        if cached is None:
            from .ue.reflection import get_class_layout
            cached = get_class_layout(self._pm, a, self._alloc)
            self._layouts[a] = cached
        return cached


# Resolved class addresses, keyed by the anchors they were resolved against.
# UClass objects live for the life of the process, so re-walking 223k objects
# every 300 s to re-find the same addresses is pure waste — but a cached
# address that has gone stale would be a WRONG read, so every entry is
# re-validated (name + meta-class read back) before use, at ~0.05 ms for the
# whole set, and any surprise falls back to a fresh walk.
_TARGET_ADDR_CACHE: dict[tuple[int, int], dict[str, int]] = {}


def _object_name(pm: Any, alloc: Any, addr: int) -> str | None:
    """The FName text of a UObject at `addr` (None if unreadable)."""
    import struct as _s

    from .ue.uobject import UOBJ_NAME_PRIVATE
    if not addr:
        return None
    nm = pm.try_read(addr + UOBJ_NAME_PRIVATE, 8)
    if nm is None or len(nm) < 8:
        return None
    ci, num = _s.unpack("<II", nm)
    return alloc.fname_to_str(ci, num)


def _meta_class_name(pm: Any, alloc: Any, addr: int) -> str | None:
    """The name of the class OF the object at `addr` — "Class",
    "ScriptStruct", "BlueprintGeneratedClass"."""
    import struct as _s

    from .ue.uobject import UOBJ_CLASS_PRIVATE
    cp = pm.try_read(addr + UOBJ_CLASS_PRIVATE, 8)
    if cp is None or len(cp) < 8:
        return None
    return _object_name(pm, alloc, _s.unpack("<Q", cp)[0])


def _cache_is_live(pm: Any, alloc: Any, addrs: dict[str, int],
                   names: dict[str, str]) -> bool:
    """True while every cached address still IS the object it was cached as.

    Deliberately strict: a cache that is missing a name (the type was not
    loaded when it was built) is rejected outright, so a class that loads
    later — the first FOB of the round — starts being checked on the next
    run instead of staying invisible until the map changes."""
    if set(addrs) != set(names):
        return False
    for name, addr in addrs.items():
        if _object_name(pm, alloc, addr) != name:
            return False
        if _meta_class_name(pm, alloc, addr) != names[name]:
            return False
    return True


def resolve_targets(pm: Any, arr: Any, alloc: Any, *,
                    use_cache: bool = True) -> DoctorTargets:
    """Resolve every name `check_target_names` asks for in ONE walk.

    NEVER call this from anywhere that must not walk the object array — it is
    the one walk the doctor is allowed, and the cache means the check-in
    usually skips even that."""
    names = check_target_names()
    key = (int(getattr(arr, "base", 0) or 0), int(getattr(alloc, "base", 0) or 0))
    cacheable = bool(key[0] and key[1])
    if use_cache and cacheable:
        cached = _TARGET_ADDR_CACHE.get(key)
        if cached is not None and _cache_is_live(pm, alloc, cached, names):
            return DoctorTargets(pm, alloc, dict(cached), complete=True,
                                 from_cache=True)
    # find_all_by_names returns {name: (internal_index, addr)} — take [1].
    found = arr.find_all_by_names(dict(names), alloc=alloc)
    addrs = {n: hit[1] for n, hit in found.items() if hit}
    complete = set(addrs) == set(names)
    if cacheable:
        if complete:
            _TARGET_ADDR_CACHE[key] = dict(addrs)
        else:
            # An incomplete resolution is not worth caching: it would freeze
            # "not loaded" in for the life of the process.
            _TARGET_ADDR_CACHE.pop(key, None)
    return DoctorTargets(pm, alloc, addrs, complete=complete, from_cache=False)


def _targets_for(pm: Any, arr: Any, alloc: Any,
                 targets: DoctorTargets | None) -> DoctorTargets:
    return targets if targets is not None else resolve_targets(pm, arr, alloc)


# ----- the checks (shared by `cmd_doctor` and `run_doctor`) -----------------

def check_required_names(pm: Any, arr: Any, alloc: Any,
                         targets: DoctorTargets | None = None
                         ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Verify every `required_reflection_names` row against live reflection.

    Returns (drift, skipped): drift records for present-but-missing names,
    and skip notes for types not currently loaded (never drift)."""
    tg = _targets_for(pm, arr, alloc, targets)
    drift: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for cls, _kind, optional, names in required_reflection_names():
        if not tg.addr(cls):
            if optional:
                skipped.append({"class": cls, "reason": "type not loaded"})
            else:
                drift.append({"class": cls, "field": "*", "expected": None,
                              "live": None, "problem": "class not found"})
            continue
        live = tg.layout(cls)
        for fname in names:
            if fname not in live:
                drift.append({"class": cls, "field": fname, "expected": None,
                              "live": None,
                              "problem": "required name not reflected"})
    return drift, skipped


def check_offset_drift(pm: Any, arr: Any, alloc: Any,
                       targets: DoctorTargets | None = None
                       ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compare every hardcoded offset table against live reflection.

    Returns (drift, skipped). Each drift record is {class, field, expected,
    live, problem}. A REQUIRED class that is missing while the core classes ARE
    present is itself a drift signal (renamed/removed); an OPTIONAL one that is
    missing is simply not in this level and is skipped.
    """
    tg = _targets_for(pm, arr, alloc, targets)
    drift: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for cls, _kind, optional, table in hardcoded_offset_tables():
        if not tg.addr(cls):
            if optional:
                skipped.append({"class": cls, "reason": "type not loaded"})
            else:
                drift.append({"class": cls, "field": "*", "expected": None,
                              "live": None, "problem": "class not found"})
            continue
        live = tg.layout(cls)
        for fname, off in table.items():
            p = live.get(fname)
            if p is None:
                drift.append({"class": cls, "field": fname, "expected": off,
                              "live": None, "problem": "not reflected"})
            elif p.offset != off:
                drift.append({"class": cls, "field": fname, "expected": off,
                              "live": p.offset, "problem": "offset drift"})
    return drift, skipped


def check_struct_fields(pm: Any, arr: Any, alloc: Any,
                        targets: DoctorTargets | None = None
                        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Verify every `struct_field_tables` row against live reflection.

    Walks the hop path with the same two moves `struct_layout_for_field`
    makes — find the StructProperty, read the UScriptStruct it points at —
    then compares the inner offsets. Returns (drift, skipped).

    A hop whose NAME is gone from the layout is drift (Squad renamed or moved
    it). A hop whose name is there but whose struct pointer would not read is
    a transient /proc failure, and that is a skip: the doctor is what you run
    BECAUSE reads are failing, so it must not turn a failed read into a
    fabricated verdict."""
    from .ue.reflection import (
        find_field_by_name_with_super, get_class_layout,
        read_fstructproperty_struct,
    )
    tg = _targets_for(pm, arr, alloc, targets)
    drift: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for owner, _kind, path, optional, table in struct_field_tables():
        where = f"{owner}.{'.'.join(path)}"
        addr = tg.addr(owner)
        if not addr:
            if optional:
                skipped.append({"class": where, "reason": "type not loaded"})
            else:
                drift.append({"class": where, "field": "*", "expected": None,
                              "live": None, "problem": "class not found"})
            continue
        layout = tg.layout(owner)
        struct_addr = 0
        for i, hop in enumerate(path):
            if hop not in layout:
                drift.append({"class": where, "field": hop, "expected": None,
                              "live": None,
                              "problem": "struct field not reflected"})
                struct_addr = 0
                break
            ff = find_field_by_name_with_super(
                pm, addr if i == 0 else struct_addr, hop, alloc)
            struct_addr = (read_fstructproperty_struct(pm, ff)
                           if ff is not None else 0)
            if not struct_addr:
                skipped.append({"class": where,
                                "reason": f"{hop} did not re-resolve "
                                          f"(transient read)"})
                break
            layout = get_class_layout(pm, struct_addr, alloc)
        if not struct_addr:
            continue
        for fname, off in table.items():
            p = layout.get(fname)
            if p is None:
                drift.append({"class": where, "field": fname, "expected": off,
                              "live": None, "problem": "not reflected"})
            elif p.offset != off:
                drift.append({"class": where, "field": fname, "expected": off,
                              "live": p.offset, "problem": "offset drift"})
    return drift, skipped


def check_reflection_anchors(pm: Any, alloc: Any,
                             targets: DoctorTargets) -> CheckOutcome:
    """The reflection walker's own assumptions — the most fundamental thing
    the reader has.

    FStructProperty.Struct at FField+0x70 and the FBoolProperty byte-mask
    layout are how EVERY other reflection answer is derived; if these moved,
    nothing else the doctor says can be trusted. No skip rule is needed: they
    depend only on the core class the `unknown` gate already requires."""
    from .ue.reflection import (
        bool_property_mask, find_field_by_name_with_super,
        read_fstructproperty_struct, struct_layout_for_field,
    )
    out = _empty()
    ps = targets.addr("SQPlayerState")
    if not ps:
        out.skipped.append({"check": "reflection anchors",
                            "reason": "SQPlayerState not loaded"})
        return out

    # FStructProperty.Struct at +0x70 — SQPlayerState.PlayerStateData must
    # point at the PlayerStateDataObject UScriptStruct.
    sname: str | None = None
    ff = find_field_by_name_with_super(pm, ps, "PlayerStateData", alloc)
    if ff is not None:
        sname = _object_name(pm, alloc, read_fstructproperty_struct(pm, ff))
    _assert(out, "FStructProperty.Struct at +0x70",
            sname == "PlayerStateDataObject", f"got {sname!r}",
            cls="SQPlayerState", field="PlayerStateData",
            expected="PlayerStateDataObject", live=sname,
            problem="FStructProperty.Struct layout moved")

    # FBoolProperty.ByteMask layout — bIsABot is the fourth bool packed into
    # its byte, so its mask is 0x08. A wrong mask reads other players' flags.
    m = bool_property_mask(pm, ps, "bIsABot", alloc)
    _assert(out, "FBoolProperty.ByteMask layout (SQPlayerState.bIsABot)",
            m is not None and m[1] == 0x08, f"got {m!r}",
            cls="SQPlayerState", field="bIsABot", expected=0x08,
            live=(m[1] if m else None),
            problem="FBoolProperty byte-mask layout moved")

    # PlayerStateDataObject reflection — the struct every player stat is read
    # out of. Two names it must carry, and its size as the detail.
    psd = struct_layout_for_field(pm, ps, "PlayerStateData", alloc)
    _assert(out, "PlayerStateDataObject reflection (25 fields incl NumKills)",
            "NumKills" in psd and "RevivedPoints" in psd,
            f"{len(psd)} fields found",
            cls="PlayerStateDataObject", field="NumKills/RevivedPoints",
            expected=None, live=len(psd),
            problem="PlayerStateData struct did not reflect")
    return out


def check_lane_graph(pm: Any, alloc: Any,
                     targets: DoctorTargets) -> CheckOutcome:
    """AAS/RAAS lane topology: DesignOutgoingLinks and the FSQDesignLink shape.

    Two honest skips live here, which is why these constants were never flat
    table rows: a layer with no lane graph at all (seed / Skirmish) has no
    SQGraphInitializerComponent, and the RAAS visualizer is absent on AAS
    layers BY DESIGN. Absent means skipped; the alternative is crying drift
    across half the map rotation."""
    import struct as _s

    from .squad.snapshot import (
        LANE_GRAPH_OFFSETS, LANE_LINK_NODEA_OFF, LANE_LINK_NODEB_OFF,
        LANE_VISUALIZER_ROUTE_INDEX_OFF,
    )
    from .ue.reflection import read_fproperty, read_fstructproperty_struct
    out = _empty()
    gi = targets.addr("SQGraphInitializerComponent")
    if not gi:
        out.skipped.append({"check": "lane graph",
                            "reason": "SQGraphInitializerComponent not loaded "
                                      "(no lane graph on this layer)"})
    else:
        want = LANE_GRAPH_OFFSETS["DesignOutgoingLinks"]
        dol = targets.layout("SQGraphInitializerComponent").get(
            "DesignOutgoingLinks")
        _assert(out, f"DesignOutgoingLinks @ +{want:#x}",
                dol is not None and dol.offset == want,
                f"got {dol.offset if dol else None}",
                cls="SQGraphInitializerComponent", field="DesignOutgoingLinks",
                expected=want, live=(dol.offset if dol else None),
                problem="offset drift")
        # ArrayProperty.Inner at FProperty+0x78 (UE 5.7) → a StructProperty
        # whose Struct is named 'SQDesignLink'.
        link_struct = 0
        if dol is not None:
            inner_addr = 0
            raw = pm.try_read(dol.addr + 0x78, 8)
            if raw is not None and len(raw) >= 8:
                inner_addr = _s.unpack("<Q", raw)[0]
            inner = read_fproperty(pm, inner_addr, alloc) if inner_addr else None
            _assert(out, "DesignOutgoingLinks inner is StructProperty",
                    inner is not None and inner.type_name == "StructProperty",
                    f"got {inner.type_name if inner else None}",
                    cls="SQGraphInitializerComponent",
                    field="DesignOutgoingLinks.Inner",
                    expected="StructProperty",
                    live=(inner.type_name if inner else None),
                    problem="ArrayProperty inner is not a StructProperty")
            if inner is not None and inner.type_name == "StructProperty":
                link_struct = read_fstructproperty_struct(pm, inner_addr)
                sname = _object_name(pm, alloc, link_struct)
                _assert(out, "DesignOutgoingLinks inner Struct == 'SQDesignLink'",
                        sname == "SQDesignLink", f"got {sname!r}",
                        cls="SQGraphInitializerComponent",
                        field="DesignOutgoingLinks.Inner.Struct",
                        expected="SQDesignLink", live=sname,
                        problem="lane link struct renamed")
        if link_struct:
            from .ue.reflection import get_class_layout
            ll = get_class_layout(pm, link_struct, alloc)
            na, nb = ll.get("NodeA"), ll.get("NodeB")
            _assert(out, "FSQDesignLink layout (NodeA@+0x00, NodeB@+0x08)",
                    (na is not None and nb is not None
                     and na.offset == LANE_LINK_NODEA_OFF
                     and nb.offset == LANE_LINK_NODEB_OFF
                     and na.type_name == "ObjectProperty"
                     and nb.type_name == "ObjectProperty"),
                    f"NodeA@{na.offset if na else None} "
                    f"NodeB@{nb.offset if nb else None}",
                    cls="SQDesignLink", field="NodeA/NodeB",
                    expected=(LANE_LINK_NODEA_OFF, LANE_LINK_NODEB_OFF),
                    live=(na.offset if na else None, nb.offset if nb else None),
                    problem="lane link layout drift")

    # RouteIndex on the RAAS visualizer — the field that says which lane is
    # live. RAAS-only by design.
    viz = targets.addr("SQGraphRAASVisualizerComponent")
    if not viz:
        out.skipped.append({"check": "lane RouteIndex",
                            "reason": "SQGraphRAASVisualizerComponent not "
                                      "loaded (AAS layer)"})
    else:
        ri = targets.layout("SQGraphRAASVisualizerComponent").get("RouteIndex")
        _assert(out, f"RouteIndex @ +{LANE_VISUALIZER_ROUTE_INDEX_OFF:#x}",
                ri is not None and ri.offset == LANE_VISUALIZER_ROUTE_INDEX_OFF,
                f"got {ri.offset if ri else None}",
                cls="SQGraphRAASVisualizerComponent", field="RouteIndex",
                expected=LANE_VISUALIZER_ROUTE_INDEX_OFF,
                live=(ri.offset if ri else None), problem="offset drift")
    return out


def check_marker_stride(pm: Any, alloc: Any,
                        targets: DoctorTargets) -> CheckOutcome:
    """The marker FastArray element stride.

    Player-placed markers are read out of MarkerArray.Items with a BRUTE-
    FORCED 104-byte stride: no property name to resolve, so a Squad update
    that resizes the item struct would silently misread every marker. This
    walks the property chain to the live element size instead — the machine
    side of MARKER_ARRAY_ITEMS_OFFSET / MARKER_ITEM_SIZE in the register."""
    import struct as _s

    from .squad.snapshot import (
        MARKER_ARRAY_ITEMS_OFFSET, MARKER_ITEM_SIZE,
        MARKER_MGR_MARKER_ARRAY_OFFSET,
    )
    from .ue.reflection import (
        find_field_by_name_with_super, get_class_layout, read_fproperty,
        read_fstructproperty_struct, read_ustruct_header,
    )
    out = _empty()
    mm = targets.addr("SQMapMarkerManagerComponent")
    if not mm:
        out.skipped.append({"check": "marker FastArray stride",
                            "reason": "SQMapMarkerManagerComponent not loaded"})
        return out
    marr = targets.layout("SQMapMarkerManagerComponent").get("MarkerArray")
    marr_ok = (marr is not None
               and marr.offset == MARKER_MGR_MARKER_ARRAY_OFFSET
               and marr.type_name == "StructProperty")
    _assert(out, f"MarkerArray StructProperty @ {MARKER_MGR_MARKER_ARRAY_OFFSET:#x}",
            marr_ok,
            f"got {marr.type_name if marr else None} @ "
            f"{marr.offset if marr else 0:#x}",
            cls="SQMapMarkerManagerComponent", field="MarkerArray",
            expected=MARKER_MGR_MARKER_ARRAY_OFFSET,
            live=(marr.offset if marr else None), problem="offset drift")
    if not marr_ok:
        return out
    # marr came from the class layout; this is a second, independent lookup,
    # so it can come back None on a transient read failure even when the
    # layout said the field is there — that is a skip, not drift.
    ff = find_field_by_name_with_super(pm, mm, "MarkerArray", alloc)
    if ff is None:
        out.skipped.append({"check": "marker FastArray stride",
                            "reason": "MarkerArray did not re-resolve for the "
                                      "Items walk (transient read)"})
        return out
    fa_layout = get_class_layout(pm, read_fstructproperty_struct(pm, ff), alloc)
    items = fa_layout.get("Items")
    items_ok = (items is not None
                and items.offset == MARKER_ARRAY_ITEMS_OFFSET
                and items.type_name == "ArrayProperty")
    _assert(out, f"MarkerArray.Items ArrayProperty @ {MARKER_ARRAY_ITEMS_OFFSET:#x}",
            items_ok,
            f"got {items.type_name if items else None} @ "
            f"{items.offset if items else 0:#x}",
            cls="MarkerArray", field="Items",
            expected=MARKER_ARRAY_ITEMS_OFFSET,
            live=(items.offset if items else None), problem="offset drift")
    if not items_ok or items is None:
        return out
    inner_addr = 0
    raw = pm.try_read(items.addr + 0x78, 8)
    if raw is not None and len(raw) >= 8:
        inner_addr = _s.unpack("<Q", raw)[0]
    inner = read_fproperty(pm, inner_addr, alloc) if inner_addr else None
    if inner is None or inner.type_name != "StructProperty":
        _assert(out, "MarkerArray.Items inner is StructProperty", False,
                f"got {inner.type_name if inner else None}",
                cls="MarkerArray", field="Items.Inner",
                expected="StructProperty",
                live=(inner.type_name if inner else None),
                problem="marker item is not a struct")
        return out
    info = read_ustruct_header(pm, read_fstructproperty_struct(pm, inner_addr),
                               alloc)
    _assert(out, f"marker item stride == {MARKER_ITEM_SIZE} bytes",
            info.properties_size == MARKER_ITEM_SIZE,
            f"live element size {info.properties_size}",
            cls="MarkerArray", field="Items.ElementSize",
            expected=MARKER_ITEM_SIZE, live=info.properties_size,
            problem="marker item stride drift")
    return out


def check_component_to_world(
        pm: Any, alloc: Any, targets: DoctorTargets,
        sample_actors: list[int] | None, *,
        translation_off: int = SCENE_COMPONENT_TO_WORLD_TRANSLATION_OFF,
        ) -> CheckOutcome:
    """ComponentToWorld.Translation, checked BY VALUE against live actors.

    For an actor whose root component is unattached, the cached world
    transform must equal its RelativeLocation. That equality is the only
    handle we have on a private C++ member — and this constant is underneath
    every position the reader reports, which makes it the single most
    valuable thing the machine doctor can check.

    `sample_actors` is a handful of actor addresses the CALLER already holds
    (the serve loop hands over vehicles from its own latest snapshot; the
    human command hands over the snapshot it built). No sample means skip:
    this check must never go discovering actors for itself, because the
    machine path may not walk the object array.

    Two guards against false drift: fewer than `C2W_MIN_SAMPLES` readable
    unattached actors is "nothing to measure", and only a MAJORITY
    disagreeing is drift — one vehicle caught mid-teleport is not."""
    import struct as _s

    from .ue.value import read_fvector
    out = _empty()
    label = ("ComponentToWorld.Translation matches RelativeLocation "
             "on unattached actors")
    if not sample_actors:
        out.skipped.append({"check": "ComponentToWorld",
                            "reason": "no actor sample supplied"})
        return out
    actor_layout = targets.layout("Actor")
    scene_layout = targets.layout("SceneComponent")
    root_p = actor_layout.get("RootComponent")
    rel_p = scene_layout.get("RelativeLocation")
    att_p = scene_layout.get("AttachParent")
    if root_p is None or rel_p is None or att_p is None:
        out.skipped.append({"check": "ComponentToWorld",
                            "reason": "Actor/SceneComponent layout did not "
                                      "resolve"})
        return out

    measured = 0
    mismatched = 0
    for actor in list(sample_actors)[:C2W_MAX_SAMPLES]:
        raw = pm.try_read(int(actor) + root_p.offset, 8)
        if raw is None or len(raw) < 8:
            continue
        root = _s.unpack("<Q", raw)[0]
        if not root:
            continue
        # Attached components carry a parent-relative RelativeLocation, so
        # comparing them would be comparing two different things. The caller
        # is expected to pre-filter; re-checking here costs one read and
        # keeps both callers honest.
        att = pm.try_read(root + att_p.offset, 8)
        if att is None or len(att) < 8 or _s.unpack("<Q", att)[0]:
            continue
        ctw = read_fvector(pm, root + translation_off)
        rel = read_fvector(pm, root + rel_p.offset)
        if ctw is None or rel is None:
            continue
        measured += 1
        if (ctw.x, ctw.y, ctw.z) != (rel.x, rel.y, rel.z):
            mismatched += 1
    if measured < C2W_MIN_SAMPLES:
        out.skipped.append(
            {"check": "ComponentToWorld",
             "reason": f"only {measured} readable unattached actor(s) in the "
                       f"sample (need {C2W_MIN_SAMPLES})"})
        return out
    _assert(out, label, mismatched * 2 <= measured,
            f"{measured - mismatched}/{measured} matched",
            cls="SceneComponent", field="ComponentToWorld.Translation",
            expected=translation_off, live=None,
            problem=f"{mismatched} of {measured} sampled actors disagree with "
                    f"RelativeLocation")
    return out


def collector_field_verdicts(
        players: list[dict]) -> dict[str, tuple[str, str]]:
    """Doctor's verdict on each ODK collector field, from one snapshot.

    Returns ``{field: (verdict, detail)}`` with verdict ``ok`` / ``skip`` /
    ``fail``.

    The rule used to be "some player must be carrying a value, or the offsets
    have drifted", and that cost a diagnostic session. These counters are
    EVENT-GATED: Squad creates a player's entry in a collector when they first
    score in that category, so a server in warmup — or a whole round in which
    nobody happened to destroy a FOB — reads exactly like a broken offset.
    Absence is not evidence of drift. It is not evidence of anything.

    A verified ZERO is a real answer: it says the entry exists and we read it.
    The old rule accepted one only by accident, and then cried drift the moment
    the entry had not been created yet. Confirmed against a live 10.5.3 server
    with a player online building a FOB — fobsBuilt 1, suppliesDelivered 3000,
    defenses 32 — the offsets resolve and track real actions.

    What IS evidence: the two fields of one collector come out of a single
    array entry, so if one of them read and its sibling did not, that struct's
    layout is wrong. That is the only drift this check can honestly assert, and
    now it is the only thing it does assert.
    """
    fields = tuple(COLLECTOR_FIELD_SOURCE)
    if not players:
        return {f: ("skip", "no players online — no counter to read")
                for f in fields}
    carried: dict[str, list[float]] = {f: [] for f in fields}
    for p in players:
        st = p.get("stats") or {}
        for f in fields:
            v = st.get(f)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                carried[f].append(v)
    out: dict[str, tuple[str, str]] = {}
    for f in fields:
        vals = carried[f]
        if vals:
            zeros = sum(1 for v in vals if v == 0)
            out[f] = ("ok", f"{len(vals)}/{len(players)} players, "
                            f"max={max(vals):g}"
                            + (f", {zeros} verified zero" if zeros else ""))
            continue
        sibling = next(s for s in fields if s != f
                       and COLLECTOR_FIELD_SOURCE[s] == COLLECTOR_FIELD_SOURCE[f])
        if carried[sibling]:
            out[f] = ("fail", f"'{sibling}' came out of the same collector "
                              f"entry and this did not — layout drift")
        else:
            out[f] = ("skip", "event-gated — nobody has scored one yet")
    return out


def check_collector_fields(players: list[dict] | None) -> CheckOutcome:
    """The collector verdicts in drift/skip terms.

    Only a `fail` is drift: it means one field of a collector entry read and
    its sibling — out of the SAME array entry — did not, which no amount of
    event-gating explains. A `skip` is an empty or unscored server and is
    reported as such.

    `players is None` means the caller has no snapshot to judge from (the
    machine path deliberately builds none) — nothing to measure, and not even
    a skip note: a permanent by-design entry in the payload would bury the
    situational skips Item G exists to surface."""
    out = _empty()
    if players is None:
        return out
    gated: list[str] = []
    for field, (verdict, detail) in collector_field_verdicts(players).items():
        if verdict == "fail":
            _assert(out, f"collector field '{field}' readable", False, detail,
                    cls="collectors", field=field, expected=None, live=None,
                    problem=detail)
        elif verdict == "skip":
            gated.append(field)
        else:
            out.notes.append({"label": f"collector field '{field}' readable",
                              "ok": True, "detail": detail})
    if gated:
        out.skipped.append({"check": "collector fields",
                            "reason": "no value to read yet: "
                                      + ", ".join(gated)})
    return out


def _assert(out: CheckOutcome, label: str, ok: bool, detail: str, *,
            cls: str, field: str, expected: Any, live: Any,
            problem: str) -> None:
    """Record one assertion in both faces at once — the human line and, when
    it failed, the machine drift record. Keeping them in a single call is what
    stops the printed doctor and the reported doctor from disagreeing."""
    out.notes.append({"label": label, "ok": ok, "detail": detail})
    if not ok:
        out.drift.append({"class": cls, "field": field, "expected": expected,
                          "live": live, "problem": problem})


# ----- the machine signal ---------------------------------------------------

def _run_checks(pm: Any, alloc: Any, targets: DoctorTargets,
                sample_actors: list[int] | None
                ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Every check, in one pass over already-resolved targets.

    Order: the offset tables and required names first (unchanged), then the
    checks that used to be human-only. Returns (drift, checks)."""
    drift: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    table_drift, table_skipped = check_offset_drift(pm, None, alloc, targets)
    drift.extend(table_drift)
    for s in table_skipped:
        checks.append({"check": f"hardcoded offsets on {s['class']}",
                       "state": "skipped", "reason": s["reason"]})

    name_drift, name_skipped = check_required_names(pm, None, alloc, targets)
    drift.extend(name_drift)
    for s in name_skipped:
        checks.append({"check": f"required names on {s['class']}",
                       "state": "skipped", "reason": s["reason"]})

    struct_drift, struct_skipped = check_struct_fields(pm, None, alloc, targets)
    drift.extend(struct_drift)
    for s in struct_skipped:
        checks.append({"check": f"struct fields in {s['class']}",
                       "state": "skipped", "reason": s["reason"]})

    for outcome in (check_reflection_anchors(pm, alloc, targets),
                    check_lane_graph(pm, alloc, targets),
                    check_marker_stride(pm, alloc, targets),
                    check_component_to_world(pm, alloc, targets, sample_actors)):
        drift.extend(outcome.drift)
        for s in outcome.skipped:
            checks.append({"check": s["check"], "state": "skipped",
                           "reason": s["reason"]})
        for n in outcome.notes:
            entry: dict[str, Any] = {
                "check": n["label"],
                "state": "passed" if n["ok"] else "failed"}
            if not n["ok"]:
                entry["reason"] = n["detail"]
            checks.append(entry)
    return drift, checks


def run_doctor(pm: Any, arr: Any, alloc: Any, *,
               sample_actors: list[int] | None = None) -> dict[str, Any]:
    """Machine-readable health of the reader against the LIVE process.

    Caller passes already-resolved anchors (arr=GUObjectArray, alloc=FNamePool)
    — in the serve loop these are the pipeline's own handles, so a check-in costs
    a reflection walk, not a rediscovery.

    `sample_actors` is an optional handful of actor addresses the caller
    already has (the serve loop's own latest snapshot). It costs the caller
    nothing to hand over and it is the only way the ComponentToWorld check can
    run without building a snapshot of its own — which this path must never
    do. Without it that one check is skipped and says so.

    Returns {state, ok, drift, checks, reason?} where state is:
      * "ok"      — every hardcoded offset still matches live reflection
      * "drift"   — at least one offset moved / a class vanished (Squad patched us)
      * "unknown" — the core class can't be resolved yet (server empty / map
                    loading); NOT reported as drift, so a loading server never
                    triggers a false "broken" alarm.
    """
    checks: list[dict[str, Any]] = []

    # Anchors must be usable before any reflection is meaningful.
    try:
        anchors_ok = (0 < int(arr.num_elements) < (1 << 26)
                      and alloc.fname_to_str(0, 0) == "None")
    except Exception:
        anchors_ok = False
    if not anchors_ok:
        return {"state": "unknown", "ok": True, "drift": [], "checks": checks,
                "reason": "anchors not resolvable (GUObjectArray/FNamePool)"}

    targets = resolve_targets(pm, arr, alloc)
    # Core class gate: if SQPlayerState's UClass isn't resolvable, the server is
    # between maps / empty. Treat as unknown, never drift (plan risk #3).
    if not targets.addr("SQPlayerState"):
        return {"state": "unknown", "ok": True, "drift": [], "checks": checks,
                "reason": "SQPlayerState unresolved (server empty / map loading)"}

    drift, checks = _run_checks(pm, alloc, targets, sample_actors)
    if drift and targets.from_cache:
        # Never report drift on cached addresses. Re-resolving costs one walk
        # and happens only on the way to an alarm, so the cache can make the
        # doctor faster but never wrong.
        targets = resolve_targets(pm, arr, alloc, use_cache=False)
        if not targets.addr("SQPlayerState"):
            return {"state": "unknown", "ok": True, "drift": [],
                    "checks": [],
                    "reason": "SQPlayerState unresolved (server empty / map loading)"}
        drift, checks = _run_checks(pm, alloc, targets, sample_actors)
    return {
        "state": "ok" if not drift else "drift",
        "ok": not drift,
        "drift": drift,
        "checks": checks,
    }
