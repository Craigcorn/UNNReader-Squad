# Commander, tactical requests, command assets and drones — capture spec

The contract for what the recorder writes about the commander role, the
squad leaders' tactical requests, the command assets they produce, and
every drone pawn, commander-called or recon-carried. Written 2026-09-05
from the findings journal (`docs/command-assets.md`, three live sessions
2026-08-30 to 09-03 and the decisions of 09-04) and the drone findings
(`docs/drones.md`, five flights 2026-09-05). This document carries no
state: every field is a direct read of a named memory location, every
claim carries the date of the evidence it rests on, and where a field's
*interpretation* still depends on a test, the test is cited by its
tracker id with what it is. Status lives in `docs/tracker.md` (this is
work item W15; the implementation plan is W17, the implementation W18).

## 1. Rules this contract is written under

- **No-guess.** A field is emitted when the read succeeds and omitted
  otherwise; nothing is inferred, defaulted or computed on the recorder.
  Every "ready in", every event ("vote resolved", "commander changed"),
  every rule about how timers combine, is the viewer's to derive from
  per-frame state and can be corrected for every recording at once.
- **Reflection by name.** Every offset in the journal is the value of its
  day and moved twice during the sessions; the implementation resolves
  every field by reflection name and registers each read with the
  doctor (§8). FastArray element sizes come from the inner struct's
  reflected size, never a constant (the 09-02 probe lost a sample to a
  guessed stride).
- **Recording truth, not client truth.** The recorder writes what the
  server holds. Things the server does not hold — the request circle's
  radius, the bomb map circles, a deleted-versus-expired request — are
  not recorded and are drawn by the viewer as documented constants.
- **Format impact.** Every addition below is additive under CLAUDE.md
  "Recordings are immutable": fields on existing records, two new
  top-level lists (`commandActions`, `drones`) and one new position-line
  key (`drones`), each documented in `docs/schema.md` and entered in its
  frame-key register (the `commandActions` and `drones` rows exist there
  as planned rows and take their commit hash at implementation). No
  version number moves. A packer built to the current replay format
  carries the new lists whole, and its round-trip test covers them.
- **Parity.** Nothing here is consumed by the stats engine, but the
  snapshot changes, so `scripts/stats_parity.py` runs against the
  test-box corpus before push, as for every snapshot change.
- **Public repo.** Class and field names are the game's; no player names.

## 2. Conventions shared by every field below

| Convention | Rule |
|---|---|
| Ids | An actor's or pawn's address as a lowercase hex string (`"0x707db0c584a0"`), the same form vehicles use. New on every spawn; never reused within a recording's life except by the game itself. |
| Player identity | `eosId`: the `OnlineUserId` string of the player state reached from the pointer named; `name`: its `PlayerNamePrivate`. A pointer that is null or does not reach a player state yields `null` for both. |
| Class names | The object's class name verbatim, e.g. `BP_CommandActor_SU25_Bomb_Strafe_C`, `CommandAction_Drone_C`. |
| Positions | `{x, y, z}` in world centimetres from the root transform, as vehicles record them. |
| `yaw` | Degrees, world, from the root transform, as vehicles record it. |
| Game time | Seconds on the server's game clock — the same clock as the existing `gameState.worldTimeSec`. Stamps are read raw; nothing is subtracted. |
| Durations | Seconds, as the game stores them (floats). |
| Emission | "every frame" means every full frame; a sub-object is present only in the frames its rule names; a field a class lacks is omitted, never defaulted; a zeroed position (the map origin) is treated as absent, as the origin-parked vehicles are. |

## 3. Surface A — the team record: `teams[].commander`

Evidence: journal §"Commander state" (2026-08-30 to 09-02), §"Cooldowns"
(2026-09-04), the agreed contract of 2026-09-04. The fix to the two
existing identity fields repairs a shipped read that treats the
commander-state actor as a player state and emits nothing (verified
live 2026-08-30).

