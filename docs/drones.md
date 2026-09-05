# Drones — capture findings and test plan

Status: **capture proposal agreed 2026-09-04 (decision 7 of the commander
plan); test T7 run and decoded 2026-09-05** — five recon-drone flights on
the test box, one player, the probe at 10 Hz — settling flight time,
health, damage attribution, identity across pickup, speed, team and the
after-death behaviour. What remains to observe is tracked, not listed
here: test T7 carries the hand-off remainder, test T8 the commander-drone
confirmations, and the 4 Hz check follows the implementation (W18).
Earlier findings: the 2026-09-02
commander session (one commander drone flight, shot down) and the
2026-09-03 session (recon drone class observed loading). Companion to
`docs/command-assets.md`, which owns the commander side; this document
owns the drone pawn, whoever launched it. Player names are deliberately
absent. Offsets quoted are the values of the day; the implementation
resolves every field by reflection name.

## What a drone is to the recorder

Two in-game things share one class:

- **The commander's drone**: called as a command asset
  (`CommandAction_Drone_C`, category 0, 10 s enroute, 600 s active, 600 s
  cooldown), spawned through `BP_CommandActor_Drone_C` plus a transient
  `BP_Deployable_DroneSpawner_C`, flown as `BP_FlyingDrone_C`. Observation
  only — it cannot harm players. Its flight budget is the call's active
  window (600 s), which the commander block records; the pawn carries no
  battery field of its own.
- **The recon kit's drone**: some factions' recon class carries a
  deployable drone, flown as `BP_FlyingDrone_Recoverable_C`, a subclass
  of the commander drone's class that adds `BatteryLifetimeMax`. Its
  launcher is the kit item itself, `BP_Deployable_DroneItem_Recoverable_C`
  in the soldier's inventory (seen by the inventory probe on 2026-09-05);
  no `BP_Deployable_DroneSpawner_C` instance appears for a recon deploy.
  Flight budget 100 s; can be picked up and redeployed; after destruction
  or timeout it re-arms at an ammo source and respawns. Not tied to the
  commander role.

Both pawns derive from `SQFlyingDrone`, itself a `Character` — a
soldier-shaped object, not an `SQVehicle`. That is why no recording has
ever seen a drone: soldiers reach the file through their player, vehicles
through the vehicle list, and the drone is neither. One capture path
keyed on `SQFlyingDrone` covers both and any future variant. On
2026-09-05 both classes resolved every field the probe asked for; only
`BatteryLifetimeMax` is recon-only.

## Verified in memory

### 2026-09-05 — five recon flights (`BP_FlyingDrone_Recoverable_C`, test T7)

| Flight | Alive | `Dead` at | Rows stopped | Final position | Notes |
|---|---|---|---|---|---|
| 1 | 97.7 s (piloted throughout) | +97.8 s | death + 72.4 s | 296 m below ground | timed out; fell through the world |
| 2 | 94.2 s (piloted) | +97.7 s | death + 59.7 s | zeroed to the map origin | timed out; the flat-out speed run |
| 3 | 31.4 s | never | at pickup | last landed position | landed, exited, picked up: the pawn vanished at pickup |
| 4 | 94.2 s (piloted 48–64 s, then abandoned) | +97.3 s | death + 92.6 s | zeroed, after falling 4 km | left unpiloted; the battery ran regardless |
| 5 | 48.5 s (landed, shot by the pilot) | +50.9 s | death + 73.3 s | zeroed | destroyed; health 15 → 0 in one 100 ms sample |

- **Flight time (D1).** Nothing on the pawn counts down. `BatteryLifetimeMax`
  (double, recon subclass, +0x7c0) read 100.0 and every timed-out flight
  hit `Dead` 97.3–97.8 s after the probe first saw the pawn — the probe
  scans for new pawns once a second and the deploy animation precedes the
  spawn, so the budget is 100 s from spawn. The battery runs whether or
  not anyone is flying (flight 4). `EndFlightTimer` is a timer handle, not
  a time; `BleedOutTime` read 30.0 constantly and is not the linger
  either. Flight time remaining is therefore a viewer derivation: the
  frame the pawn first appears plus the budget. For the commander drone
  the budget is the action's active window from the commander block.
- **Health and damage (D2).** `HealthComponent` → `HealthComponent_C`
  with `Health` (float, +0xb4) and `Max Health` (double, +0x120) — 15.0
  and 15.0 on every live row. One rifle burst took it to 0.0 with `Dead`
  true in the same sample; no intermediate value was seen at 10 Hz, so a
  recon drone is effectively one-hit. Timeout leaves health at 15.
- **Shoot-down attribution (D5).** At the kill `LastHitBy` resolved to
  the shooter's controller (the pilot shot his own landed drone; the
  pointer is the same one the possession fields use). Attribution is
  readable from the pawn — closing the drone half of the commander doc's
  open item A1; the strike-aircraft half remains unobserved.
