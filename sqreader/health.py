"""Structured reader-health check — the machine-readable "did a Squad update
break us?" signal.

`cmd_doctor` (cli.py) is the rich, human-facing command; this module is its
programmatic sibling. `run_doctor()` returns a plain dict that the fleet/update
system reports to central (Phase 0 telemetry) and, later, uses as the
post-offset-apply verification gate (Phase 1).

Both derive their expectations from the SAME module-level offset constants in
`squad.snapshot` via `hardcoded_offset_tables()`, so the human command and the
machine signal can never silently disagree about what "correct" means.

The core signal is cheap: for each Squad class we hardcode offsets for, walk its
live UClass layout by reflection and compare. A drifted or vanished field is the
fingerprint of a Squad patch that moved a struct. Reads are never fabricated
(the reader nulls bad reads), so drift shows up here as a mismatch, not a crash.
"""
from __future__ import annotations

from typing import Any


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

      * SQ_SEATCOMP_SEAT_CONFIG_OFFSET (0x250), SQ_TURRET_INVENTORY_OFFSET
        (0x4c0), SQ_INV_CURRENT_WEAPON_OFFSET (0x1b8) — value-correlated
        probe discoveries whose UPROPERTY names have never been seen in a
        reflection dump. Verify the names live, then promote them here.
      * AMMO_WEP_OFFSETS — read off `AmmoWep_*_C` blueprint actors; no single
        UClass to check against and the declaring base class is unverified.
        Same promotion path.
      * COLLECTOR_OFFSETS — private C++ members, not UPROPERTIES at all, so
        reflection cannot resolve them by name in any Squad build. Validated
        instead by the value-based collector verdicts (`cmd_doctor`,
        `tests/test_doctor_collectors.py`).
      * LANE_GRAPH_OFFSETS / LANE_LINK_* / LANE_VISUALIZER_ROUTE_INDEX_OFF —
        verified by `cmd_doctor`'s dedicated lane-graph section, which knows
        the AAS/RAAS mode differences a flat table cannot express (the RAAS
        visualizer is absent on AAS layers BY DESIGN — tabling it would
        report drift on half the map rotation).
      * MARKER_ITEM_OFFSETS, SQ_SEATCOMP_ANIM_STATE_OFFSET,
        SQ_SEATCOMP_FORCE_OCCUPIED_OFFSET, SQ_VEHCOMP_STATE_OFFSET — declared
        but never read, so nothing can drift through them. Watch them the day
        they are first read.
      * STRUCT-INTERNAL offsets — fields addressed relative to a struct, not a
        class: THI_* / HR_* (inside FSQTakeHitInfo and its FHitResult),
        MARKER_ARRAY_ITEMS_OFFSET and the derived MARKER_ITEMS_ABS_OFFSET
        (FastArraySerializer `Items`), MARKER_ITEM_SIZE / the per-item field
        offsets (brute-forced stride, no property names). A class table cannot
        express these. The follow-up that WOULD cover the named ones is a
        second check tier built on `struct_layout_for_field` — the same call
        `resolve_paths` already uses for PlayerStateData. Until then damage-
        event struct drift stays visible only as implausible values."""
    from .squad.snapshot import (
        AACTOR_OWNER_OFFSET, DEPLOYABLE_OFFSETS, FOB_RESOURCE_OFFSETS,
        MARKER_MGR_MARKER_ARRAY_OFFSET, PC_PLAYER_STATE_OFFSET,
        PC_PLAYER_STATS_INDEX_OFFSET, PC_RECENT_VOICE_CHANNEL_OFFSET,
        VEHICLE_SPAWNER_OFFSETS,
        MARKER_OFFSETS, PROJECTILE_INSTIGATOR_CONTROLLER_OFFSET,
        PROJECTILE_OFFSETS, RALLY_OFFSETS,
        SQ_DEPLOYABLE_VEHICLE_GUN_MOUNT_OFF, SQ_DEPLOYABLE_VEHICLE_OWNING_OFF,
        SQ_DEPLOYABLE_VEHICLE_SWIVEL_OFF, SQ_SEATCFG_ATTACH_SOCKET_OFF,
        SQ_SEATCOMP_SEAT_PAWN_OFFSET, SQ_SEATCOMP_SEATED_PLAYER_OFFSET,
        SQ_SEATCOMP_SEATED_SOLDIER_OFFSET, SQ_SOLDIER_TAKE_HIT_INFO_OFFSET,
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
        }),
        ("SQVehicleSeat", "Class", False,
         {"SeatHealth": SQ_VEHICLESEAT_SEAT_HEALTH_OFFSET}),
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
        # pattern. OPTIONAL: `resolve_paths` treats the class as optional, so
        # a level without one must skip, not cry drift.
        ("SQDeployableVehicle", "Class", True, {
            "OwningDeployable":    SQ_DEPLOYABLE_VEHICLE_OWNING_OFF,
            "SwivelMeshComponent": SQ_DEPLOYABLE_VEHICLE_SWIVEL_OFF,
            "GunMountComponent":   SQ_DEPLOYABLE_VEHICLE_GUN_MOUNT_OFF,
        }),
        # The seat's role-socket offset lives inside a ScriptStruct, not a
        # UClass — which is what the meta-class column is for. Optional for
        # the same reason as the row above.
        ("SQVehicleSeatConfig", "ScriptStruct", True,
         {"SeatAttachSocket": SQ_SEATCFG_ATTACH_SOCKET_OFF}),
        # FOB radio resource pool — reflection-first with these as fallback
        # (see `f_off` in read_deployable). The BP that declares them is a
        # BlueprintGeneratedClass, and optional: a level with no FOB built
        # has not loaded it.
        ("BP_BaseFobCreator_C", "BlueprintGeneratedClass", True,
         dict(FOB_RESOURCE_OFFSETS)),
        # Player-controller fields, reflection-first with these as fallback.
        # Optional because a dedicated server's controller is the blueprint
        # variant; `resolve_paths` accepts either and so does this row's
        # absence.
        ("SQPlayerController", "Class", True, {
            "PlayerStatsIndex":   PC_PLAYER_STATS_INDEX_OFFSET,
            "PlayerState":        PC_PLAYER_STATE_OFFSET,
            "RecentVoiceChannel": PC_RECENT_VOICE_CHANNEL_OFFSET,
        }),
        # The map-marker manager's MarkerArray struct. Its FastArray `Items`
        # offset INSIDE that struct is struct-internal — see the register.
        ("SQMapMarkerManagerComponent", "Class", True,
         {"MarkerArray": MARKER_MGR_MARKER_ARRAY_OFFSET}),
    ]


def required_reflection_names() -> list[tuple[str, str, list[str]]]:
    """Reflection-only reads with no fallback constant: a Squad rename makes
    them silently vanish from recordings (fail-safe, but dark). Listing a
    (type, meta-class, [property names]) row here turns that rename into a
    drift report instead.

    Semantics in `check_required_names`: a type that is absent from the
    GUObjectArray is SKIPPED, not drift — several of these only load once
    the matching content exists in the level (a medic item, an emplacement
    gun). A type that is present but missing a listed name is drift."""
    return [
        # Medical capture (per-player `medical` dict).
        ("SQSoldier", "Class", ["CurrentHeldItem"]),
        ("SQHealingEquipableItem", "Class", ["HealedTarget", "ItemCount"]),
        # Commander identity: the team-state pointer and the hop the
        # identity read takes through the commander-state actor.
        ("SQTeamState", "Class", ["CommanderState"]),
        ("SQCommanderState", "Class", ["CurrentCommander"]),
    ]


def check_required_names(pm: Any, arr: Any, alloc: Any
                         ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Verify every `required_reflection_names` row against live reflection.

    Returns (drift, skipped): drift records for present-but-missing names,
    and skip notes for types not currently loaded (never drift)."""
    from .ue.reflection import get_class_layout
    drift: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for cls, kind, names in required_reflection_names():
        hits = arr.find_by_name(cls, class_name=kind, alloc=alloc, limit=1)
        if not hits:
            skipped.append({"class": cls, "reason": "type not loaded"})
            continue
        live = get_class_layout(pm, hits[0][1], alloc)
        for fname in names:
            if fname not in live:
                drift.append({"class": cls, "field": fname, "expected": None,
                              "live": None,
                              "problem": "required name not reflected"})
    return drift, skipped