| Wire field | Memory source | Type | Meaning | Emitted |
|---|---|---|---|---|
| `commanderName`, `commanderEosId` (existing fields) | `SQTeamState.CommanderState` → `SQCommanderState.CurrentCommander` → player state | string | who holds the seat | every frame; explicit `null` when the seat is empty |
| `commander.enabled` | `SQCommanderState.bCommanderIsActive` | bool | the commander system exists on this layer — not "claimed" (read 1 on both teams while one had no commander, 2026-08-30) | every frame |
| `commander.actionsEnabled` | `SQCommanderState.bActionsEnabled` | bool | the team may issue commands this frame (live state; it opened around the moments assets were called and toggled as a commander moved, 09-02 raws) | every frame |
| `commander.vote` | — | object | present while a vote is open and on the frame it ends, so the final tallies land | see fields |
| `commander.vote.inProgress` | `bVoteInProgress` | bool | a commander vote is open | with the vote object |
| `commander.vote.timer` | `CommanderVoteTimer` | int, s | seconds left in the vote window (60 → 0, once per second, 2026-08-31) | with the vote object |
| `commander.vote.startedGameTime` | `CommanderVoteTimestamp` | int, game s | when the vote opened | with the vote object |
| `commander.vote.nominees[]` | `NomineeStatus.Items[].Content` (`CommanderVoteNominee`): `NomineeState` → player state; `VoteCount` | `{eosId, name, votes}` | each nominee and the live tally; no per-voter ballots exist in memory (three votes read at 96-byte width, 09-02) | with the vote object |
| `commander.vote.cooldownActive` | `bVoteCooldownActive` | bool | the block on new votes after a claim | while the cooldown is active |
| `commander.vote.cooldownTimer` | `VoteCooldownTimer` | int, s | its countdown (300 → 0 after a claim, 09-02) | while the cooldown is active |
| `commander.vote.cooldownStartedGameTime` | `VoteCooldownTimestamp` | int, game s | when it started | while the cooldown is active |
| `commander.cooldowns.categories[]` | `CommanderCategories[i]` (`CommanderCategory`): `Name`, `CooldownDuration`; `LastCategoryGameTime[i]` | `{id, name, intervalSec, lastUseGameTime}` | the per-category gate: any call in the category writes the stamp (strafe, mortar and three bomb calls all wrote index 1, 09-02); `lastUseGameTime` is `null` until the first call | every frame |
| `commander.cooldowns.actions[]` | `CommandIntervals.Items[].Content` (`SQCommandActionData`): `CommandActionData`, `GameTimeAtCreation`, `CooldownTimeRemaining`, `IsDestroyedDuringActive`; plus, from the action class's defaults, `CategoryId`, `EnrouteDuration`, `ActiveDuration`, `CooldownDuration` | `{action, createdGameTime, remainingAtChange, destroyedDuringActive, categoryId, enrouteSec, activeSec, cooldownSec}` | one entry per action the team can call; entries appear at the first claim, back-dated by enroute plus active so each asset starts with its own cooldown to run (both claims, 09-02); a call rewrites `createdGameTime`; `remainingAtChange` is written by the game only at a commander change and is otherwise 0; `destroyedDuringActive` read 1 on the drone that was shot down. The four config values ride every frame so a seek into a replay is self-describing | every frame once entries exist |

Element sizes: `CommandIntervals` items are `SQCommandActionDataFASItem`
(40 bytes on 09-04, taken from the reflected struct size, never
assumed); `NomineeStatus` items likewise from their struct.

## 4. Surface B — `gameState.commanderRules`

Evidence: manager config read live 2026-09-04 (60 / 300 / 300 / 2 / 3).

| Wire field | Memory source (`SQCommanderManager`) | Type | Emitted |
|---|---|---|---|
| `commanderRules.enabled` | `bCommanderActive` | bool | every frame |
| `commanderRules.votingTimeSec` | `VotingTimeSeconds` | number | every frame |
| `commanderRules.voteCooldownSec` | `VoteCooldownTimeSeconds` | number | every frame |
| `commanderRules.newCommanderExtensionSec` | `ActionCooldownExtensionOnNewCommander` | number | every frame |
| `commanderRules.minSquadSize` | `MinimumSquadSizeForVoting` | int | every frame |
| `commanderRules.minSquads` | `MinimumSquadsRequiredForVoting` | int | every frame |

