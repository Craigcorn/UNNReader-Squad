# Drones — capture findings and test plan

Status: **proposal agreed in principle 2026-09-04 (decision 7 of the
commander plan); capture tests outstanding, see the table** · findings
from the 2026-09-02 commander session (one commander drone flight, shot
down) and the 2026-09-03 session (recon drone class observed loading).
Companion to `docs/command-assets.md`, which owns the commander side;
this document owns the drone pawn, whoever launched it. Player names are
deliberately absent.

## What a drone is to the recorder

Two in-game things share one class:

- **The commander's drone**: called as a command asset
  (`CommandAction_Drone_C`, category 0, 10 s enroute, 600 s active, 600 s
  cooldown), spawned through `BP_CommandActor_Drone_C` plus a transient
  `BP_Deployable_DroneSpawner_C`, flown as `BP_FlyingDrone_C`. Observation
  only — it cannot harm players.
- **The recon kit's drone**: some factions' recon class carries a
  deployable drone, flown as `BP_FlyingDrone_Recoverable_C`. Shorter
  flight; can be picked up and redeployed; after destruction or timeout
  it re-arms and respawns. Not tied to the commander role
  (player-confirmed 2026-09-04).

Both pawns derive from `SQFlyingDrone`, itself a `Character` — a
soldier-shaped object, not an `SQVehicle`. That is why no recording has
ever seen a drone: soldiers reach the file through their player, vehicles
through the vehicle list, and the drone is neither. One capture path
keyed on `SQFlyingDrone` covers both and any future variant.

## Verified in memory (2026-09-02 flight, `BP_FlyingDrone_C`)

- Position and yaw through the normal root transform (flight tracked
  live; one cruising sample at ~10 m/s — the player reports drones fly
  faster than that; no top speed has been measured).
- Possession chain: `PlayerState` (+0x2d8), `Controller` (+0x2e8),
  `PreviousController` (+0x2f0) — all three null while de-possessed,
  restored on re-possession. Possession is logged (`OnPossess` lines with
  full player ids); nothing else about a drone is logged, including its
  death.
- Death: the pawn despawns (74 s after the commander state's
  `IsDestroyedDuringActive` flipped, in the one observed case); the
  commander's cooldown restarts from the destruction (see the commander
  doc). Nobody has yet read who destroyed it (open item A1 there).

## Reflected but never read in flight (verify before emitting)

From the `BP_FlyingDrone_C` layout, past the Character boilerplate:
`HealthComponent` (object — its fields need reflecting), `SQ PC` (the
possessing SQ player controller), `Dead` (bool), `Can Possess`,
`Command Action` (class — the calling action; expected null on a recon
drone), `Max Fly Height`, `Can Increase Altitude`, `BleedOutTime`
(double — plausibly flight time remaining, unverified), `EndFlightTimer`
(timer handle), `CrashVelocity`, `CollisionDamageFactor`, `FPV Item` /
`FPV Item Class`, zoom state, `NuisanceTarget`, `LastHitBy`.

The recon launcher `BP_Deployable_DroneSpawner_C` is an `SQDeployable`
(Health, Team, `InstigatingPlayerState`, `Drone Class`, `Action`,
`FPV Item Class`) and should already reach the deployables stream — to
be confirmed in implementation.

## Proposed capture (decision 7, agreed in principle 2026-09-04)

1. **Full frame**: a `drones` list, one entry per live `SQFlyingDrone`
   pawn of any class — `id`, `class`, `position`, `yaw`, `pilotEosId`
   (possession chain; honest gaps when handed off), `dead`,
   `commandAction` (class name; null when none), and — once verified —
   `health` and `flightTimeRemaining`. Emitted only while a pawn exists.
2. **4 Hz position frames**: drones join players and vehicles as a third
   sampled set, position and yaw only, with the same freshness gates
   (class pointer, sane coordinates; a freed pawn is omitted, never
   guessed). Measured cost: ~115 B of raw JSON per drone per sample,
   ~28 KB on disk per 10-minute flight, 3–4 small reads per drone per
   sample. Touch points: `possample.SampledEntities` / `sample_positions`,
   the position-frame `drones` key, the viewer reconstructor,
   `docs/schema.md`. Additive under the format rule, entered in the schema doc's frame-key register; the packed stream passes position lines through untouched, and the packer's round-trip test covers the key.
