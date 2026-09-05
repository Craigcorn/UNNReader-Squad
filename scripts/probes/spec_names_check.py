"""Does every memory name in the capture spec resolve, and what else do
those classes carry? (test T11 — `docs/command-assets-spec.md`)

The spec names classes, structs and properties the recorder will read by
reflection. This probe resolves every one of them and reports each as
found (with reflected type and offset) or missing, then lists every other
property those classes carry beyond engine boilerplate, so the spec's
"deliberately not recorded" section can be exhaustive instead of a memory
of what was noticed.

Two modes, because the classes live in two places:

  live     attach to the running server and reflect the classes that are
           loaded now (the native SQ classes, the structs, the drone and
           marker masters on a layer that has them):
             .venv/bin/python scripts/probes/spec_names_check.py
  archive  read the reflected layouts archived from earlier sessions
           (flat JSON: property name -> {offset, type}) for the content
           classes that exist only during a call:
             python scripts/probes/spec_names_check.py --archive DIR [DIR ...]

Read-only; one shot; exit 0 always (the report is the result). Output is
JSONL, one row per class, to /tmp/spec_names_check.jsonl (live) or --out.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

# (class, meta-class, [property names the spec reads], where the spec uses it)
EXPECTED: list[tuple[str, str, list[str], str]] = [
    ("SQTeamState", "Class", ["CommanderState"], "§3 identity hop"),
    ("SQCommanderState", "Class",
     ["CurrentCommander", "bCommanderIsActive", "bActionsEnabled",
      "bVoteInProgress", "CommanderVoteTimer", "CommanderVoteTimestamp",
      "bVoteCooldownActive", "VoteCooldownTimer", "VoteCooldownTimestamp",
      "CommanderCategories", "LastCategoryGameTime", "CommandIntervals",
      "NomineeStatus"], "§3 commander block"),
    ("SQCommanderManager", "Class",
     ["bCommanderActive", "VotingTimeSeconds", "VoteCooldownTimeSeconds",
      "ActionCooldownExtensionOnNewCommander", "MinimumSquadSizeForVoting",
      "MinimumSquadsRequiredForVoting"], "§4 rules"),
    ("SQCommandActionData", "ScriptStruct",
     ["CommandActionData", "GameTimeAtCreation", "CooldownTimeRemaining",
      "IsDestroyedDuringActive"], "§3 actions[]"),
    ("SQCommandActionDataFASItem", "ScriptStruct", ["Content"], "§3 actions[]"),
    ("CommanderVoteNominee", "ScriptStruct", ["NomineeState", "VoteCount"],
     "§3 nominees[]"),
    ("CommanderCategory", "ScriptStruct", ["Name", "CooldownDuration"],
     "§3 categories[]"),
    ("SQPlayerState", "Class", ["PlayerNamePrivate", "OnlineUserId"],
     "§2 identity"),
    ("Controller", "Class", ["PlayerState"], "§2 controller -> player state"),
    ("Pawn", "Class", ["PlayerState", "Controller", "LastHitBy"],
     "§7 pawn fields (inherited by the drones)"),
    ("SQFlyingDrone", "Class", ["PlayerState", "LastHitBy"],
     "§7/§8 merged layout"),
    ("BP_FlyingDrone_C", "BlueprintGeneratedClass",
     ["SQ PC", "HealthComponent", "Dead", "Command Action"], "§7 drones[]"),
    ("BP_FlyingDrone_Recoverable_C", "BlueprintGeneratedClass",
     ["BatteryLifetimeMax"], "§7 drones[]"),
    ("HealthComponent_C", "BlueprintGeneratedClass", ["Health", "Max Health"],
     "§7 health"),
    ("BP_MapMarker_CommandMaster_C", "BlueprintGeneratedClass",
     ["Distance", "AddDistance", "Action", "Request"], "§5 markers"),
    ("BP_MapMarker_DirectorMaster_C", "BlueprintGeneratedClass",
     ["Distance"], "§5 markers"),
    ("BP_MapMarker_CommandPath_C", "BlueprintGeneratedClass",
     ["Distance", "AddDistance", "Action", "Request"], "§5 markers"),
    ("BP_MapMarker_CommandLine_C", "BlueprintGeneratedClass",
     ["Distance", "AddDistance", "Action", "Request"], "§5 markers"),
    ("BP_MapMarker_CommandLineRadius_C", "BlueprintGeneratedClass",
     ["Distance", "AddDistance", "Action", "Request"], "§5 markers"),
    ("BP_MapMarker_CommandRadius_C", "BlueprintGeneratedClass",
     ["Distance", "AddDistance", "Action", "Request"], "§5 markers"),
    ("BP_MapMarker_CommandRadius_Friendly_C", "BlueprintGeneratedClass",
     ["Distance", "AddDistance", "Action", "Request"], "§5 markers"),
    ("BP_MapMarker_Command_Request_C", "BlueprintGeneratedClass",
     ["Distance", "AddDistance", "Action", "Request"], "§5 markers"),
    ("BP_MapMarker_Command_SLRequest_C", "BlueprintGeneratedClass",
     ["Distance", "AddDistance", "Action", "Request"], "§5 markers"),
]

ACTOR_COMMON = ["Distance", "Team", "DamageInstigatorController", "Action",
                "Action Destroyed", "Destroy Delay after Action Destroyed"]
STRIKE = ["Health", "Dead_0", "CurrentShotsMade", "MaxShots", "Spline Distance",
          "Origin Location"]
ARTILLERY = ["Origin Location", "target location", "Max Drop Radius",
             "Pre Warning Shells", "Shells Per Barrage", "Barrage Count",
             "Current Barrage", "Projectile"]
EXPECTED += [
    ("BP_CommandActor_Artillery_Creep_C", "BlueprintGeneratedClass",
     ACTOR_COMMON + ARTILLERY, "§6 artillery"),
    ("BP_CommandActor_Mortar_Radius_C", "BlueprintGeneratedClass",
     ACTOR_COMMON + ARTILLERY, "§6 artillery"),
    ("BP_CommandActor_FA18_Rockets_Strafe_USMC_C", "BlueprintGeneratedClass",
     ACTOR_COMMON + STRIKE, "§6 strike"),
    ("BP_CommandActor_SU25_Bomb_Strafe_C", "BlueprintGeneratedClass",
     ACTOR_COMMON + STRIKE, "§6 strike"),
    ("BP_CommandActor_UAV_MQ9_C", "BlueprintGeneratedClass",
     ACTOR_COMMON + ["Health", "Dead_0"], "§6 UAV"),
    ("BP_CommandActor_Drone_C", "BlueprintGeneratedClass",
     ACTOR_COMMON + ["Health", "SQ PC"], "§6 drone call actor"),
    ("CommandAction_Drone_C", "BlueprintGeneratedClass",
     ["CategoryId", "EnrouteDuration", "ActiveDuration", "CooldownDuration"],
     "§3 action config"),
    ("CommandAction_Mortar_Barrage_INS_C", "BlueprintGeneratedClass",
     ["CategoryId", "EnrouteDuration", "ActiveDuration", "CooldownDuration"],
     "§3 action config"),
    ("CommandAction_Mortar_Barrage_IMF_C", "BlueprintGeneratedClass",
     ["CategoryId", "EnrouteDuration", "ActiveDuration", "CooldownDuration"],
     "§3 action config"),
]

# Engine levels whose own properties are boilerplate, not game fields.
BOILERPLATE = {"Object", "Actor", "Pawn", "Character", "Info", "PlayerState",
               "Controller", "PlayerController", "ActorComponent",
               "SceneComponent", "PrimitiveComponent", "MeshComponent",
               "FastArraySerializerItem", "ReplicationGraphNode"}
ACTOR_SIZE = 0x2b8  # archive mode: everything at or past Actor's end is game-level


def check(expected: list[str], layout: dict[str, tuple[str, int]]) -> dict:
    props = {}
    for n in expected:
        hit = layout.get(n)
        props[n] = ({"ok": True, "type": hit[0], "offset": hit[1]} if hit
                    else {"ok": False})
    return props


def run_archive(dirs: list[str], out_path: str) -> int:
    rows = []
    for cls, meta, names, use in EXPECTED:
        found = None
        for d in dirs:
            p = os.path.join(d, cls + ".json")
            if os.path.isfile(p):
                found = p
                break
        if not found:
            rows.append({"class": cls, "meta": meta, "use": use, "found": False,
                         "source": "archive"})
            continue
        raw = json.load(open(found, encoding="utf-8"))
        layout = {k: (v.get("type"), v.get("offset")) for k, v in raw.items()
                  if isinstance(v, dict) and "offset" in v}
        threshold = 0 if cls.startswith("CommandAction_") else ACTOR_SIZE
        unused = sorted(
            ({"name": k, "type": t, "offset": o} for k, (t, o) in layout.items()
             if o >= threshold and k not in names and k != "UberGraphFrame"),
            key=lambda r: r["offset"])
        rows.append({"class": cls, "meta": meta, "use": use, "found": True,
                     "source": "archive:" + os.path.basename(os.path.dirname(found))
                     + "/" + os.path.basename(found),
                     "props": check(names, layout), "unused": unused})
    return report(rows, out_path)


def run_live(out_path: str) -> int:
    from probe_common import attach
    from sqreader.ue.reflection import describe_class

    a = attach()
    pm, arr, alloc = a.pm, a.arr, a.alloc
    targets = {cls: meta for cls, meta, _, _ in EXPECTED}
    hits = arr.find_all_by_names(targets, alloc=alloc)
    rows = []
    for cls, meta, names, use in EXPECTED:
        if cls not in hits:
            rows.append({"class": cls, "meta": meta, "use": use, "found": False,
                         "source": "live"})
            continue
        addr = hits[cls][1]
        try:
            levels = describe_class(pm, addr, alloc)
        except Exception as e:  # noqa: BLE001 — report, never die
            rows.append({"class": cls, "meta": meta, "use": use, "found": True,
                         "source": "live", "error": repr(e)})
            continue
        layout: dict[str, tuple[str, int]] = {}
        unused = []
        for info, props in levels:
            for p in props:
                layout.setdefault(p.name, (p.type_name, p.offset))
                if info.name not in BOILERPLATE and p.name not in names \
                        and p.name != "UberGraphFrame":
                    unused.append({"name": p.name, "type": p.type_name,
                                   "offset": p.offset, "level": info.name})
        rows.append({"class": cls, "meta": meta, "use": use, "found": True,
                     "source": "live", "addr": f"{addr:#x}",
                     "chain": [info.name for info, _ in levels],
                     "props": check(names, layout),
                     "unused": sorted(unused, key=lambda r: r["offset"])})
    return report(rows, out_path)


def report(rows: list[dict], out_path: str) -> int:
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            r["t"] = round(time.time(), 3)
            f.write(json.dumps(r) + "\n")
    n_cls = n_found = n_props = n_ok = 0
    for r in rows:
        n_cls += 1
        if not r["found"]:
            print(f"{r['class']:<44s} NOT LOADED/ARCHIVED")
            continue
        n_found += 1
        if "error" in r:
            print(f"{r['class']:<44s} ERROR {r['error']}")
            continue
        miss = [n for n, v in r["props"].items() if not v["ok"]]
        n_props += len(r["props"])
        n_ok += len(r["props"]) - len(miss)
        tag = "ok" if not miss else "MISSING " + ", ".join(miss)
        print(f"{r['class']:<44s} {len(r['props']) - len(miss)}/{len(r['props'])} "
              f"{tag}; {len(r['unused'])} other game-level props "
              f"[{r['source']}]")
    print(f"\nclasses: {n_found}/{n_cls} resolvable here; "
          f"properties: {n_ok}/{n_props} found; rows -> {out_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", nargs="+", metavar="DIR",
                    help="check against archived layout JSONs instead of live")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.archive:
        return run_archive(args.archive, args.out or "spec_names_check.archive.jsonl")
    return run_live(args.out or "/tmp/spec_names_check.jsonl")


if __name__ == "__main__":
    sys.exit(main())
