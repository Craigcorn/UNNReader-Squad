# Commander, tactical requests, command assets and drones — capture spec

The contract for what the recorder writes about the commander role, the
squad leaders' tactical requests, the command assets they produce, and
every drone pawn, commander-called or recon-carried. Written 2026-09-05
from the findings journal (`docs/command-assets.md`, three live sessions
2026-08-30 to 09-03 and the decisions of 09-04) and the drone findings
(`docs/drones.md`, five flights 2026-09-05), and corrected the same day
against a fresh-session review (§12). This document carries no state:
every field is a direct read of a named memory location, every claim
carries the date of the evidence it rests on, and where a field's
*interpretation* depends on a test, the test is cited by its tracker id
with what it is. Status lives in `docs/tracker.md` (this is work item
W15; the implementation plan is W17, the implementation W18).

## 1. Rules this contract is written under

- **No-guess.** A field is emitted when the read succeeds and omitted
  otherwise; nothing is inferred, defaulted or computed on the recorder.
  Every "ready in", every event ("vote resolved", "commander changed"),
  every rule about how timers combine, is the viewer's to derive from
  per-frame state and can be corrected for every recording at once.
  Three narrow things the recorder does that are not raw reads, named
  here so nothing else is: it follows pointers (a player state's id and
  name, a class's name, an action entry's config values from that
  class's defaults); it produces one key of its own, the category index
  `i` (§3); and it applies the existing sanity exclusion to positions
  (§2, "Positions").
- **Reflection by name.** Every offset in the journal is the value of its
  day and moved twice during the sessions; the implementation resolves
  every field by reflection name and registers each read with the
  doctor (§8). FastArray element sizes come from the inner struct's
  reflected size, never a constant (the 09-02 probe lost a sample to a
  guessed stride). Every name is attempted on the object at hand; the
  family columns in §6 and §7 document which classes carry which, they
  are never a class-name test.
- **Recording truth, not client truth.** The recorder writes what the
  server holds. Things the server does not hold — the request circle's
  radius, the bomb map circles, a deleted-versus-expired request — are
  not recorded and are drawn by the viewer as documented constants.
- **Format impact.** Every addition below is additive under CLAUDE.md
  "Recordings are immutable": fields on existing records, two new
  top-level lists (`commandActions`, `drones`) and one new position-line
  key (`drones`), each documented in `docs/schema.md` and entered in its
  frame-key register. No version number moves. A packer built to the
  current replay format carries the new lists whole, and its round-trip
  test covers them.
- **Stats.** The stats engine consumes none of these fields today. If
  commander statistics are ever built (tracker W33, the engine additions
  row), the engine derives them from this per-frame state exactly as the
  viewer does, and the parity gate applies in full. The snapshot changes
  either way, so `scripts/stats_parity.py` runs against the test-box
  corpus before push.
- **Public repo.** Class and field names are the game's; no player names.

## 2. Conventions shared by every field below

| Convention | Rule |
|---|---|
| Ids | An actor's or pawn's address as a lowercase hex string (`"0x707db0c584a0"`), the same form vehicles use. New on every spawn; never reused within a recording's life except by the game itself. |
| Player identity | `eosId`: the `OnlineUserId` string of the player state reached from the pointer named; `name`: its `PlayerNamePrivate` — the reader's existing identity read. A pointer that reads null gives `null` for both; one that does not reach a player state gives an omitted field. |
| Absent reads | Two cases, kept distinct because they mean different things to a viewer. **`null`** = the game's own value is empty and was read successfully: a pointer that reads null (no commander in the seat, no pilot in the drone, no calling action on a recon drone), an array with no element at the index (a category never called). **Omitted** = the recorder could not read: the class lacks the field, the pointer reaches an object that is not what the field names, or the read fails. So a viewer that sees `null` knows "none", and a viewer that sees nothing knows "unknown". This is the journal's contract for the identity fields ("explicit `null` when the seat is empty") applied to every field. |
| Class names | The object's class name verbatim, e.g. `BP_CommandActor_SU25_Bomb_Strafe_C`, `CommandAction_Drone_C`. A class pointer that reads null gives `null`. |
| Positions | `{x, y, z}` in world centimetres from the root transform, as vehicles record them. The existing sanity exclusion applies unchanged: a `commandActions` or `drones` entry whose root position reads exactly (0, 0, 0) is dropped from the list (the junk-vehicle test; a dead drone's final tick reads (0, 0, 0) before the pawn is freed, 09-05), and the position line applies the sampler's existing finite-and-in-bounds gate. |
| `yaw` | Degrees, world, from the root transform, as vehicles record it. |
| Game time | Seconds on the server's game clock — the same clock as the existing `gameState.worldTimeSec`. Stamps are read raw; nothing is subtracted. |
| Numbers | Types follow the reflected property: `int` for IntProperty and ByteProperty, `number` for FloatProperty and DoubleProperty, `bool` for BoolProperty. An FText (`TextProperty`) is read with the reader's existing FText helper. |
| Emission | "every frame" means every full frame; a field a class lacks is omitted, never defaulted. |

## 3. Surface A — the team record: `teams[].commander`

Evidence: journal §"Commander state" (08-30/31 and 09-02 sessions),
§"Cooldowns" (2026-09-04), the agreed contract of 2026-09-04; the four
inner structs reflected live 2026-09-05 (archived: Misc
`command-probe-2026-09-05/struct_layouts_0905.txt`). The fix to the two
existing identity fields repairs a shipped read that treats the
commander-state actor as a player state and emits nothing (verified
live in the 08-30/31 sessions).

| Wire field | Memory source | Type | Meaning | Emitted |
|---|---|---|---|---|
| `commanderName`, `commanderEosId` (existing fields) | `SQTeamState.CommanderState` → `SQCommanderState.CurrentCommander` → player state | string | who holds the seat; explicit `null` when `CurrentCommander` reads null — the seat is empty | every frame |
| `commander.enabled` | `SQCommanderState.bCommanderIsActive` | bool | the commander system exists on this layer — not "claimed" (read 1 on both teams while one had no commander, 08-30/31) | every frame |
| `commander.actionsEnabled` | `SQCommanderState.bActionsEnabled` | bool | the team may issue commands this frame (live state; it opened around the moments assets were called and toggled as a commander moved, 09-02 raws) | every frame |
| `commander.vote.inProgress` | `bVoteInProgress` | bool | a commander vote is open | every frame |
| `commander.vote.timer` | `CommanderVoteTimer` | int, s | seconds left in the vote window (60 → 0, once per second, 2026-08-31); reads 0 when no vote is open | every frame |
| `commander.vote.startedGameTime` | `CommanderVoteTimestamp` | int, game s | when the current or last vote opened | every frame |
| `commander.vote.nominees[]` | `NomineeStatus.Items[]` (`CommanderVoteNominee`, 32-byte items): `NomineeState` → player state; `VoteCount` | `{eosId, name, votes}` | each nominee and the live tally; no per-voter ballots exist in memory (three votes read at 96-byte width, 09-02). Entries persist after resolution, so the frame after `inProgress` drops still carries the final tallies — the recorder keeps no memory across frames | whenever the array holds entries |
| `commander.vote.cooldownActive` | `bVoteCooldownActive` | bool | the block on new votes after a claim | every frame |
| `commander.vote.cooldownTimer` | `VoteCooldownTimer` | int, s | its countdown (counts down after every claim, 09-02; the manager's `VoteCooldownTimeSeconds` read 300, 09-04) | every frame |
| `commander.vote.cooldownStartedGameTime` | `VoteCooldownTimestamp` | int, game s | when it started | every frame |
| `commander.cooldowns.categories[]` | `CommanderCategories[i]` (`CommanderCategory`, 24-byte items): `Name` (FText), `CooldownDuration` (float); `LastCategoryGameTime[i]` (float) | `{id, name, intervalSec, lastUseGameTime}` | the per-category gate: any call in the category writes the stamp (strafe, mortar and three bomb calls all wrote index 1, 09-02). `id` is the array index `i` — the index `LastCategoryGameTime` uses and the value the actions' `categoryId` carries — the one key the recorder produces. `lastUseGameTime` is `null` while `LastCategoryGameTime` has no element at `i` (the array is empty until the first call) | every frame |
| `commander.cooldowns.actions[]` | `CommandIntervals.Items[]` (`SQCommandActionDataFASItem`, 40-byte items) `.Content` (`SQCommandActionData`): `CommandActionData` (class), `GameTimeAtCreation` (float), `CooldownTimeRemaining` (float), `IsDestroyedDuringActive` (bool); plus, from that class's defaults, `CategoryId` (byte), `EnrouteDuration`, `ActiveDuration`, `CooldownDuration` (floats) | `{action, createdGameTime, remainingAtChange, destroyedDuringActive, categoryId, enrouteSec, activeSec, cooldownSec}` | one entry per action the team can call; entries appear at the first claim, back-dated by enroute plus active so each asset starts with its own cooldown to run (both claims, 09-02); a call rewrites `createdGameTime`; `remainingAtChange` is the raw read — the game writes it only at a commander change and it reads 0 otherwise; `destroyedDuringActive` read 1 on the drone that was shot down. The four config values ride every frame so a seek into a replay is self-describing | every frame once entries exist |

## 4. Surface B — `gameState.commanderRules`

Evidence: manager config read live 2026-09-04 (60 / 300 / 300 / 2 / 3);
the manager's layout archived 09-02.

| Wire field | Memory source (`SQCommanderManager`) | Type | Emitted |
|---|---|---|---|
| `commanderRules.enabled` | `bCommanderActive` | bool | every frame |
| `commanderRules.votingTimeSec` | `VotingTimeSeconds` | int | every frame |
| `commanderRules.voteCooldownSec` | `VoteCooldownTimeSeconds` | int | every frame |
| `commanderRules.newCommanderExtensionSec` | `ActionCooldownExtensionOnNewCommander` | number | every frame |
| `commanderRules.minSquadSize` | `MinimumSquadSizeForVoting` | int | every frame |
| `commanderRules.minSquads` | `MinimumSquadsRequiredForVoting` | int | every frame |

Six scalars every frame: simpler and seek-safe versus "once". The
per-team `SQCommanderState` carries copies of `VotingTimeSeconds`,
`VoteCooldownTimeSeconds`, `MinimumSquadSizeForVoting` and
`MinimumSquadsRequiredForVoting` (09-02 layout); the manager's are the
ones read, once, not per team. Two flags both called "enabled" are
deliberate and distinct: the manager's is the server setting; the
team's (`commander.enabled`, §3) is per-team state.

## 5. Surface C — geometry fields on actor markers: `markers[]`

Evidence: offline decode 2026-08-31, live enumeration of 163 marker
classes 2026-09-04, the agreed contract of 2026-09-04, the two master
classes reflected live 2026-09-05 (`BP_MapMarker_CommandMaster_C`:
`Distance` double, `Action` class, `Request` bool, `AddDistance` double;
`BP_MapMarker_DirectorMaster_C`: `Distance` double); the geometry
subclasses `CommandPath`, `CommandLine` and `CommandRadius_Friendly`
carry the master's same four fields at the same offsets (08-30 layouts).

| Wire field | Memory source | Type | Meaning | Emitted |
|---|---|---|---|---|
| `distance` | `Distance` | number, raw game units (cm) | the marker's own length figure: circle radius on `CommandRadius` (10000 = 100 m UAV coverage; 7500 = the mortar barrage's fixed footprint), run length on `CommandLine` (6000), path length on `CommandPath` (45000, equal to the creep actor's), aim separation on `CommandLineRadius` (the chosen 44.75–120 m); 0 on request markers | whenever the marker's class carries the field |
| `addDistance` | `AddDistance` | number, raw | the secondary figure: drop scatter on `CommandPath` (7500), the outer danger band on the mortar `CommandRadius` (4500); 0 elsewhere | whenever the class carries the field |
| `yaw` | root transform | degrees | the marker's facing — the run direction, the path bearing, the aim line (the bomb marker's facing matched the aircraft's approach to within a degree, 09-02/03) | whenever `distance` is emitted, i.e. on the classes that carry `Distance` |

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
reach the file. The Command master's `Action` class pointer, a direct
join from a marker to the action it belongs to, is not recorded; it is
tracker D13.

## 6. Surface D — the `commandActions` list

One entry per live `BP_CommandActor_*` actor, every full frame, present
only while such an actor exists (a handful per match, 30 s to 10 min
each). Evidence: journal §"Per-call actors" (2026-08-30 to 09-03), the
agreed contract of 2026-09-04, six actor layouts archived (creep, UAV,
F/A-18 on 08-30; drone, F/A-18, SU-25 bomb, mortar on 09-02).

Common fields, every actor:

| Wire field | Memory source | Type | Meaning |
|---|---|---|---|
| `id` | actor address | id | this call's actor |
| `class` | class name | string | e.g. `BP_CommandActor_Artillery_Creep_C` |
| `team` | `Team` | int | owning team |
| `action` | `Action` (class) | string | the `CommandAction_*` config class — joins `commander.cooldowns.actions[].action` |
| `callerEosId` | `DamageInstigatorController` (a weak object pointer, resolved through the object array as the reader's existing weak-pointer read does) → controller → player state | string | the commander who called it: the attribution pointer the game itself uses for the asset's kills (a strafe wound event named the commander as attacker, 09-02) |
| `position`, `yaw` | root transform | position, degrees | where the actor is this frame — aircraft move along their run, artillery sits at its origin |
| `actionDestroyed` | `Action Destroyed` | bool | the call has ended (the actor lingers for `Destroy Delay after Action Destroyed`) |
| `distance` | `Distance` | number, raw | the actor's own length figure (the creep read exactly 45000, its marker's path length) |

Family fields, emitted where the class has them. Every name below is
attempted on every command actor; the family column says which classes
carried which on the days they were read. Blueprint variable names
contain spaces and are used verbatim.

| Family | Wire fields (type) | Memory source |
|---|---|---|
| Strike aircraft (`*_Strafe_*`, gun and bomb): a shootable pawn flying its run | `health` (number), `dead` (bool), `shotsMade` (int), `maxShots` (int), `splineDistance` (number), `originLocation` (`{x, y, z}`) | `Health`, `Dead_0`, `CurrentShotsMade`, `MaxShots`, `Spline Distance`, `Origin Location` (09-02 layouts) |
| Artillery creep and barrage, mortar barrage: the fire plan and its progress | `originLocation` (`{x, y, z}`), `targetLocation` (`{x, y, z}`), `maxDropRadius` (number, cm), `preWarningShells` (int), `shellsPerBarrage` (int), `barrageCount` (int), `currentBarrage` (int), `projectile` (class name) | `Origin Location`, `target location`, `Max Drop Radius`, `Pre Warning Shells`, `Shells Per Barrage`, `Barrage Count`, `Current Barrage`, `Projectile` — the spellings reflected on both `BP_CommandActor_Artillery_Creep_C` (08-30 layout) and `BP_CommandActor_Mortar_Radius_C` (09-02 layout), identical names at identical offsets. The journal's creep entry dropped the spaces when it was transcribed; the layouts never did |
| UAV (`BP_CommandActor_UAV_MQ9_C`): position is the point; a shootable actor | `health` (number), `dead` (bool) | `Health`, `Dead_0` (08-30 layout; the strike family's pair, the layout also carrying `HealthComponent`, `Min Flight Speed`, `Max Flight Speed`, `Actual Flight Speed` and `Height`, none of which is recorded) |
| Commander drone call actor (`BP_CommandActor_Drone_C`) | `health` (number), `ownerEosId` (string) | `Health`, `SQ PC` → player state — on the drone pawn the same-named field holds the deployer or last pilot (09-05, §7); on the actor its behaviour is unread and is confirmed under tracker T8 |

Not recorded: who damaged or destroyed an actor. No last-damager field
exists in the reflected lists of the six command actors archived (creep,
UAV and F/A-18 on 08-30; drone, F/A-18, SU-25 bomb and mortar on
09-02). The drone pawn's `LastHitBy` is recorded in §7.
Tracker T9 is the observation that would add an aircraft attribution
read, if one exists.

The shells, rockets and bombs an asset fires are already tracked
projectiles with `firer` = the commander (bombs 09-02/03; 155 mm shells
at 97 % impact capture in a production recording), so the actor record
adds the plan and its progress, never the impacts.

## 7. Surface E — the `drones` list and the position-line `drones` key

One entry per live pawn whose class derives from `SQFlyingDrone`
(`BP_FlyingDrone_C` for the commander's, `BP_FlyingDrone_Recoverable_C`
for the recon kit's, any future variant), every full frame, present
only while a pawn exists; the position exclusion of §2 drops the dead
pawn's zeroed final tick. Evidence: `docs/drones.md` — one commander
flight 2026-09-02, five recon flights 2026-09-05 (both classes' layouts
archived in Misc `command-probe-2026-09-05/`).

| Wire field | Memory source | Type | Meaning |
|---|---|---|---|
| `id` | pawn address | id | new on every deploy — picking a drone up destroys the pawn, and every redeploy or re-arm is a fresh one (09-05) |
| `class` | class name | string | which drone |
| `position`, `yaw` | root transform | position, degrees | where it is |
| `dead` | `Dead` | bool | true from the moment the battery expires or it is destroyed |
| `health`, `maxHealth` | `HealthComponent` → `Health` (float), `Max Health` (double) | number | 15 / 15 on the recon drone; one rifle burst takes it to 0 (09-05) |
| `pilotEosId` | `PlayerState` → player state | string | who is flying it this frame; `null` while nobody is — landed and exited, or deployed and not yet possessed (48 s of a fresh deploy read no pilot, flight 4, 09-05) |
| `ownerEosId` | `SQ PC` → its player state | string | the deployer or last pilot; observed to persist through de-possession and death (09-05). What a hand-off does to it is tracker T10 |
| `commandAction` | `Command Action` (class) | string | the calling action on a commander drone; `null` on a recon drone, where the pointer reads null (every recon row, 09-05) |
| `batteryLifetimeMax` | `BatteryLifetimeMax` | number, s | the flight budget from spawn (100 on the recon class; the field does not exist on the commander drone's class, whose budget is its action's `activeSec` in §3) |
| `lastHitByEosId` | `LastHitBy` → controller → player state | string | who last hit it; `null` until something has (every live row, 09-05); resolved to the shooter at the kill |

The pawn's `PlayerState`, `Controller` and `LastHitBy` are `Pawn`'s own
properties (`SQFlyingDrone` adds none); the reader's layout read merges
the super chain, so they resolve by name on the drone class. No
remaining-time field exists in memory (`EndFlightTimer` is a timer
handle; `BleedOutTime` is a constant 30 that is neither the flight time
nor the linger) and none is recorded. Team is not a field of the pawn;
the viewer derives it from `ownerEosId` (§9).

**Position line** (`{"t": "pos"}`, 4 Hz): a `drones` array joins
`players` and `vehicles`, entries `{id, x, y, z, yaw}` with the same `id`
as the full-frame entry, under the sampler's existing freshness gates
(class pointer intact, position finite and in bounds; a freed pawn is
omitted). No `h` or `team`: the drone's health changes only at death,
which the full frame carries, and team derives from the owner. Measured
cost ~115 B of raw JSON per drone per sample, ~28 KB on disk per
ten-minute flight, three to four small reads per drone per sample.
Touch points: `possample.SampledEntities` / `sample_positions`, the
position-frame key, the viewer's reconstructor, the schema register.

## 8. Doctor coverage

Every read above is reflection-resolved, so each class gains a
`required_reflection_names()` row (type, meta-class, optional?,
[property names]); no hardcoded offset is introduced, so
`hardcoded_offset_tables()` gains nothing. The register takes exact
class names. Rows marked optional carry the observed reason the register
demands: Blueprint content classes load with a layer, a claim or a call,
and their absence on an idle server is not drift.

| Type | Meta-class | Optional | Properties |
|---|---|---|---|
| `SQTeamState` | Class | no | `CommanderState` (existing row) |
| `SQCommanderState` | Class | no | `CurrentCommander` (existing), `bCommanderIsActive`, `bActionsEnabled`, `bVoteInProgress`, `CommanderVoteTimer`, `CommanderVoteTimestamp`, `bVoteCooldownActive`, `VoteCooldownTimer`, `VoteCooldownTimestamp`, `CommanderCategories`, `LastCategoryGameTime`, `CommandIntervals`, `NomineeStatus` |
| `SQCommanderManager` | Class | no | `bCommanderActive`, `VotingTimeSeconds`, `VoteCooldownTimeSeconds`, `ActionCooldownExtensionOnNewCommander`, `MinimumSquadSizeForVoting`, `MinimumSquadsRequiredForVoting` |
| `SQCommandActionData` | ScriptStruct | no | `CommandActionData`, `GameTimeAtCreation`, `CooldownTimeRemaining`, `IsDestroyedDuringActive` |
| `SQCommandActionDataFASItem` | ScriptStruct | no | `Content` |
| `CommanderVoteNominee` | ScriptStruct | no | `NomineeState`, `VoteCount` |
| `CommanderCategory` | ScriptStruct | no | `Name`, `CooldownDuration` |
| the `CommandAction_*` classes' common base — its name taken from reflection at implementation (the CDOs load only when a claim resolves, so none was loaded on the 09-05 layer) | Class | no | `CategoryId`, `EnrouteDuration`, `ActiveDuration`, `CooldownDuration` |
| `SQFlyingDrone` | Class | no | `PlayerState`, `LastHitBy` (inherited from `Pawn`; present on an idle server, 09-04 self-test) |
| `BP_FlyingDrone_C` | Class | yes — content, loads with a layer that has it | `SQ PC`, `HealthComponent`, `Dead`, `Command Action` |
| `BP_FlyingDrone_Recoverable_C` | Class | yes — content | `BatteryLifetimeMax` |
| `HealthComponent_C` | Class | yes — content | `Health`, `Max Health` |
| `BP_MapMarker_CommandMaster_C` | Class | yes — content, loaded on an idle server on 09-04 and 09-05 | `Distance`, `AddDistance` |
| `BP_MapMarker_DirectorMaster_C` | Class | yes — content | `Distance` |
| `BP_CommandActor_Artillery_Creep_C`, `BP_CommandActor_UAV_MQ9_C`, `BP_CommandActor_FA18_Rockets_Strafe_USMC_C` (archived 08-30), `BP_CommandActor_Drone_C`, `BP_CommandActor_SU25_Bomb_Strafe_C`, `BP_CommandActor_Mortar_Radius_C` (archived 09-02) | Class | yes — content, exist only during a call | the common and family properties of §6 each class carries; a Blueprint parent common to the family, if reflection shows one at implementation, replaces the per-class rows |

## 9. Viewer rules (interpretation; nothing here is recorded)

Each rule names the fields it reads. Where a rule rests on a test, the
test is cited by tracker id; the fields do not change when it runs.

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
- **Drones.** Stop drawing at `dead`. Team = the team of `ownerEosId`'s
  player in the same frame. Remaining flight time = the `worldTimeSec`
  of the first frame the `id` appears + `batteryLifetimeMax` (recon) or
  the calling action's `activeSec` (commander) − now. A new `id` whose
  `ownerEosId` and team match one that just vanished is the same kit
  redeployed, if continuity is wanted; the recorder never joins them.
  Interpolate at 4 Hz knowing cruise is ~10 m/s (2.5 m per sample).
- **Asset display names.** SquadCalc's per-asset table maps the
  `CommandAction_*` class names to display names and agrees with the
  config values (cross-checked 2026-09-04).

## 10. Deliberately not recorded

Two lists. First, the choices made when the contract was agreed:
`bDoubleCaptureSpeed` and `bCommandActionAttempted` (never left 0
across 21,000 rows); any "ready in" or remaining-time number; any
event line; any rule; the request circle radius; the bomb map circles;
any last-damager for a command actor (none exists in memory); the recon
launcher (no deployable exists — the launcher is the kit item in the
soldier's inventory); the command marker's `Action` pointer (tracker
D13).

Second, exhaustively, every other game-level property the classes of
§3–§7 carry, from test T11's full layouts (2026-09-05), each with the
reason it is not read. Identity and hop classes (`SQTeamState`,
`SQPlayerState`, `Controller`, `Pawn`, `SQFlyingDrone`) are read for one
pointer each and their other properties belong to other workstreams.
Where a property is worth a decision it is cited by tracker id. Where
a property is present on only some classes of a group, the class is
named.

| Class | Property (type) | Not recorded because |
|---|---|---|
| `SQCommanderState` | `VoteCooldownTimeSeconds` (Int) | copy of the manager's rules (§4) |
| ″ | `bCommandActionAttempted` (Bool) | never left 0 across 21,000 rows (09-04) |
| ″ | `bDoubleCaptureSpeed` (Bool) | never left 0 across 21,000 rows (09-04) |
| ″ | `MinimumSquadSizeForVoting` (Int) | copy of the manager's rules (§4) |
| ″ | `MinimumSquadsRequiredForVoting` (Int) | copy of the manager's rules (§4) |
| ″ | `VotingTimeSeconds` (Int) | copy of the manager's rules (§4) |
| ″ | `TeamCommands` (Object) | the faction's action DataTable, not runtime state |
| ″ | `OnCommanderChangedEvent` (MulticastInlineDelegate) | event hook, not state |
| ″ | `OnNominationAvailableEvent` (MulticastInlineDelegate) | event hook, not state |
| ″ | `OnNominationEndedEvent` (MulticastInlineDelegate) | event hook, not state |
| ″ | `OnNominationStartedEvent` (MulticastInlineDelegate) | event hook, not state |
| `SQCommanderManager` | `CommanderState` (Object) | structure pointer / category config read through the state |
| ″ | `TeamCommands` (Object) | structure pointer / category config read through the state |
| ″ | `Categories` (Array) | structure pointer / category config read through the state |
| ″ | `bDoubleCaptureSpeed` (Bool) | never left 0 (09-04) |
| `BP_FlyingDrone_C` (and, inherited, the recon subclass) | `HitBox` (Object) | engine or visual component |
| ″ | `SC_QuadcoptersAudio` (Object) | engine or visual component |
| ″ | `Camera` (Object) | engine or visual component |
| ″ | `SQMapIcon` (Object) | engine or visual component |
| ″ | `SQCoreState` (Object) | object pointer; a possible direct team source, unread — team derives from the owner (§9) |
| ″ | `Blade4` (Object) | engine or visual component |
| ″ | `Blade3` (Object) | engine or visual component |
| ″ | `Blade2` (Object) | engine or visual component |
| ″ | `Blade` (Object) | engine or visual component |
| ″ | `Body` (Object) | engine or visual component |
| ″ | `Explode Effect` (Object) | engine or visual component |
| ″ | `Explode Sound` (Object) | engine or visual component |
| ″ | `Can Possess` (Bool) | false at death; `dead` covers it (09-05) |
| ″ | `CrashVelocity` (Double) | flight-model config |
| ″ | `Max Fly Height` (Double) | constant 2200; meaning unresolved (09-05) |
| ″ | `Can Increase Altitude` (Bool) | flight-model config |
| ″ | `Altitude Timer` (Struct) | flight-model config |
| ″ | `Zoom Level` (Int) | camera / FPV state |
| ″ | `Desired Zoom` (Double) | camera / FPV state |
| ″ | `Zoom Levels` (Array) | camera / FPV state |
| ″ | `FPV Item Class` (Class) | camera / FPV state |
| ″ | `BankAngleLimit` (Double) | flight-model config |
| ″ | `DebugFloatHistory` (Struct) | internal |
| ″ | `FPV Item` (Object) | camera / FPV state |
| ″ | `BleedOutTime` (Double) | constant 30 / a timer handle — neither is a time (09-05) |
| ″ | `EndFlightTimer` (Struct) | constant 30 / a timer handle — neither is a time (09-05) |
| ″ | `CollisionDamageFactor` (Double) | flight-model config |
| ″ | `TargetInventoryOffset` (Int) | internal |
| ″ | `NAME_IMC_State` (Name) | internal |
| ″ | `NuisanceTarget` (Object) | internal |
| `BP_FlyingDrone_Recoverable_C`, its own additions only | `HealthComponent` (Object) | inherited; read on the parent class row (§7) |
| ″ | `SQ PC` (Object) | inherited; read on the parent class row (§7) |
| ″ | `Dead` (Bool) | inherited; read on the parent class row (§7) |
| ″ | `Command Action` (Class) | inherited; read on the parent class row (§7) |
| ″ | `UsableData` (Struct) | internal |
| `HealthComponent_C` | `Health Gained` (MulticastInlineDelegate) | event hook, not state |
| ″ | `Health Lost` (MulticastInlineDelegate) | event hook, not state |
| ″ | `Health Zero` (MulticastInlineDelegate) | event hook, not state |
| ″ | `Health Max` (MulticastInlineDelegate) | event hook, not state |
| every command and director marker class (the same ten on all eight) | `Team` (Enum) | already recorded by the existing marker read |
| ″ | `MapIcon` (Object) | cosmetic or replication plumbing |
| ″ | `StateObject` (Object) | cosmetic or replication plumbing |
| ″ | `bReplicateOwnerState` (Bool) | cosmetic or replication plumbing |
| ″ | `OwnerPlayerState` (Object) | already recorded by the existing marker read |
| ″ | `Squad` (Int) | already recorded by the existing marker read |
| ″ | `FireTeamId` (Int) | already recorded by the existing marker read |
| ″ | `PlacementEmote` (Enum) | cosmetic or replication plumbing |
| ″ | `DefaultSceneRoot` (Object) | cosmetic or replication plumbing |
| ″ | `DefaultTint` (Struct) | cosmetic or replication plumbing |
| both artillery actors (creep 08-30, mortar 09-02: identical) | `Arrow` (Object) | engine or visual component |
| ″ | `DefaultSceneRoot` (Object) | engine or visual component |
| ″ | `Edge Only` (Bool) | placement config |
| ″ | `Pre Warning Delay` (Double) | fire-plan timing, scatter and pre-warning progress beyond the agreed plan fields — tracker D14 |
| ″ | `Barrage Interval` (Struct) | fire-plan timing, scatter and pre-warning progress beyond the agreed plan fields — tracker D14 |
| ″ | `First Barrage Height Variance` (Double) | fire-plan timing, scatter and pre-warning progress beyond the agreed plan fields — tracker D14 |
| ″ | `Main Barrage Height Variance` (Double) | fire-plan timing, scatter and pre-warning progress beyond the agreed plan fields — tracker D14 |
| ″ | `Current Prewarning Shells` (Int) | fire-plan timing, scatter and pre-warning progress beyond the agreed plan fields — tracker D14 |
| both strike actors (F/A-18 08-30/09-02, SU-25 bomb 09-02) | `Arrow` (Object) | engine or visual component |
| ″ | `DefaultSceneRoot` (Object) | engine or visual component |
| ″ | `Cam` (Object) | engine or visual component |
| ″ | `FlybyAudio` (Object) | engine or visual component |
| ″ | `HealthComponent` (Object) | engine or visual component |
| ″ | `PrimaryWeapon` (Object) | engine or visual component |
| ″ | `SplineLeft` (Object) | engine or visual component |
| ″ | `SplineParent` (Object) | engine or visual component |
| ″ | `Aircraft` (Object) | engine or visual component |
| ″ | `Scale_NewTrack_0_D98340604061CF915BF9F68AF206DF47` (Float) | engine or visual component |
| ″ | `Scale__Direction_D98340604061CF915BF9F68AF206DF47` (Byte) | engine or visual component |
| ″ | `Scale` (Object) | engine or visual component |
| ″ | `Flight Speed` (Double) | run mechanics; position, `yaw` and `splineDistance` already carry the run |
| ″ | `Can Fly` (Bool) | run mechanics; position, `yaw` and `splineDistance` already carry the run |
| ″ | `Selected Spline` (Object) | engine or visual component |
| ″ | `Attack Duration` (Double) | run mechanics; position, `yaw` and `splineDistance` already carry the run |
| ″ | `UseFireSplineDistance` (Bool) | run mechanics; position, `yaw` and `splineDistance` already carry the run |
| ″ | `Fire Spline Point Index` (Int) | run mechanics; position, `yaw` and `splineDistance` already carry the run |
| ″ | `Origin Forward` (Struct) | run mechanics; position, `yaw` and `splineDistance` already carry the run |
| ″ | `Firing Timer` (Struct) | a timer handle |
| ″ | `Explode Sound` (Object) | engine or visual component |
| ″ | `Explode Effects` (Object) | engine or visual component |
| ″ | `Fire Spline Distance` (Double) | run mechanics; position, `yaw` and `splineDistance` already carry the run |
| ″ | `MuzzleEffect` (Object) | engine or visual component |
| ″ | `Per Bullet Sound` (Object) | engine or visual component |
| ″ | `One Shot On Fire Sound` (Object) | engine or visual component |
| ″ | `One Shot End Fire Sound` (Object) | engine or visual component |
| ″ | `Scaled In` (Bool) | run mechanics; position, `yaw` and `splineDistance` already carry the run |
| ″ | `My Cam` (Object) | engine or visual component |
| ″ | `Smooth Distance` (Double) | run mechanics; position, `yaw` and `splineDistance` already carry the run |
| ″ | `FA18_pylon_paveway` (Object) — only `BP_CommandActor_FA18_Rockets_Strafe_USMC_C` | engine or visual component |
| ″ | `FA18_pylon_apkws` (Object) — only `BP_CommandActor_FA18_Rockets_Strafe_USMC_C` | engine or visual component |
| ″ | `InitialSplinePoint` (Int) — only `BP_CommandActor_SU25_Bomb_Strafe_C` | run mechanics; position, `yaw` and `splineDistance` already carry the run |
| ″ | `CalcTolerance` (Double) — only `BP_CommandActor_SU25_Bomb_Strafe_C` | run mechanics; position, `yaw` and `splineDistance` already carry the run |
| `BP_CommandActor_UAV_MQ9_C` | `Arrow` (Object) | engine or visual component |
| ″ | `DefaultSceneRoot` (Object) | engine or visual component |
| ″ | `HealthComponent` (Object) | engine or visual component |
| ″ | `Mesh` (Object) | engine or visual component |
| ″ | `SpringArm` (Object) | engine or visual component |
| ″ | `Cam` (Object) | engine or visual component |
| ″ | `Timeline_0_NewTrack_0_719303D24143C95CB5BCE08AD124BFC2` (Float) | engine or visual component |
| ″ | `Timeline_0__Direction_719303D24143C95CB5BCE08AD124BFC2` (Byte) | engine or visual component |
| ″ | `Timeline_0` (Object) | engine or visual component |
| ″ | `Min Flight Speed` (Double) | orbit mechanics; the coverage marker and the actor's position carry what the viewer draws |
| ″ | `Scaled In` (Bool) | orbit mechanics; the coverage marker and the actor's position carry what the viewer draws |
| ″ | `My Cam` (Object) | engine or visual component |
| ″ | `Max Flight Speed` (Double) | orbit mechanics; the coverage marker and the actor's position carry what the viewer draws |
| ″ | `Explode Effect` (Object) | engine or visual component |
| ″ | `Explode Sound` (Object) | engine or visual component |
| ″ | `Actual Flight Speed` (Double) | orbit mechanics; the coverage marker and the actor's position carry what the viewer draws |
| ″ | `Current Rotation` (Double) | orbit mechanics; the coverage marker and the actor's position carry what the viewer draws |
| ″ | `Origin Scale` (Struct) | orbit mechanics; the coverage marker and the actor's position carry what the viewer draws |
| ″ | `Height` (Double) | orbit mechanics; the coverage marker and the actor's position carry what the viewer draws |
| `BP_CommandActor_Drone_C` | `Arrow` (Object) | engine or visual component |
| ″ | `DefaultSceneRoot` (Object) | engine or visual component |
| ″ | `Equippable Drone Item Class` (Class) | spawn plumbing |
| ″ | `TargetInventorySlot` (Int) | spawn plumbing |
| the action configs (three CDOs archived 09-02) | `DisplayName` (Str) | the game's own display text — tracker D15 |
| ″ | `Description` (Str) | the game's own display text — tracker D15 |
| ″ | `Texture` (Object) | UI: icon, tint, widget, sounds |
| ″ | `Tint` (Struct) | UI: icon, tint, widget, sounds |
| ″ | `CommandActor` (Class) | reverse joins from the action to its actor and marker classes (see D13) |
| ″ | `ControlWidget` (Class) | UI: icon, tint, widget, sounds |
| ″ | `IconAngleOffset` (Float) | UI: icon, tint, widget, sounds |
| ″ | `MaxAngleFromBase` (Float) | placement bounds — tracker D16 |
| ″ | `CreateMapMarker` (Bool) | config flag with no agreed use |
| ″ | `bAllowedInVehicle` (Bool) | config flag with no agreed use |
| ″ | `bIgnoreActionEnabled` (Bool) | config flag with no agreed use |
| ″ | `MapMarkerClass` (Class) | reverse joins from the action to its actor and marker classes (see D13) |
| ″ | `CommanderActionSoundsList` (Struct) | UI: icon, tint, widget, sounds |
| ″ | `MinimumDistance` (Float) — only `CommandAction_Mortar_Barrage_IMF_C`, `CommandAction_Mortar_Barrage_INS_C` | placement bounds — tracker D16 |
| ″ | `MaximumDistance` (Float) — only `CommandAction_Mortar_Barrage_IMF_C`, `CommandAction_Mortar_Barrage_INS_C` | placement bounds — tracker D16 |

## 11. Acceptance

Tracker T8: a six-player run with the command-assets probe as oracle,
after W18 lands, comparing recorded values against the probe at the same
instants, the doctor clean with the rows of §8, the parity harness
green, and the viewer drawing the shapes, actors and drones of §9. T8
also carries the commander-drone confirmations of the fields in §7.

## 12. How this document was checked, and how to check it again

A fresh session with no memory of the work reviewed the first draft on
2026-09-05 against the six questions below and returned 23 corrections,
all applied; a second pass the same day found the creep and UAV actor
layouts already archived from 08-30, which settled two of them outright.
The questions stand for the next review.

1. Does every row of the journal's four "Agreed capture" sections
   (decisions 2, 5, 6 and 7; decision 3 is the paragraph inside decision
   2's section) and of the drones doc's "Verified in memory" appear here
   with the same memory source and the same meaning?
2. Does every field here trace to a dated, verified statement in the
   journal or the drones doc, or to an archived layout — nothing
   introduced without evidence?
3. Where the journal disagrees with itself, does this document take the
   later statement? The known disagreements and the side taken: cooldown
   "anchored to the call" versus restarting from destruction
   (destruction, 09-04); action config values "once per entry" versus
   every frame (every frame, decision 2); "the one new top-level list"
   versus two lists (two, decision 7); the format rule as it stood versus
   its reword (the reword, 09-04); a "reopened" category gate re-closed
   the same day (closed); a section headed "not yet agreed" whose items
   were agreed (agreed); a commander-state table older than the field
   names read on 09-02 (the 09-02 names); two "enabled" flags left
   unlabelled (§4 labels them); the creep actor's unspaced field names
   (08-30 transcription) versus the mortar's spaced names (09-02 layout)
   (spaced — both layouts carry the spaces, the journal's transcription
   dropped them, §6); the drone's owner on the call actor's
   `DamageInstigatorController` (09-02) versus the pawn's `SQ PC` (09-05)
   (`SQ PC`, §7); a recon launcher deployable (09-04) versus the kit item
   (09-05) (the item, §10); the vote rules on the commander state (08-30
   table) versus the manager (09-04) (the manager, §4). The journal's
   "explicit `null` when the seat is empty" is kept and generalised into
   the two-way rule of §2, "Absent reads".
4. Does every memory name here resolve by reflection? Test T11
   (`scripts/probes/spec_names_check.py`) answers this mechanically — live
   for loaded classes, from the archived layouts for per-call classes — and
   on 2026-09-05 resolved all 157 names (§13). The one name reflection has
   not yet supplied, and this document says so where it uses it: the
   `CommandAction_*` common base, whose CDOs load only when a claim
   resolves; the four properties themselves resolved on three archived
   CDOs.
5. Is anything here computed, inferred or defaulted on the recorder
   side beyond the three things §1 names?
6. Does this document carry any state word — open, pending, outstanding,
   decoded, done — about its own content or the program, other than game
   states (a vote being open, a request pending approval) and this
   sentence? There should be none; unresolved interpretation is cited by
   tracker id.

## 13. Names as resolved (test T11, 2026-09-05)

Every class, struct and property this document names, resolved by
reflection: live on the box for the classes loaded on 2026-09-05, from
the archived layouts (08-30, 09-02) for the classes that exist only
during a call. Types are the reflected property types; offsets are in
the archived probe output (Misc `command-probe-2026-09-05/`,
`spec_names_check.live.jsonl` and `.archive.jsonl`) and are values of
their day, never read by the implementation. All 157 names resolved —
57 on the box for 18 classes, 131 from the archives for 19, eight classes
checked both ways, and one class (`SQTeamState`) live only.

| Class | Source | Names → reflected type |
|---|---|---|
| `SQTeamState` | live, 09-05 | `CommanderState` Object |
| `SQCommanderState` | live, 09-05 | `CurrentCommander` Object; `bCommanderIsActive` Bool; `bActionsEnabled` Bool; `bVoteInProgress` Bool; `CommanderVoteTimer` Int; `CommanderVoteTimestamp` Int; `bVoteCooldownActive` Bool; `VoteCooldownTimer` Int; `VoteCooldownTimestamp` Int; `CommanderCategories` Array; `LastCategoryGameTime` Array; `CommandIntervals` Struct; `NomineeStatus` Struct |
| `SQCommanderManager` | live, 09-05 | `bCommanderActive` Bool; `VotingTimeSeconds` Int; `VoteCooldownTimeSeconds` Int; `ActionCooldownExtensionOnNewCommander` Float; `MinimumSquadSizeForVoting` Int; `MinimumSquadsRequiredForVoting` Int |
| `SQCommandActionData` | live, 09-05 | `CommandActionData` Class; `GameTimeAtCreation` Float; `CooldownTimeRemaining` Float; `IsDestroyedDuringActive` Bool |
| `SQCommandActionDataFASItem` | live, 09-05 | `Content` Struct |
| `CommanderVoteNominee` | live, 09-05 | `NomineeState` Object; `VoteCount` Int |
| `CommanderCategory` | live, 09-05 | `Name` Text; `CooldownDuration` Float |
| `SQPlayerState` | live, 09-05 | `PlayerNamePrivate` Str; `OnlineUserId` Str |
| `Controller` | live, 09-05 | `PlayerState` Object |
| `Pawn` | live, 09-05 | `PlayerState` Object; `Controller` Object; `LastHitBy` Object |
| `SQFlyingDrone` | live, 09-05 | `PlayerState` Object; `LastHitBy` Object |
| `BP_FlyingDrone_C` | live, 09-05 | `SQ PC` Object; `HealthComponent` Object; `Dead` Bool; `Command Action` Class |
| `BP_FlyingDrone_Recoverable_C` | live, 09-05 | `BatteryLifetimeMax` Double |
| `HealthComponent_C` | live, 09-05 | `Health` Float; `Max Health` Double |
| `BP_MapMarker_CommandMaster_C` | live, 09-05 | `Distance` Double; `AddDistance` Double; `Action` Class; `Request` Bool |
| `BP_MapMarker_DirectorMaster_C` | live, 09-05 | `Distance` Double |
| `BP_MapMarker_CommandPath_C` | archive, 08-30 | `Distance` Double; `AddDistance` Double; `Action` Class; `Request` Bool |
| `BP_MapMarker_CommandLine_C` | archive, 09-02 | `Distance` Double; `AddDistance` Double; `Action` Class; `Request` Bool |
| `BP_MapMarker_CommandLineRadius_C` | archive, 09-02 | `Distance` Double; `AddDistance` Double; `Action` Class; `Request` Bool |
| `BP_MapMarker_CommandRadius_C` | archive, 09-02 | `Distance` Double; `AddDistance` Double; `Action` Class; `Request` Bool |
| `BP_MapMarker_CommandRadius_Friendly_C` | archive, 08-30 | `Distance` Double; `AddDistance` Double; `Action` Class; `Request` Bool |
| `BP_MapMarker_Command_Request_C` | live, 09-05 | `Distance` Double; `AddDistance` Double; `Action` Class; `Request` Bool |
| `BP_MapMarker_Command_SLRequest_C` | live, 09-05 | `Distance` Double; `AddDistance` Double; `Action` Class; `Request` Bool |
| `BP_CommandActor_Artillery_Creep_C` | archive, 08-30 | `Distance` Float; `Team` Int; `DamageInstigatorController` WeakObject; `Action` Class; `Action Destroyed` Bool; `Destroy Delay after Action Destroyed` Double; `Origin Location` Struct; `target location` Struct; `Max Drop Radius` Double; `Pre Warning Shells` Int; `Shells Per Barrage` Int; `Barrage Count` Int; `Current Barrage` Int; `Projectile` Class |
| `BP_CommandActor_Mortar_Radius_C` | archive, 09-02 | `Distance` Float; `Team` Int; `DamageInstigatorController` WeakObject; `Action` Class; `Action Destroyed` Bool; `Destroy Delay after Action Destroyed` Double; `Origin Location` Struct; `target location` Struct; `Max Drop Radius` Double; `Pre Warning Shells` Int; `Shells Per Barrage` Int; `Barrage Count` Int; `Current Barrage` Int; `Projectile` Class |
| `BP_CommandActor_FA18_Rockets_Strafe_USMC_C` | archive, 09-02 | `Distance` Float; `Team` Int; `DamageInstigatorController` WeakObject; `Action` Class; `Action Destroyed` Bool; `Destroy Delay after Action Destroyed` Double; `Health` Double; `Dead_0` Bool; `CurrentShotsMade` Int; `MaxShots` Int; `Spline Distance` Double; `Origin Location` Struct |
| `BP_CommandActor_SU25_Bomb_Strafe_C` | archive, 09-02 | `Distance` Float; `Team` Int; `DamageInstigatorController` WeakObject; `Action` Class; `Action Destroyed` Bool; `Destroy Delay after Action Destroyed` Double; `Health` Double; `Dead_0` Bool; `CurrentShotsMade` Int; `MaxShots` Int; `Spline Distance` Double; `Origin Location` Struct |
| `BP_CommandActor_UAV_MQ9_C` | archive, 08-30 | `Distance` Float; `Team` Int; `DamageInstigatorController` WeakObject; `Action` Class; `Action Destroyed` Bool; `Destroy Delay after Action Destroyed` Double; `Health` Double; `Dead_0` Bool |
| `BP_CommandActor_Drone_C` | archive, 09-02 | `Distance` Float; `Team` Int; `DamageInstigatorController` WeakObject; `Action` Class; `Action Destroyed` Bool; `Destroy Delay after Action Destroyed` Double; `Health` Double; `SQ PC` Object |
| `CommandAction_Drone_C` | archive, 09-02 | `CategoryId` Byte; `EnrouteDuration` Float; `ActiveDuration` Float; `CooldownDuration` Float |
| `CommandAction_Mortar_Barrage_INS_C` | archive, 09-02 | `CategoryId` Byte; `EnrouteDuration` Float; `ActiveDuration` Float; `CooldownDuration` Float |
| `CommandAction_Mortar_Barrage_IMF_C` | archive, 09-02 | `CategoryId` Byte; `EnrouteDuration` Float; `ActiveDuration` Float; `CooldownDuration` Float |