Six scalars every frame: simpler and seek-safe versus "once". Two
flags both called "enabled" are deliberate and distinct: the manager's is
the server setting; the team's (`commander.enabled`, §3) is per-team
state.

## 5. Surface C — geometry fields on actor markers: `markers[]`

Evidence: offline decode 2026-08-31, live enumeration of 163 marker
classes 2026-09-04, the agreed contract of 2026-09-04.

| Wire field | Memory source | Type | Meaning | Emitted |
|---|---|---|---|---|
| `distance` | `Distance` | number, raw game units (cm) | the marker's own length figure: circle radius on `CommandRadius` (10000 = 100 m UAV coverage; 7500 = the mortar barrage's fixed footprint), run length on `CommandLine` (6000), path length on `CommandPath` (45000, equal to the creep actor's), aim separation on `CommandLineRadius` (the chosen 44.75–120 m); 0 on request markers | whenever the marker's class carries the field |
| `addDistance` | `AddDistance` | number, raw | the secondary figure: drop scatter on `CommandPath` (7500), the outer danger band on the mortar `CommandRadius` (4500); 0 elsewhere | whenever the class carries the field |
| `yaw` | root transform | degrees | the marker's facing — the run direction, the path bearing, the aim line (the bomb marker's facing matched the aircraft's approach to within a degree, 09-02/03) | whenever the transform reads |

Rules: the fields are read by name on any marker class that carries
them, never by class-name matching — today the Command family
(`BP_MapMarker_CommandMaster_C` and subclasses) carries `Distance` and
`AddDistance`, the Director family (`BP_MapMarker_DirectorMaster_C` and
subclasses) carries `Distance`, and no other actor marker carries
either. The existing `arrowLength` / `arrowHeading` fields are not
reused: they describe a dragged arrow on the squad-data markers. The
squad-data marker and the team actor marker are two markers to the game
(what the placing squad sees, and what other squad leaders see,
player-confirmed 2026-09-04); the recorder records both and never
merges; drawing one shape is the viewer's rule (§9).

Nothing new is recorded about request markers themselves: `type`
(the class name is the only pending/approved discriminator — the two
classes have byte-identical layouts and the `Request` bool reads 1 on
both), position, `team`, `squad` and `ownerPlayerStateAddr` already
reach the file.

## 6. Surface D — the `commandActions` list

One entry per live `BP_CommandActor_*` actor, every full frame, present
only while such an actor exists (a handful per match, 30 s to 10 min
each). Evidence: journal §"Per-call actors" (2026-08-30 to 09-03), the
agreed contract of 2026-09-04.

Common fields, every actor:

| Wire field | Memory source | Type | Meaning |
|---|---|---|---|
| `id` | actor address | id | this call's actor |
| `class` | class name | string | e.g. `BP_CommandActor_Artillery_Creep_C` |
| `team` | `Team` | int | owning team |
| `action` | `Action` (class) | string | the `CommandAction_*` config class — joins `commander.cooldowns.actions[].action` |
| `callerEosId` | `DamageInstigatorController` → controller → player state | string | the commander who called it: the attribution pointer the game itself uses for the asset's kills (a strafe wound event named the commander as attacker, 09-02) |
| `position`, `yaw` | root transform | position, degrees | where the actor is this frame — aircraft move along their run, artillery sits at its origin |
| `actionDestroyed` | `Action Destroyed` | bool | the call has ended (the actor lingers for its configured delay) |
| `distance` | `Distance` | number, raw | the actor's own length figure (the creep read exactly 45000, its marker's path length) |

Family fields, emitted where the class has them (Blueprint variable
names contain spaces and are used verbatim):