def check_offset_drift(pm: Any, arr: Any, alloc: Any) -> list[dict[str, Any]]:
    """Compare every hardcoded offset table against live reflection.

    Returns a list of drift records (empty == all offsets still valid). Each
    record is {class, field, expected, live, problem}. A REQUIRED class that is
    missing while the core classes ARE present is itself a drift signal
    (renamed/removed); an OPTIONAL one that is missing is simply not in this
    level and is skipped.
    """
    from .ue.reflection import get_class_layout
    drift: list[dict[str, Any]] = []
    for cls, kind, optional, table in hardcoded_offset_tables():
        hits = arr.find_by_name(cls, class_name=kind, alloc=alloc, limit=1)
        if not hits:
            if not optional:
                drift.append({"class": cls, "field": "*", "expected": None,
                              "live": None, "problem": "class not found"})
            continue
        live = get_class_layout(pm, hits[0][1], alloc)
        for fname, off in table.items():
            p = live.get(fname)
            if p is None:
                drift.append({"class": cls, "field": fname, "expected": off,
                              "live": None, "problem": "not reflected"})
            elif p.offset != off:
                drift.append({"class": cls, "field": fname, "expected": off,
                              "live": p.offset, "problem": "offset drift"})
    return drift


def run_doctor(pm: Any, arr: Any, alloc: Any) -> dict[str, Any]:
    """Machine-readable health of the reader against the LIVE process.

    Caller passes already-resolved anchors (arr=GUObjectArray, alloc=FNamePool)
    — in the serve loop these are the pipeline's own handles, so a check-in costs
    a reflection walk, not a rediscovery.

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

    # Core class gate: if SQPlayerState's UClass isn't resolvable, the server is
    # between maps / empty. Treat as unknown, never drift (plan risk #3).
    if not arr.find_by_name("SQPlayerState", class_name="Class", alloc=alloc, limit=1):
        return {"state": "unknown", "ok": True, "drift": [], "checks": checks,
                "reason": "SQPlayerState unresolved (server empty / map loading)"}

    drift = check_offset_drift(pm, arr, alloc)
    name_drift, skipped = check_required_names(pm, arr, alloc)
    drift.extend(name_drift)
    for s in skipped:
        checks.append({"check": f"required names on {s['class']}",
                       "state": "skipped", "reason": s["reason"]})
    return {
        "state": "ok" if not drift else "drift",
        "ok": not drift,
        "drift": drift,
        "checks": checks,
    }