- **Identity across pickup and redeploy (D3).** Picking a landed drone up
  destroys the pawn; the redeploy is a new pawn at a new address. A
  re-arm after timeout is likewise a new pawn. Every deploy is therefore a
  new drone id in a recording, and "the same drone" across a pickup is a
  viewer join on owner and time, never an identity the recorder can see.
- **Possession and owner (D7, half of D6).** While flying,
  `PlayerState`, `Controller` and `PreviousController` all name the pilot.
  On landing and exiting, all three null; `SQ PC` keeps naming the last
  pilot through the de-possession, through death and until the pawn is
  gone. A freshly deployed drone reads `SQ PC` = the deployer before
  anyone flies it. So `SQ PC` is the owner and the source of team for a
  pilotless drone; `PlayerState` is the live pilot. What a hand-off does
  to `SQ PC` is still unobserved (D6).
- **Speed (D4).** Flat out and level for 40 s: 9–10.6 m/s in every
  five-second bucket, best ten-second mean 11.4 m/s, no boost observed.
  Single-sample spikes to 28 m/s are replication steps, not speed. Cruise
  matches the 2026-09-02 commander drone (~10 m/s). At 4 Hz that is
  2.5 m per sample.
- **After death (the "gone" semantics).** A dead drone is not a wreck on
  the ground: an airborne one falls through the world (flight 1 ended
  296 m below ground, flight 4 nearly 4 km below), a landed one stays
  put; on its final tick the position is zeroed to the map origin, and
  the pawn is removed 60–93 s after death — not a fixed timer, and not
  the 30 s `BleedOutTime`. For the recorder: emit `dead` truthfully with
  whatever position the pawn has, gate a zeroed position as absent
  (exactly as the origin-parked vehicles are gated), and let the viewer
  stop drawing a drone the moment it is dead.
- `Command Action` read null on every recon row (it names the calling
  action on a commander drone). `Max Fly Height` read 2200.0 constantly
  while flight 1 climbed 66 m above its spawn, so it is not a ceiling
  above the spawn point; its meaning is unresolved and it is not
  proposed for capture. `Can Possess` drops to false at death.

### 2026-09-02 — one commander drone flight (`BP_FlyingDrone_C`)

- Position and yaw through the normal root transform; one cruising
  sample at ~10 m/s.
- Possession chain: `PlayerState` (+0x2d8), `Controller` (+0x2e8),
  `PreviousController` (+0x2f0) — all three null while de-possessed,
  restored on re-possession. Possession is logged (`OnPossess` lines with
  full player ids); nothing else about a drone is logged, including its
  death.
- Death: the pawn despawned 74 s after the commander state's
  `IsDestroyedDuringActive` flipped — consistent with the 60–93 s linger
  measured on the recon drone; the commander's cooldown restarts from the
  destruction (see the commander doc).

## Proposed capture (decision 7, agreed in principle 2026-09-04; fields pinned 2026-09-05)

1. **Full frame**: a `drones` list, one entry per live `SQFlyingDrone`
   pawn of any class, emitted only while a pawn exists and omitted when
   its position is zeroed or the pawn is gone. Fields, all direct reads
   resolved by reflection name: `id` (the pawn address, as vehicles use;
   new on every deploy), `class`, `position`, `yaw`, `dead` (`Dead`),
   `health` and `maxHealth` (the health component's `Health` and
   `Max Health`), `pilotEosId` (`PlayerState` → id; null while nobody
   flies it), `ownerEosId` (`SQ PC` → its player state → id; the deployer
   or last pilot, persists through de-possession and death; team derives
   from it), `commandAction` (`Command Action` class name; null on a recon
   drone), `batteryLifetimeMax` (`BatteryLifetimeMax`, seconds; emitted
   when the class has it), `lastHitByEosId` (`LastHitBy` → its player
   state → id; emitted when set). No remaining-time field: the viewer
   derives it from the first frame the id appears plus the budget — the
   pawn's battery for a recon drone, the action's active window for a
   commander drone.
2. **4 Hz position frames**: drones join players and vehicles as a third
   sampled set, `{id, x, y, z, yaw}` with the same `id` as the full-frame
   entry and the same freshness gates (class pointer, sane coordinates; a
   zeroed position or a freed pawn is omitted, never guessed). Measured
   cost: ~115 B of raw JSON per drone per sample, ~28 KB on disk per
   10-minute flight, 3–4 small reads per drone per sample. Touch points:
   `possample.SampledEntities` / `sample_positions`, the position-frame
   `drones` key, the viewer reconstructor, `docs/schema.md`. Additive
   under the format rule, entered in the schema doc's frame-key register;
   the packed stream passes position lines through untouched, and the
   packer's round-trip test covers the key.
3. Viewer rules (interpretation, never recorded): stop drawing at `dead`;
   remaining time as above; a drone whose `ownerEosId` and team match a
   just-vanished one is the same kit redeployed, if the viewer wants
   continuity.

## Tests (needs are capabilities, not head-counts; state lives in the tracker)