| Family | Wire fields | Memory source |
|---|---|---|
| Strike aircraft (`*_Strafe_*`, gun and bomb): a shootable pawn flying its run | `health`, `dead`, `shotsMade`, `maxShots`, `splineDistance`, `originLocation` | `Health`, `Dead_0`, `CurrentShotsMade`, `MaxShots`, `Spline Distance`, `Origin Location` |
| Artillery creep and barrage, mortar barrage: the fire plan and its progress | `originLocation`, `targetLocation`, `maxDropRadius`, `preWarningShells`, `shellsPerBarrage`, `barrageCount`, `currentBarrage`, `projectile` | `OriginLocation`, `target location`, `MaxDropRadius`, `PreWarningShells`, `ShellsPerBarrage`, `BarrageCount`, `CurrentBarrage`, `Projectile` (class name) |
| UAV | `health` where present | `Health`; the UAV actor's layout has not been read live — the common fields apply, family fields join when the class is reflected |
| Commander drone call actor (`BP_CommandActor_Drone_C`) | `health`, `pilotEosId` | `Health`, `SQ PC` → player state |

Not recorded: who damaged or destroyed an actor. No last-damager field
exists in the reflected list of any command actor (09-02); the drone
pawn's `LastHitBy` is recorded in §7. Tracker T9 is the observation that
would add an aircraft attribution read, if one exists.

The shells, rockets and bombs an asset fires are already tracked
projectiles with `firer` = the commander (bombs 09-02/03; 155 mm shells
at 97 % impact capture in a production recording), so the actor record
adds the plan and its progress, never the impacts.

## 7. Surface E — the `drones` list and the position-line `drones` key