3. Attribution of a shoot-down (`LastHitBy`) is added when observed
   working (A1 in the commander doc).

## Tests outstanding (one list, importance-ranked)

| # | Test | Needs | What it decides | Importance |
|---|---|---|---|---|
| D1 | Read `BleedOutTime` and `EndFlightTimer` through a whole flight, commander and recon | one recon-kit player (solo) + one commander flight when a commander session happens | whether either is flight time remaining; the meaning and units of `flightTimeRemaining` before it is emitted | high — the field the viewer most wants and the one we know least |
| D2 | Reflect `HealthComponent` and watch it while the drone takes fire | one recon drone + one shooter | which field is health, its scale, whether damage shows before death | high |
| D3 | Recon lifecycle: deploy, fly, pick up, redeploy; let one time out; re-arm at an ammo source; respawn | one recon-kit player, solo | how pickup/redeploy look in memory (same pawn or a new one), what the launcher deployable does across the cycle, whether `Command Action` is null | high |
| D4 | Speed: fly flat out in a straight line for 30 s while tracked at 10 Hz | one player | the real top speed, which sizes the 4 Hz argument and the viewer's interpolation | medium |
| D5 | Shoot-down attribution: `LastHitBy` on the pawn at the kill (A1) | one drone + one shooter | whether "shot down by" can be recorded | medium |
| D6 | Possession gaps: hand the drone off, take it back; watch `PlayerState`, `Controller`, `SQ PC` | two players (or commander + squad mate) | that `pilotEosId` follows the game and that `SQ PC` agrees with `Controller` | medium |
| D7 | Team of a pilotless drone: what carries team while de-possessed (`SQCoreState`? the launcher?) | one player | whether `team` can be emitted, or only derived from the last pilot | medium |
| D8 | The 4 Hz sample on a live drone: confirm the position gates admit it and omit it cleanly at death | any drone flight with the two-tier recorder running | that the fast tier behaves | high (implementation acceptance) |

Most of these need one recon-kit player on the test box and no
commander; D1's commander half and D6 ride along with the next commander
session. The 10 Hz tracker built for the bombing test (the
`bomb_track.py` pattern: reflection-resolved fields, change-triggered
dumps, a 1 Hz roll-call) is the harness — add the `SQFlyingDrone`
subclasses at dump priority 0 and read the fields above by name.

## Session B — run sheet (one recon-kit player, ~30 min, BEFORE implementation)

Tracker: `drone_track.py` (scratchpad; copy to the box, run as root under
the fork's venv). It follows every `SQFlyingDrone` pawn, the launcher
deployable and the commander drone actor at 10 Hz, reflects every field by
name and dumps the health component's numeric fields, so nothing below
needs an offset.

| Step | Action | What to say | Decides |
|---|---|---|---|
| B1 | Deploy the recon drone; fly it for its whole life until it times out | "deployed", "timed out" | D1 flight-time field (which value counts down, in what units); D7 team while flying |
| B2 | Re-arm at an ammo source; deploy again; fly 30 s flat out in a straight line | "re-armed", "flat out", "stopping" | D4 top speed; whether re-arm/respawn is a new pawn |
| B3 | Land it, walk to it, pick it up, redeploy | "picked up", "redeployed" | D3 pick-up/redeploy in memory (same pawn or new), launcher deployable across the cycle |
| B4 | Hand it to a squad mate and take it back (if a second player is present) | "handing off", "taking back" | D6 possession chain vs `SQ PC` |
| B5 | Have a second player shoot it down (if present) | "shooting", "dead" | D2 health fields, D5 `LastHitBy` |
| B6 | Deploy one more and leave it; observe how long the pawn lingers after timeout / death | — | pawn lifetime for the recorder's "gone" semantics |

Check afterwards: the tracker's rows against the recorder's `drones`
entries once implemented (D8), and the speed profile from B2.