| # | Test | Needs | What it decides | Result (dated) or tracker pointer |
|---|---|---|---|---|
| D1 | Flight time: which value, if any, counts down; the budget's source per class | a pilot; a whole flight to timeout | how `flightTimeRemaining` is derived | 2026-09-05: no countdown; budget from spawn — `BatteryLifetimeMax` 100 s on the recon pawn, the action's active window for the commander drone; the battery runs unpiloted |
| D2 | Health: which field, its scale, whether damage shows before death | a drone that holds still and a shooter — the pilot can land it and shoot it | the `health` fields | 2026-09-05: `HealthComponent_C.Health` / `Max Health`, 15/15; one burst → 0 and `Dead` in the same sample |
| D3 | Recon lifecycle: deploy, fly, pick up, redeploy; time out; re-arm; respawn | a recon-kit player and an ammo source | identity in memory across the cycle | 2026-09-05: pickup destroys the pawn; every redeploy and every re-arm is a new pawn; no launcher deployable exists for the recon drone |
| D4 | Speed: flat out, straight, 30 s at 10 Hz | a pilot | top speed for the 4 Hz argument and interpolation | 2026-09-05: 9–10.6 m/s sustained, best 10 s mean 11.4 m/s; spikes are replication steps |
| D5 | Shoot-down attribution: `LastHitBy` at the kill | a drone and a shooter — the pilot can be the shooter on a landed drone; a second player only for an in-flight shoot-down | whether "shot down by" is recordable | 2026-09-05: `LastHitBy` → the shooter's controller at the kill (self-inflicted case); in-flight by another player optional |
| D6 | Possession hand-off: give the drone to a squad mate and take it back; watch `PlayerState`, `Controller`, `SQ PC` | two players | that `pilotEosId` follows the game and what `SQ PC` does on a hand-off | tracker T7 (the hand-off remainder; a second player) |
| D7 | Team and owner of a pilotless drone | a pilot who lands and exits | whether `team` can be emitted | 2026-09-05: `SQ PC` holds the deployer/last pilot through de-possession and death → `ownerEosId`, team derived |
| D8 | The 4 Hz sample on a live drone: gates admit it, omit it cleanly at death and at the zeroed position | any drone flight with the two-tier recorder running the new code | that the fast tier behaves | tracker W18 then T8 (acceptance of the implementation) |
| D9 | Commander drone confirmations: `SQ PC` persistence, health value, linger, `Command Action` set | a commander calling the drone (rides test T8) | that the commander drone matches the recon findings where the class is shared | tracker T8 (rides the six-player run) |

The harness is `scripts/probes/drone_track.py` (tracker test T7): the
`bomb_track.py` pattern — reflection-resolved fields, change-triggered
dumps, a 1 Hz soldier roll-call — following every `SQFlyingDrone`
subclass, the launcher deployable and the commander drone actor by name,
with the health component's numeric fields dumped on change.
`BatteryLifetimeMax` is not in its field list (read on 2026-09-05 by a
one-off layout read); add it before the next run. Session outputs are
archived in the Misc folder under `command-probe-2026-09-05/` (probe
rows, both class layouts, the inventory probe's output).

## T7 run sheet — the drone bundle (one recon-kit player, ~30 min)

Run 2026-09-05 (Yehorivka RAAS v2, one player, AFU recon kit). Harness:
`scripts/probes/drone_track.py`, canonical in the repo (test T7 in
`docs/tracker.md`; conventions in `scripts/probes/README.md`). On the box
it arrives with `git pull` and runs from the fork checkout as
`sudo .venv/bin/python scripts/probes/drone_track.py` (reading the
server's memory needs root); it appends to `/tmp/drone_track.jsonl`.
Start it after the layer is loaded and the pilot is in; it resolves
every class at attach. Only B4 needs a second player.

| Step | Action | What to say | Decides | 2026-09-05 |
|---|---|---|---|---|
| B1 | Deploy the recon drone; fly it for its whole life until it times out | "deployed", "timed out" | D1 budget and its source; D7 team while flying | done — 97.8 s to `Dead` |
| B2 | Re-arm at an ammo source; deploy again; fly 30 s flat out in a straight line | "re-armed", "flat out", "stopping" | D4 top speed; whether re-arm is a new pawn | done — new pawn; 9–10.6 m/s |
| B3 | Fly briefly, land it, walk to it, pick it up, redeploy | "picked up", "redeployed" | D3 identity across pickup | done — pawn destroyed at pickup, redeploy a new pawn |
| B4 | Hand it to a squad mate and take it back | "handing off", "taking back" | D6 possession chain vs `SQ PC` | waits for a second player |
| B5 | Land it, exit, and shoot it with your rifle until destroyed (or have a second player shoot it down in flight) | "landed", "shooting", "destroyed" | D2 health fields, D5 `LastHitBy` | done solo — 15 → 0 in one sample; `LastHitBy` = the shooter |
| B6 | Deploy one more and leave it unpiloted; observe the death and the linger | "left it" | pawn lifetime and the "gone" semantics | done — died at 97.3 s unpiloted; removed 93 s later after falling and a zeroed final position |

The decode files each answer into the test table above and into the
tracker's T7 row. The commander-drone confirmations (D9) ride test T8's
drone step with this probe running alongside.