One entry per live pawn whose class derives from `SQFlyingDrone`
(`BP_FlyingDrone_C` for the commander's, `BP_FlyingDrone_Recoverable_C`
for the recon kit's, any future variant), every full frame, present
only while a pawn exists and omitted once its position is zeroed or the
pawn is gone. Evidence: `docs/drones.md` — one commander flight
2026-09-02, five recon flights 2026-09-05.

| Wire field | Memory source | Type | Meaning |
|---|---|---|---|
| `id` | pawn address | id | new on every deploy — picking a drone up destroys the pawn, and every redeploy or re-arm is a fresh one (09-05) |
| `class` | class name | string | which drone |
| `position`, `yaw` | root transform | position, degrees | where it is |
| `dead` | `Dead` | bool | true from the moment the battery expires or it is destroyed |
| `health`, `maxHealth` | `HealthComponent` → `Health`, `Max Health` | number | 15 / 15 on the recon drone; one rifle burst takes it to 0 (09-05) |
| `pilotEosId` | `PlayerState` → player state | string | who is flying it this frame; `null` while nobody is (landed, exited, or before first possession) |
| `ownerEosId` | `SQ PC` → its player state | string | the deployer or last pilot; observed to persist through de-possession and death (09-05). Team derives from it. What a hand-off does to it is tracker T10 |
| `commandAction` | `Command Action` (class) | string | the calling action on a commander drone; `null` on a recon drone (every recon row, 09-05) |
| `batteryLifetimeMax` | `BatteryLifetimeMax` | number, s | the flight budget from spawn (100 on the recon class; the field does not exist on the commander drone's class, whose budget is its action's `activeSec` in §3) |
| `lastHitByEosId` | `LastHitBy` → controller → player state | string | who last hit it; resolved to the shooter at the kill (09-05) |

No remaining-time field exists in memory (`EndFlightTimer` is a timer
handle; `BleedOutTime` is a constant 30 that is neither the flight time
nor the linger) and none is recorded.

**Position line** (`{"t": "pos"}`, 4 Hz): a `drones` array joins
`players` and `vehicles`, entries `{id, x, y, z, yaw}` with the same `id`
as the full-frame entry, under the same freshness gates (class pointer
intact, sane coordinates; a zeroed position or a freed pawn is omitted).
Measured cost ~115 B of raw JSON per drone per sample, ~28 KB on disk
per ten-minute flight, three to four small reads per drone per sample.
Touch points: `possample.SampledEntities` / `sample_positions`, the
position-frame key, the viewer's reconstructor, the schema register.

## 8. Doctor coverage

Every read above is reflection-resolved, so each class gains a
`required_reflection_names()` row (type, meta-class, optional?,
[property names]); no hardcoded offset is introduced, so
`hardcoded_offset_tables()` gains nothing. Rows marked optional carry
the observed reason the register demands: Blueprint content classes
load with a layer, a claim or a call, and their absence on an idle
server is not drift.

| Type | Meta-class | Optional | Properties |
|---|---|---|---|
| `SQTeamState` | Class | no | `CommanderState` (existing row) |
| `SQCommanderState` | Class | no | `CurrentCommander` (existing), `bCommanderIsActive`, `bActionsEnabled`, `bVoteInProgress`, `CommanderVoteTimer`, `CommanderVoteTimestamp`, `bVoteCooldownActive`, `VoteCooldownTimer`, `VoteCooldownTimestamp`, `CommanderCategories`, `LastCategoryGameTime`, `CommandIntervals`, `NomineeStatus` |
| `SQCommanderManager` | Class | no | `bCommanderActive`, `VotingTimeSeconds`, `VoteCooldownTimeSeconds`, `ActionCooldownExtensionOnNewCommander`, `MinimumSquadSizeForVoting`, `MinimumSquadsRequiredForVoting` |
| `SQCommandActionData` | ScriptStruct | no | `CommandActionData`, `GameTimeAtCreation`, `CooldownTimeRemaining`, `IsDestroyedDuringActive` |
| `SQCommandActionDataFASItem` | ScriptStruct | no | `Content` |
| `CommanderVoteNominee` | ScriptStruct | no | `NomineeState`, `VoteCount` |
| `CommanderCategory` | ScriptStruct | no | `Name`, `CooldownDuration` |
| the `CommandAction_*` classes' common base (its name taken from reflection at implementation) | Class | no | `CategoryId`, `EnrouteDuration`, `ActiveDuration`, `CooldownDuration` |
| `SQFlyingDrone` | Class | no | `PlayerState`, `Controller`, `LastHitBy` (present on an idle server, 09-05) |
| `BP_FlyingDrone_C` | Class | yes — content, loads with a layer that has it | `SQ PC`, `HealthComponent`, `Dead`, `Command Action` |
| `BP_FlyingDrone_Recoverable_C` | Class | yes — content | `BatteryLifetimeMax` |
| `HealthComponent_C` | Class | yes — content | `Health`, `Max Health` |
| `BP_MapMarker_CommandMaster_C` | Class | yes — content, loaded idle on 09-04 but unproven on every layer | `Distance`, `AddDistance` |
| `BP_MapMarker_DirectorMaster_C` | Class | yes — content | `Distance` |
| `BP_CommandActor_*` families | Class | yes — content, exist only during a call | the common and family properties of §6, as the implementation names them |

## 9. Viewer rules (interpretation; nothing here is recorded)

Each rule names the fields it reads. Where a rule rests on a test that
has not yet been run, the test is cited by tracker id; the fields do not
change when it runs.

- **Request circle.** 50 m around an approved request, a documented
  game constant (edge-stands on two maps, 49.95 m and 50.20 m,
  2026-08-31); absent from server memory.
- **Pending versus approved.** By marker `type`: `Command_SLRequest` is
  pending, `Command_Request` approved. Bridge the one-tick class swap
  (same squad, same position) as one request changing state. A request
  a squad leader deletes lives out its fuse on the server; never infer a
  delete.
- **Asset shapes.** From `type`, `distance`, `addDistance`, `yaw`:
  `CommandRadius` a circle of radius `distance`; `CommandLine` a run of
  `distance` along `yaw`; `CommandPath` a path of `distance` along `yaw`
  with a scatter band of `addDistance`; the mortar `CommandRadius` a
  circle plus an outer band; `CommandLineRadius` two aim points, at 0
  and `distance` along `yaw`.
- **Precision bombs.** A dashed circle pair at each aim point; impacts
  from the projectile rest positions where `hasImpacted` set. The pair's
  radii are the bomb config's 45 m and 100 m, assumed to be what the
  in-game map draws (the ratio matches, the absolute values were never
  measured — tracker W20's item B2). Every observed bomb fell inside the
  first pair (five calls, 09-02/03).
- **Director markers.** When a frame carries a squad-data marker and an
  actor marker of the same family, owner and position, draw one shape.
- **Commander seat and votes.** Derive "vote opened / resolved / won",
  "commander changed / stepped down" by comparing frames of §3; a vote
  spans 60 s and the seat is a per-frame field, so nothing falls between
  frames. A step-down clears the seat with no vote cooldown; a team
  switch behaves as a step-down (09-02).
- **Ready-in arithmetic.** Per action: effective duration = `enrouteSec`
  + `activeSec` + `cooldownSec`; ready = `createdGameTime` + effective.
  Per category: ready = `lastUseGameTime` + `intervalSec`. An asset is
  callable at the later of the two, less the frame's `worldTimeSec`. When
  `destroyedDuringActive` flips true, ready = that frame's
  `worldTimeSec` + `cooldownSec` (the 09-02 drone; player-confirmed).
  After a commander change the game re-stamps entries so that
  `newCommanderExtensionSec` remain (one sample, a long-ready asset,
  09-02); what it does to an asset still cooling is tracker T9. Three
  sources agree on the category gate — memory arithmetic, the players'
  rule, SquadCalc's model — and the direct call test is tracker T9's
  optional confirmation.
- **Actions enabled.** `commander.actionsEnabled` is displayed as read;
  its reading as "the commander stands in a command zone" is an
  inference the viewer may label as such (tracker W20's item R8).
- **Drones.** Stop drawing at `dead`. Remaining flight time = the
  `worldTimeSec` of the first frame the `id` appears + `batteryLifetimeMax`
  (recon) or the calling action's `activeSec` (commander) − now. A new
  `id` whose `ownerEosId` and team match one that just vanished is the
  same kit redeployed, if continuity is wanted; the recorder never joins
  them. Interpolate at 4 Hz knowing cruise is ~10 m/s (2.5 m per sample).
- **Asset display names.** SquadCalc's per-asset table maps the
  `CommandAction_*` class names to display names and agrees with the
  config values (cross-checked 2026-09-04).

## 10. Deliberately not recorded

`bDoubleCaptureSpeed` and `bCommandActionAttempted` (never left 0 across
21,000 rows); any "ready in" or remaining-time number; any event line;
any rule; the request circle radius; the bomb map circles; any
last-damager for a command actor (none exists in memory); the drone's
`Max Fly Height`, `BleedOutTime`, `EndFlightTimer`, zoom and FPV state;
the recon launcher (no deployable exists — the launcher is the kit item
in the soldier's inventory).

## 11. Acceptance

Tracker T8: a six-player run with the command-assets probe as oracle,
after W18 lands, comparing recorded values against the probe at the same
instants, the doctor clean with the rows of §8, the parity harness
green, and the viewer drawing the shapes, actors and drones of §9. T8
also carries the commander-drone confirmations of the fields in §7.

## 12. How to check this document against the journal

For the fresh-session comparison (tracker W15), each check is a
question with a yes/no answer:

1. Does every row of the journal's four "Agreed capture" sections
   (decisions 2, 3, 5, 6) and of the drones doc's "Proposed capture"
   appear here with the same memory source and the same meaning?
2. Does every field here trace to a dated, verified statement in the
   journal or the drones doc — nothing introduced without evidence?
3. Where the journal disagrees with itself, does this document take the
   later statement? The known disagreements: cooldown "anchored to the
   call" versus restarting from destruction (later: destruction, 09-04);
   action config values "once per entry" versus every frame (later:
   every frame, decision 2); "the one new top-level list" versus two
   lists (later: two, decision 7); the format rule as it stood versus its
   reword (later: the reword, 09-04); a "reopened" category gate that was
   re-closed the same day; a section headed "not yet agreed" whose items
   were agreed; a commander-state table older than the field names read on 09-02; two
   "enabled" flags left unlabelled (§4 labels them).
4. Is every memory name here present in the archived layouts
   (`Misc/command-probe-2026-09-0*/` on Craig's machine)?
5. Is anything here computed, inferred or defaulted on the recorder
   side? There should be nothing.
6. Does this document carry any state word — open, pending, outstanding,
   decoded, done? There should be none; unresolved interpretation is
   cited by tracker id.
