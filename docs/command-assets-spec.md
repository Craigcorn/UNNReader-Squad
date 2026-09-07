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
  Four narrow things the recorder does that are not raw reads, named
  here so nothing else is: it follows pointers (a player state's id and
  name, a class's name, an action entry's config values from that
  class's defaults); it produces one key of its own, the category index
  `i` (§3); it applies the existing sanity exclusion to positions (§2,
  "Positions"); and it keeps the 4 Hz drone sample small by omitting
  `dead` and `lastHitBy` until they are set (§2, "Absent reads"; §7).
- **Reflection by name.** Every offset in the journal is the value of its
  day and moved twice during the sessions; the implementation resolves
  every field by reflection name and registers each read with the
  doctor (§8). FastArray element sizes come from the inner struct's
  reflected size, never a constant (the 09-02 probe lost a sample to a
  guessed stride), and a plain array's stride is its element property's
  type size — on this build the header's `ElementSize` reads 0 for a
  float array (2026-09-07). Every name is attempted on the object at hand; the
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
  frame-key register. No frame or container version moves; the packed
  replay stream's format moves only if the packer index-tracks the new
  lists, which is its own rule (CLAUDE.md) and W18's call. A packer
  built to the current replay format carries the new lists whole, and
  its round-trip test covers them.
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
| Absent reads | Two cases, kept distinct because they mean different things to a viewer. **`null`** = the game's own value is empty and was read successfully: a pointer that reads null (no commander in the seat, no pilot in the drone, no calling action on a recon drone), an array with no element at the index (a category never called). **Omitted** = the recorder could not read: the class lacks the field, the pointer reaches an object that is not what the field names, or the read fails. So a viewer that sees `null` knows "none", and a viewer that sees nothing knows "unknown". This is the journal's contract for the identity fields ("explicit `null` when the seat is empty") applied to every field. One exception, named in §1: the 4 Hz position line (§7) omits `dead` while it reads false and `lastHitBy` while it reads null, to keep the sample small; absence there means "not set", never "unknown", and the full frame carries the two-way reading. |
| Class names | The object's class name verbatim, e.g. `BP_CommandActor_SU25_Bomb_Strafe_C`, `CommandAction_Drone_C`. A class pointer that reads null gives `null`. |
| Positions | `{x, y, z}` in world centimetres from the root transform, as vehicles record them. The existing sanity exclusion applies unchanged: a `commandActions` or `drones` entry whose root position reads exactly (0, 0, 0) is dropped from the list (the junk-vehicle test; a dead drone's final tick reads (0, 0, 0) before the pawn is freed, 09-05), and the position line applies the sampler's existing finite-and-in-bounds gate plus the same (0, 0, 0) drop to its `drones` entries. |
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
| `commander.vote.endsGameTime` | `CommanderVoteTimestamp` | int, game s | the current or last vote's end: written when the vote opens, as the open time plus `votingTimeSec` (three votes, 2026-09-07) | every frame |
| `commander.vote.nominees[]` | `NomineeStatus.Items[]` (`CommanderVoteNominee`, 32-byte items): `NomineeState` → player state; `VoteCount` | `{eosId, name, votes}` | each nominee and the live tally; no per-voter ballots exist in memory (three votes read at 96-byte width, 09-02). Entries persist after resolution, so the frame after `inProgress` drops still carries the final tallies — the recorder keeps no memory across frames | every frame: `[]` while the array is empty (read, and none — §2), the entries otherwise |
| `commander.vote.cooldownActive` | `bVoteCooldownActive` | bool | the block on new votes after a claim | every frame |
| `commander.vote.cooldownTimer` | `VoteCooldownTimer` | int, s | its countdown (counts down after every claim, 09-02; the manager's `VoteCooldownTimeSeconds` read 300, 09-04) | every frame |
| `commander.vote.cooldownEndsGameTime` | `VoteCooldownTimestamp` | int, game s | when it ends: written at the vote's resolution as the resolution time plus `voteCooldownSec`; `cooldownActive` reads true exactly until then (2026-09-07) | every frame |
| `commander.cooldowns.categories[]` | `CommanderCategories[i]` (`CommanderCategory`, 24-byte items): `Name` (FText), `CooldownDuration` (float); `LastCategoryGameTime[i]` (float) | `{id, name, intervalSec, lastUseGameTime}` | the per-category gate: any call in the category writes the stamp (strafe, mortar and three bomb calls all wrote index 1, 09-02). `id` is the array index `i` — the index `LastCategoryGameTime` uses and the value the actions' `categoryId` carries — the one key the recorder produces. `lastUseGameTime` is `null` while `LastCategoryGameTime` has no element at `i` (the array is empty until the first call) | every frame |
| `commander.cooldowns.actions[]` | `CommandIntervals.Items[]` (`SQCommandActionDataFASItem`, 40-byte items) `.Content` (`SQCommandActionData`): `CommandActionData` (class), `GameTimeAtCreation` (float), `CooldownTimeRemaining` (float), `IsDestroyedDuringActive` (bool); plus, from that class's defaults, `CategoryId` (byte), `EnrouteDuration`, `ActiveDuration`, `CooldownDuration` (floats), `DisplayName` (FString) | `{action, displayName, createdGameTime, remainingAtChange, destroyedDuringActive, categoryId, enrouteSec, activeSec, cooldownSec}` | one entry per action the team can call; entries appear at the first claim, back-dated by enroute plus active so each asset starts with its own cooldown to run (both claims, 09-02); a call rewrites `createdGameTime`; `displayName` is the config's own display text ("MQ-9 UAV Recon", "Heavy Mortar Barrage" — eleven read 2026-09-07; decision D15); `remainingAtChange` is the raw read — the game writes it at a commander change and at a step-down and leaves it otherwise, so it reads 0 until the first of those and keeps its last value through later calls (2026-09-07); `destroyedDuringActive` read 1 on the drone shot down on 09-02 and on the UAV and the drone shot down on 2026-09-07, with `createdGameTime` unchanged. The config values ride every frame so a seek into a replay is self-describing | every frame: `[]` before the first claim, the entries otherwise; an entry whose `CommandActionData` reads null carries `action` `null` and no config values, since the class cannot be read |

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
per-team `SQCommanderState` carries same-named fields, `VotingTimeSeconds`,
`VoteCooldownTimeSeconds`, `MinimumSquadSizeForVoting` and
`MinimumSquadsRequiredForVoting` (09-02 layout); the manager's are the
ones read, once, not per team. Two flags both called "enabled" are
deliberate and distinct: the manager's is the server setting; the
team's (`commander.enabled`, §3) is per-team state.

Several `SQCommanderManager` instances exist at once — four in the first
match of 2026-09-07, two in the second, the worlds the server holds —
all reading the same six values; the recorder reads the first live
instance it finds, class default objects excluded.

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
| `distance` | `Distance` | number, raw game units (cm) | the marker's own length figure, the commander's choice wherever the UI offers one: circle radius on `CommandRadius_Friendly` (the UAV's coverage marker: the chosen radius, 10000 on 08-30, 16608 and 19958 on 2026-09-07) and on `CommandRadius` (the static barrage's chosen radius, 15000; the mortar's fixed 7500), run length on `CommandLine` (the chosen 6000 and 3306), path length on `CommandPath` (45000, equal to the creep actor's), aim separation on `CommandLineRadius` (the chosen 44.75–120 m); 0 on request markers | whenever the marker's class carries the field |
| `addDistance` | `AddDistance` | number, raw | the secondary figure: drop scatter on `CommandPath` (7500), the outer band on `CommandRadius` (the mortar's 4500, the static barrage's 7500); 0 elsewhere | whenever the class carries the field |
| `yaw` | root transform | degrees | the marker's facing — the run direction, the path bearing, the aim line (the bomb marker's facing matched the aircraft's approach to within a degree, 09-02/03) | whenever `distance` is emitted, i.e. on the classes that carry `Distance` |
| `action` | `Action` (class) | string | the `CommandAction_*` config the marker belongs to, a join to `commander.cooldowns.actions[].action`: `null` on request markers (both classes, twelve markers across placement, approval, expiry and deletion, 2026-09-07), the calling config on every footprint (UAV coverage, static barrage, strike line, mortar radius, 2026-09-07); the drone's icon marker lies outside the Command family and carries no pointer | whenever the class carries the field |

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

Nothing else is recorded about request markers: `type` (the class name
is the only pending/approved discriminator — the two classes have
byte-identical layouts and the `Request` bool reads 1 on both),
position, `team`, `squad` and `ownerPlayerStateAddr` already reach the
file, and `action` reads `null` on them; `type` is the verbatim class
name (`BP_MapMarker_Command_SLRequest_C`), which §9 abbreviates to its
distinctive segment. How request markers live and die on the server is
test T15's finding, applied in §9.

## 6. Surface D — the `commandActions` list

One entry per live actor of the command-actor family, every full frame,
present only while such an actor exists (a handful per match, 30 s to
10 min each). Membership is a subclass test on the native `SQCommandActor`, which
every command actor derives from through the Blueprint `BP_CommandActor_C`
(chain reflected live 2026-09-07: `BP_CommandActor_ArtilleryBase_C` →
`BP_CommandActor_C` → `SQCommandActor` → `Actor`), the way the drones
test `SQFlyingDrone`; class default objects are never entries, and the level's
template actors that exist for one second at a layer load with null
pointers (2026-09-07) are recorded as read, `action` and `callerEosId`
`null`. Evidence: journal §"Per-call actors" (2026-08-30 to 09-03), the
agreed contract of 2026-09-04, eleven actor layouts archived (creep, UAV,
F/A-18 on 08-30; drone, F/A-18, SU-25 bomb, mortar on 09-02; the static
barrage, the F/A-18 gun, the A-10 and two more rocket classes on
2026-09-07).

Common fields, every actor:

| Wire field | Memory source | Type | Meaning |
|---|---|---|---|
| `id` | actor address | id | this call's actor |
| `class` | class name | string | e.g. `BP_CommandActor_Artillery_Creep_C` |
| `team` | `Team` | int | owning team |
| `action` | `Action` (class) | string | the `CommandAction_*` config class — joins `commander.cooldowns.actions[].action` |
| `callerEosId` | `DamageInstigatorController` (a weak object pointer, resolved through the object array as the reader's existing weak-pointer read does, `sqreader/squad/snapshot.py`) → controller → player state | string | the commander who called it: the attribution pointer the game itself uses for the asset's kills (a strafe wound event named the commander as attacker, 09-02) |
| `position`, `yaw` | root transform | position, degrees | where the actor is this frame — aircraft move along their run, artillery sits at its origin; the drone's call actor reads (0, 0, z), which the viewer ignores (§9) |
| `actionDestroyed` | `Action Destroyed` | bool | the call was cut short: true while the actor lingers after a shoot-down (a UAV at +67 s of a 330 s window, an aircraft at +33 s, 2026-09-07); on a natural end no one-second sample read it true before the actor vanished (two cases), and the drone's call actor never sets it (§9, "Shoot-downs") |
| `distance` | `Distance` | number, raw | the actor's own length figure (the creep read exactly 45000, its marker's path length) |

The four common names `Distance`, `Team`, `DamageInstigatorController`
and `Action` are declared on the native `SQCommandActor`, `Action
Destroyed` and the destroy delay on `BP_CommandActor_C` (reflected live
2026-09-07), and the doctor checks them there (§8).

Family fields, emitted where the class has them. Every name below is
attempted on every command actor; the family column says which classes
carried which on the days they were read. Blueprint variable names
contain spaces and are used verbatim.

| Family | Wire fields (type) | Memory source |
|---|---|---|
| Strike aircraft (`*_Strafe_*`: gun, rockets and bomb): a shootable pawn flying its run | `shotsMade` (int), `maxShots` (int), `splineDistance` (number), `originLocation` (`{x, y, z}`) | `CurrentShotsMade`, `MaxShots`, `Spline Distance`, `Origin Location` (09-02 layouts; the F/A-18 gun, A-10 and SU-25 rocket classes archived 2026-09-07 carry the same names). `Health` and `Dead_0` are not recorded — decision D18: an aircraft killed before it fired left both untouched, and its `HealthComponent_C` too |
| Artillery creep and barrage, mortar barrage: the fire plan and its progress | `originLocation` (`{x, y, z}`), `targetLocation` (`{x, y, z}`), `maxDropRadius` (number, raw — the creep's is centimetre-scale, the mortar's read 1.0), `preWarningShells` (int), `preWarningDelaySec` (number, s), `shellsPerBarrage` (int), `barrageCount` (int), `currentPrewarningShells` (int), `currentBarrage` (int), `projectile` (class name) | `Origin Location`, `target location`, `Max Drop Radius`, `Pre Warning Shells`, `Pre Warning Delay`, `Shells Per Barrage`, `Barrage Count`, `Current Prewarning Shells`, `Current Barrage`, `Projectile` — `Pre Warning Delay` and `Current Prewarning Shells` added by decision D14, their values read on 2026-09-05 from the 08-30 creep call's archived per-tick raws: delay 12.0 s constant; the counter 0 → 1 at +59.7 s and 2 at +67.1 s after the actor appeared, with `Current Barrage` reaching 1 at +79.2 s, twelve seconds after the second warning shell — the spellings reflected on both `BP_CommandActor_Artillery_Creep_C` (08-30 layout) and `BP_CommandActor_Mortar_Radius_C` (09-02 layout), identical names at identical offsets. The journal's creep entry dropped the spaces when it was transcribed; the layouts never did. The static barrage actor `BP_CommandActor_Artillery_Radius_C` (archived 2026-09-07) carries the same names; all ten are declared on the family's parent `BP_CommandActor_ArtilleryBase_C` (reflected live 2026-09-07), where the doctor checks them. The mortar's values, read 2026-09-07: `Pre Warning Shells` 0, `Pre Warning Delay` 0, `Shells Per Barrage` 10, `Barrage Count` 8, `Max Drop Radius` 1.0, `Current Barrage` advancing every 8–9 s from +30 s |
| UAV (`BP_CommandActor_UAV_MQ9_C`): position is the point; a shootable actor | none beyond the common fields | `Health` and `Dead_0` are not recorded — decision D18, reversing D17: a UAV shot down on 2026-09-07 left both untouched (1000, false) through the kill and a 16 s linger; the shoot-down is the common `actionDestroyed` (§9). The layout also carries `HealthComponent`, `Min Flight Speed`, `Max Flight Speed`, `Actual Flight Speed` and `Height`, none of which is recorded |
| Commander drone call actor (`BP_CommandActor_Drone_C`) | `health` (number), `ownerEosId` (string) | `Health` (read 100 throughout a call, 2026-09-07), `SQ PC` → player state — the commander who called it (2026-09-07, T14); on the pawn the same-named field holds the deployer (§7). This actor's root position reads (0, 0, z) and means nothing; it never flips `Action Destroyed` and outlives its window (still present 18 min past it, 2026-09-07) — the drone's own life is the pawn's (§7) |

Not recorded: who damaged or destroyed an actor. No last-damager field
exists in the reflected lists of the eleven command actors archived (§8;
the five added on 2026-09-07 carry none either), so the actor itself
cannot supply it. Neither the server log (no
damage or death line for a UAV or an aircraft, 2026-09-07) nor the
actor's `HealthComponent_C` (1000 / 1000 through a kill) names one, so
the shooter of a UAV or aircraft is unrecorded (decision D18). The drone
pawn's `LastHitBy` is recorded in §7.

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
| `ownerEosId` | `SQ PC` → its player state | string | the deployer; persists through de-possession and death (09-05, 2026-09-07). A drone cannot change hands (T10, 2026-09-07), so it never moves |
| `commandAction` | `Command Action` (class) | string | the calling action on a commander drone; `null` on a recon drone, where the pointer reads null (every recon row, 09-05) |
| `batteryLifetimeMax` | `BatteryLifetimeMax` | number, s | the flight budget from spawn (100 on the recon class; the field does not exist on the commander drone's class, whose budget is its action's `activeSec` in §3) |
| `lastHitByEosId` | `LastHitBy` → controller → player state | string | who last hit it; `null` until something has (every one of 1,619 live rows, 09-05); the killer at the kill (09-05 on a landed drone; 2026-09-07 in flight, in the same 100 ms sample as `dead`). Later hits on the falling pawn move it (the pointer moved to a second shooter 1.1 s after the kill, in the same 100 ms sample as the pilot pointers cleared), so the killer is the value of the first 4 Hz sample carrying `dead`, exact to a quarter second (decision D19) |

The pawn's `PlayerState`, `Controller` and `LastHitBy` are `Pawn`'s own
properties (`SQFlyingDrone` adds none); the reader's layout read merges
the super chain, so they resolve by name on the drone class. No
remaining-time field exists in memory (`EndFlightTimer` is a timer
handle; `BleedOutTime` is a constant 30 that is neither the flight time
nor the linger) and none is recorded. Team is not a field of the pawn;
the viewer derives it from `ownerEosId` (§9).

**Position line** (`{"t": "pos"}`, 4 Hz): a `drones` array joins
`players` and `vehicles`, entries `{id, x, y, z, yaw}` with the same `id`
as the full-frame entry, plus `dead` (true) once the pawn is dead and
`lastHitBy` (the hitter's EOS id) once something has hit it, each of the
two present only when set (decision D19, 2026-09-07), under the sampler's
existing freshness gates (class pointer intact, position finite and in
bounds; a freed pawn is omitted). No `h` or `team`: the drone's health
changes only at death, and team derives from the owner. The killer of a
drone is the `lastHitBy` of the first sample carrying `dead`, exact to a
quarter second — on 2026-09-07 a second shooter moved the pointer 1.1 s
after a kill, inside the full frame's one-second gap. Measured cost
~115 B of raw JSON per drone per sample while it lives, about 50 B more
per sample once hit (estimated, not measured) for the minute or so a
wreck lingers, ~28 KB on disk
per ten-minute flight, three to four small reads per drone per sample
plus one bool and one pointer chain. Touch points:
`possample.SampledEntities` / `sample_positions`, the position-frame key,
the viewer's reconstructor, the schema register.

## 8. Doctor coverage

Every read above is reflection-resolved, so each class gains a
`required_reflection_names()` row (type, meta-class, optional?,
[property names]); no hardcoded offset is introduced, so
`hardcoded_offset_tables()` gains nothing. The player-state identity
fields of §2 ride the reader's existing identity read and its existing
doctor rows. The register takes exact
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
| `SQCommanderActionDataArray` (the type of `CommandIntervals`) and `CommanderNomineeArray` (of `NomineeStatus`), both reflected live 2026-09-07 | ScriptStruct | no | `Items` (the element array; the inner struct's reflected size is the stride) |
| `CommanderVoteNominee` | ScriptStruct | no | `NomineeState`, `VoteCount` |
| `CommanderCategory` | ScriptStruct | no | `Name`, `CooldownDuration` |
| the `CommandAction_*` classes' common base — its name taken from reflection at implementation (the CDOs load only when a claim resolves; none was loaded on the 09-05 layer nor on the idle 2026-09-07 one); until it is named, the rows are the concrete configs `CommandAction_Drone_C` and `CommandAction_Mortar_Barrage_INS_C`, optional content classes that load at a claim | Class | no | `CategoryId`, `EnrouteDuration`, `ActiveDuration`, `CooldownDuration`, `DisplayName` |
| `SQFlyingDrone` | Class | no | `PlayerState`, `LastHitBy` (inherited from `Pawn`; resolved on an idle server by the 09-04 self-test, Misc `command-probe-2026-09-05/drone_track.pre-0905-selftest.jsonl`) |
| `Controller` | Class | no | `PlayerState` — the hop from a drone's `LastHitBy` to the shooter's player state (§7). The reader's existing controller read is a reflection-first, doctor-checked offset on `SQPlayerController` (reader code, `sqreader/health.py`), the same field by inheritance; this row names the base class the pawn's pointer is typed as |
| `BP_FlyingDrone_C` | Class | yes — content, loads with a layer that has it | `SQ PC`, `HealthComponent`, `Dead`, `Command Action` |
| `BP_FlyingDrone_Recoverable_C` | Class | yes — content | `BatteryLifetimeMax` |
| `HealthComponent_C` | Class | yes — content | `Health`, `Max Health` |
| `BP_MapMarker_CommandMaster_C` | Class | yes — content, loaded on an idle server on 09-04 and 09-05 | `Distance`, `AddDistance`, `Action` |
| `BP_MapMarker_DirectorMaster_C` | Class | yes — content | `Distance` |
| `SQCommandActor` | Class | no | `Distance`, `Team`, `DamageInstigatorController`, `Action` — the common fields, declared on the native base (reflected live 2026-09-07); membership is a subclass test on this class |
| `BP_CommandActor_C` | Class | yes — content, loaded on an idle server (Sanxian Seed v1, 2026-09-07) | `Action Destroyed`, `Destroy Delay after Action Destroyed` |
| `BP_CommandActor_ArtilleryBase_C` | Class | yes — content, loaded on an idle server (2026-09-07) | the ten artillery fields of §6: `Origin Location`, `target location`, `Max Drop Radius`, `Pre Warning Shells`, `Pre Warning Delay`, `Shells Per Barrage`, `Barrage Count`, `Current Prewarning Shells`, `Current Barrage`, `Projectile` |
| `BP_CommandActor_FA18_Rockets_Strafe_USMC_C` (archived 08-30), `BP_CommandActor_SU25_Bomb_Strafe_C` (09-02), `BP_CommandActor_FA18_Strafe_C`, `BP_CommandActor_A10_Strafe_2_C`, `BP_CommandActor_SU25_Rockets_Strafe_C`, `BP_CommandActor_FA18_Rockets_Strafe_C` (2026-09-07) | Class | yes — content, exist only during a call | the strike family's `CurrentShotsMade`, `MaxShots`, `Spline Distance`, `Origin Location`; a strike parent, if reflection shows one at a call, replaces these rows |
| `BP_CommandActor_Drone_C` (archived 09-02) | Class | yes — content, exists only during a call | `Health`, `SQ PC` |

## 9. Viewer rules (interpretation; nothing here is recorded)

Each rule names the fields it reads. Where a rule rests on a test, the
test is cited by tracker id; the fields do not change when it runs.

- **Request circle.** 50 m around an approved request, a documented
  game constant (edge-stands on two maps, 49.95 m and 50.20 m,
  2026-08-31); absent from server memory.
- **Pending versus approved, and deletes.** By marker `type`:
  `Command_SLRequest` is pending, `Command_Request` approved. The server
  sweeps markers every ~61 s (T15, 2026-09-07): on approval the approved
  marker appears at once and the pending twin stays until the next
  sweep, so treat a same-squad, same-position pair as one request
  changing state for as long as both exist (up to a minute); a pending
  marker otherwise runs a 61 s fuse and goes at the sweep after it, an
  approved one about 60 s, and either goes at the sweep after a call
  consumes it. A pending marker gone before its 61 s fuse could have run (removals
  at 22, 55 and 57 s on 2026-09-07; no untouched marker ever went before
  61 s) with no approved twin at its spot was deleted by its squad
  leader, and the viewer may say so (decision D20).
- **Asset shapes.** From `type`, `distance`, `addDistance`, `yaw`:
  `CommandRadius` and `CommandRadius_Friendly` a circle of radius
  `distance`, plus an outer band of `addDistance` where it is non-zero
  (the mortar, the static barrage); `CommandLine` a run of `distance`
  along `yaw`; `CommandPath` a path of `distance` along `yaw` with a
  scatter band of `addDistance`; `CommandLineRadius` two aim points, at
  0 and `distance` along `yaw`.
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
  spans `votingTimeSec` and the seat is a per-frame field, so nothing
  falls between frames; `vote.endsGameTime` and
  `vote.cooldownEndsGameTime` are end times. A step-down clears the seat
  and adds no cooldown, but the last vote's cooldown still refuses a
  fresh claim until it ends (2026-09-07); a team switch behaves as a
  step-down (09-02).
- **Ready-in arithmetic.** Per action: effective duration = `enrouteSec`
  + `activeSec` + `cooldownSec`; ready = `createdGameTime` + effective.
  Per category: ready = `lastUseGameTime` + `intervalSec`. An asset is
  callable at the later of the two, less the frame's `worldTimeSec`. When
  `destroyedDuringActive` flips true the stamps do not move: a UAV and a
  drone destroyed on 2026-09-07 kept `createdGameTime`, and the UI's
  ready-in for the UAV matched the unchanged arithmetic, not destruction
  + `cooldownSec`; the 09-02 reading of a restart from destruction was
  not reproduced and is not applied. At a commander change (2026-09-07):
  an entry still cooling has `createdGameTime` moved forward by
  `newCommanderExtensionSec` and `remainingAtChange` written with the
  time it had left; an entry whose own cooldown had run out is
  re-stamped to become ready `newCommanderExtensionSec` after the
  change; the category stamps do not move. At a step-down every entry's
  `remainingAtChange` is written with its time left and nothing else
  moves; at the next claim each entry becomes ready at claim +
  min(`remainingAtChange` + `newCommanderExtensionSec`, `cooldownSec`),
  while the match's first claim starts every entry on its full cooldown
  (09-02, 2026-09-07). The category gate holds across all of it:
  confirmed directly on 2026-09-07 (a strike refused with 15:00 after an
  artillery call) and untouched by a commander change.
- **Artillery timeline.** From the call: the guns open at
  `createdGameTime` + `enrouteSec` (the first warning shell landed at
  +59.7 s on a 60 s enroute, 08-30 creep); the main barrage opens when
  `currentPrewarningShells` reaches `preWarningShells`, plus
  `preWarningDelaySec` (observed 12.1 s after the second warning shell);
  barrages then advance `currentBarrage` at the game's own interval, which
  is not recorded, roughly every six to seven seconds on the 08-30 creep.
  The mortar has no warning phase: `preWarningShells` and
  `preWarningDelaySec` read 0, `currentPrewarningShells` and
  `currentBarrage` both reach 1 at `enrouteSec`, and eight barrages of
  ten follow every eight to nine seconds (2026-09-07); its
  `maxDropRadius` reads 1.0 and its footprint is the marker's `distance`.
- **Shoot-downs.** A command actor whose `actionDestroyed` reads true in
  a full frame while the actor still exists was cut short — shot down:
  a natural end removes the actor without any frame reading the flag
  (two natural ends and two kills, 2026-09-07), and the team entry's
  `destroyedDuringActive` says the same. Who did it is not recorded
  (D18). The drone's call actor never sets the flag; its shoot-down is
  the pawn's `dead` and `lastHitByEosId` (§7).
- **Actions enabled.** `commander.actionsEnabled` is the commander's
  presence in a command zone, both ways (walked out and back in,
  2026-09-07), and the viewer may say so.
- **Drones.** Stop drawing at `dead`. Team = the team of `ownerEosId`'s
  player in the same frame. Remaining flight time = the `worldTimeSec`
  of the first frame the `id` appears + `batteryLifetimeMax` (recon) or
  the calling action's `activeSec` (commander) − now. A new `id` whose
  `ownerEosId` and team match one that just vanished is the same kit
  redeployed, if continuity is wanted; the recorder never joins them.
  Interpolate at 4 Hz knowing cruise is ~10 m/s (2.5 m per sample). The
  killer is the `lastHitBy` of the first 4 Hz sample carrying `dead`,
  the full frame's `lastHitByEosId` being the same value at lower
  resolution (§7). The drone's call actor draws nothing: its position is
  meaningless and its life outruns the pawn's.
- **Asset display names.** Each action entry carries the config's own
  `displayName` (decision D15; eleven read 2026-09-07). SquadCalc's
  per-asset table, which agreed with the config values (2026-09-04), is
  no longer needed.

## 10. Deliberately not recorded

Two lists. First, the choices made when the contract was agreed:
`bDoubleCaptureSpeed` and `bCommandActionAttempted` (never left 0
across 21,000 rows); any "ready in" or remaining-time number; any
event line; any rule; the request circle radius; the bomb map circles;
any last-damager for a command actor (none exists in memory); the recon
launcher (no deployable exists — the launcher is the kit item in the
soldier's inventory); the action configs' `Description` (D15) and
placement bounds (D16); the UAV's and strike aircraft's `Health` and
`Dead_0` and the strike actor's `HealthComponent` (D18 — none moved
through a kill); the commander drone's spawner deployable and drone item
(`BP_Deployable_DroneSpawner_C`, `BP_Deployable_DroneItem_C`: spawn
plumbing that comes with the call and goes 68 s later, 2026-09-07).

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
| `SQCommanderState` | `VoteCooldownTimeSeconds` (Int) | same-named as the manager's rule (§4), unread |
| ″ | `bCommandActionAttempted` (Bool) | never left 0 across 21,000 rows (09-04) |
| ″ | `bDoubleCaptureSpeed` (Bool) | never left 0 across 21,000 rows (09-04) |
| ″ | `MinimumSquadSizeForVoting` (Int) | same-named as the manager's rule (§4), unread |
| ″ | `MinimumSquadsRequiredForVoting` (Int) | same-named as the manager's rule (§4), unread |
| ″ | `VotingTimeSeconds` (Int) | same-named as the manager's rule (§4), unread |
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
| every command and director marker class (the same ten on all nine) | `Team` (Enum) | already recorded by the existing marker read |
| ″ | `MapIcon` (Object) | cosmetic or replication plumbing |
| ″ | `StateObject` (Object) | cosmetic or replication plumbing |
| ″ | `bReplicateOwnerState` (Bool) | cosmetic or replication plumbing |
| ″ | `OwnerPlayerState` (Object) | already recorded by the existing marker read |
| ″ | `Squad` (Int) | already recorded by the existing marker read |
| ″ | `FireTeamId` (Int) | already recorded by the existing marker read |
| ″ | `PlacementEmote` (Enum) | cosmetic or replication plumbing |
| ″ | `DefaultSceneRoot` (Object) | cosmetic or replication plumbing |
| ″ | `DefaultTint` (Struct) | cosmetic or replication plumbing |
| ″ | `Request` (Bool) | reads 1 on both request classes (08-31); the class name is the discriminator (§5) |
| every command actor | `Destroy Delay after Action Destroyed` (Double) | the linger is observed from the actor's presence after `actionDestroyed`, not from its config |
| both artillery actors (creep 08-30, mortar 09-02: identical) | `Arrow` (Object) | engine or visual component |
| ″ | `DefaultSceneRoot` (Object) | engine or visual component |
| ″ | `Edge Only` (Bool) | placement config |
| ″ | `Barrage Interval` (Struct) | a timer handle, not a time — decision D14 (2026-09-05) took the delay and the pre-warning counter and left this |
| ″ | `First Barrage Height Variance` (Double) | spawn-height scatter with no display use — decision D14 (2026-09-05) took the delay and the pre-warning counter and left this |
| ″ | `Main Barrage Height Variance` (Double) | spawn-height scatter with no display use — decision D14 (2026-09-05) took the delay and the pre-warning counter and left this |
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
| ″ | `FA18_pylon_paveway` (Object) — the two F/A-18 rocket classes | engine or visual component |
| ″ | `FA18_pylon_apkws` (Object) — the two F/A-18 rocket classes | engine or visual component |
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
| the action configs (three CDOs archived 09-02, eleven on 2026-09-07) | `Description` (Str) | the game's own descriptive sentence — not recorded (D15; read 2026-09-07) |
| ″ | `Texture` (Object) | UI: icon, tint, widget, sounds |
| ″ | `Tint` (Struct) | UI: icon, tint, widget, sounds |
| ″ | `CommandActor` (Class) | reverse join from the action to its actor class — the marker's own pointer is the recorded side (D13); every config's joins matched the classes that spawned, 2026-09-07 |
| ″ | `ControlWidget` (Class) | UI: icon, tint, widget, sounds |
| ″ | `IconAngleOffset` (Float) | UI: icon, tint, widget, sounds |
| ″ | `MaxAngleFromBase` (Float) | placement bounds — not recorded by decision D16 (2026-09-05): where an asset was placed matters, not where it could have been |
| ″ | `CreateMapMarker` (Bool) | config flag with no agreed use |
| ″ | `bAllowedInVehicle` (Bool) | config flag with no agreed use |
| ″ | `bIgnoreActionEnabled` (Bool) | config flag with no agreed use |
| ″ | `MapMarkerClass` (Class) | reverse join from the action to its marker class — the marker's own pointer is the recorded side (D13) |
| ″ | `CommanderActionSoundsList` (Struct) | UI: icon, tint, widget, sounds |
| ″ | `MinimumDistance` (Float) — every config but `CommandAction_Drone_C` (the 09-02 table; eleven layouts 2026-09-07) | placement bounds — not recorded by decision D16 (2026-09-05): where an asset was placed matters, not where it could have been |
| ″ | `MaximumDistance` (Float) — every config but `CommandAction_Drone_C` (the 09-02 table; eleven layouts 2026-09-07) | placement bounds — not recorded by decision D16 (2026-09-05): where an asset was placed matters, not where it could have been |

## 11. Acceptance

Tracker T8: a six-player run with the command-assets probe as oracle,
after W18 lands, comparing recorded values against the probe at the same
instants, the doctor clean with the rows of §8, the parity harness
green, and the viewer drawing the shapes, actors and drones of §9. The
observations this document rests on — the marker `Action` pointer (T12),
the action configs' strings (T13), the commander drone and its call actor
(T14), the request markers' lifecycle (T15), the commander branches and
the mortar's fire plan (T9) — ran on 2026-09-07, before this text was
final; T8 verifies and discovers nothing.

## 12. How this document was checked, and how to check it again

A fresh session with no memory of the work reviewed the first draft on
2026-09-05 against the six questions below and returned 23 corrections,
all applied; a second pass the same day found the creep and UAV actor
layouts already archived from 08-30, which settled two of them outright.
The 2026-09-07 play session (journal "2026-09-07") amended §3 and
§5–§10 with observed values and rules. A second fresh-session review of
the amended text on 2026-09-07 returned 18 corrections, all applied
after verification against the archives — the names-table tallies had
been wrong since 09-05, the FastArray struct types lacked doctor rows,
the list in question 3 was stale — plus sharper wording for eight claims
it could not trace. The questions stand for the next review.

1. Does every row of the journal's four "Agreed capture" sections
   (decisions 2, 5, 6 and 7; decision 3 is the paragraph inside decision
   2's section) and of the drones doc's "Verified in memory" appear here
   with the same memory source and the same meaning?
2. Does every field here trace to a dated, verified statement in the
   journal or the drones doc, or to an archived layout — nothing
   introduced without evidence?
3. Where the journal disagrees with itself, does this document take the
   later statement? The known disagreements and the side taken: cooldown
   "anchored to the call" (09-02) versus restarting from destruction
   (09-04) versus stamps that do not move on a destroyed UAV and drone,
   the UI matching the unchanged arithmetic (the 09-07 reading, §3 and
   §9); action config values "once per entry" versus
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
   table) versus the manager (09-04) (the manager, §4). The 2026-09-07 session added its own, each taken
   on the 09-07 side: the vote stamps as open times versus end times
   (end times, §3, the fields renamed); approval destroying the pending
   twin within a tick versus the sweep removing it up to a minute later
   (the sweep, §9); a delete not reaching the server versus reaching it
   (reaches it, §9, D20); a consumed request despawning at once versus
   at the next sweep (the sweep, §9); `Action Destroyed` as "the call
   has ended" versus set only when a call is cut short (cut short, §6
   and §9); the seat claimable at once after a step-down versus the last
   vote's cooldown still refusing a claim (the cooldown, §9); the call
   actor's `SQ PC` as the pilot versus the commander (the commander,
   §6); the strike and UAV `health`/`dead` versus none (none, D18);
   nominees behind a `Content` wrapper versus fields directly on the
   32-byte item (no wrapper, §3, the 09-05 struct); the vote and
   cooldown sub-objects emitted while open or active versus every frame
   (every frame, §3, for §4's seek-safety reason). The journal's
   "explicit `null` when the seat is empty" is kept and generalised into
   the two-way rule of §2, "Absent reads".
4. Does every memory name here resolve by reflection? Test T11
   (`scripts/probes/spec_names_check.py`) answers this mechanically — live
   for loaded classes, from the archived layouts for per-call classes — and
   on 2026-09-05 resolved all 157 names (§13). The one name this document takes
   from reflection at implementation, and says so where it uses it: the
   `CommandAction_*` common base, whose CDOs load only when a claim
   resolves (its five properties, `DisplayName` included, resolved on
   the three CDOs archived 09-02 and the eleven archived 2026-09-07);
   the two FastArray struct types and the command actors' base classes
   were reflected live on 2026-09-07 (§8, §13).
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
57 on the box for 18 classes, 131 from the archives for 19: five classes
checked both ways, thirteen live only (the native classes, the four
structs, both marker masters, the recon drone and the health component),
fourteen archive only (corrected at the 2026-09-07 review; the 09-05 text
said eight and one). On 2026-09-07
the session's 48 layouts re-resolved 115 of them for 17 classes
(`--archive` mode), and the classes marked 2026-09-07 below were added
from that archive with the names this document reads on them,
`DisplayName` included; `Health` and `Dead_0` left the strike and UAV
rows with decision D18. The two
artillery fields decision D14 added afterwards, `Pre Warning Delay` and
`Current Prewarning Shells`, resolved in the same run as part of the
artillery actors' full layouts (they appeared in §10's enumeration before
they were promoted).

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
| `BP_CommandActor_Artillery_Creep_C` | archive, 08-30 | `Distance` Float; `Team` Int; `DamageInstigatorController` WeakObject; `Action` Class; `Action Destroyed` Bool; `Destroy Delay after Action Destroyed` Double; `Origin Location` Struct; `target location` Struct; `Max Drop Radius` Double; `Pre Warning Shells` Int; `Pre Warning Delay` Double; `Shells Per Barrage` Int; `Barrage Count` Int; `Current Prewarning Shells` Int; `Current Barrage` Int; `Projectile` Class |
| `BP_CommandActor_Mortar_Radius_C` | archive, 09-02 | `Distance` Float; `Team` Int; `DamageInstigatorController` WeakObject; `Action` Class; `Action Destroyed` Bool; `Destroy Delay after Action Destroyed` Double; `Origin Location` Struct; `target location` Struct; `Max Drop Radius` Double; `Pre Warning Shells` Int; `Pre Warning Delay` Double; `Shells Per Barrage` Int; `Barrage Count` Int; `Current Prewarning Shells` Int; `Current Barrage` Int; `Projectile` Class |
| `BP_CommandActor_FA18_Rockets_Strafe_USMC_C` | archive, 08-30 (identical 09-02 and 2026-09-07) | `Distance` Float; `Team` Int; `DamageInstigatorController` WeakObject; `Action` Class; `Action Destroyed` Bool; `Destroy Delay after Action Destroyed` Double; `CurrentShotsMade` Int; `MaxShots` Int; `Spline Distance` Double; `Origin Location` Struct |
| `BP_CommandActor_SU25_Bomb_Strafe_C` | archive, 09-02 | `Distance` Float; `Team` Int; `DamageInstigatorController` WeakObject; `Action` Class; `Action Destroyed` Bool; `Destroy Delay after Action Destroyed` Double; `CurrentShotsMade` Int; `MaxShots` Int; `Spline Distance` Double; `Origin Location` Struct |
| `BP_CommandActor_UAV_MQ9_C` | archive, 08-30 | `Distance` Float; `Team` Int; `DamageInstigatorController` WeakObject; `Action` Class; `Action Destroyed` Bool; `Destroy Delay after Action Destroyed` Double |
| `BP_CommandActor_Drone_C` | archive, 09-02 | `Distance` Float; `Team` Int; `DamageInstigatorController` WeakObject; `Action` Class; `Action Destroyed` Bool; `Destroy Delay after Action Destroyed` Double; `Health` Double; `SQ PC` Object |
| `CommandAction_Drone_C` | archive, 09-02 | `CategoryId` Byte; `EnrouteDuration` Float; `ActiveDuration` Float; `CooldownDuration` Float; `DisplayName` Str |
| `CommandAction_Mortar_Barrage_INS_C` | archive, 09-02 | `CategoryId` Byte; `EnrouteDuration` Float; `ActiveDuration` Float; `CooldownDuration` Float; `DisplayName` Str |
| `CommandAction_Mortar_Barrage_IMF_C` | archive, 09-02 | `CategoryId` Byte; `EnrouteDuration` Float; `ActiveDuration` Float; `CooldownDuration` Float; `DisplayName` Str |
| `BP_CommandActor_Artillery_Radius_C` | archive, 2026-09-07 | `Distance` Float; `Team` Int; `DamageInstigatorController` WeakObject; `Action` Class; `Action Destroyed` Bool; `Destroy Delay after Action Destroyed` Double; `Origin Location` Struct; `target location` Struct; `Max Drop Radius` Double; `Pre Warning Shells` Int; `Pre Warning Delay` Double; `Shells Per Barrage` Int; `Barrage Count` Int; `Current Prewarning Shells` Int; `Current Barrage` Int; `Projectile` Class |
| `BP_CommandActor_FA18_Strafe_C` | archive, 2026-09-07 | `Distance` Float; `Team` Int; `DamageInstigatorController` WeakObject; `Action` Class; `Action Destroyed` Bool; `Destroy Delay after Action Destroyed` Double; `CurrentShotsMade` Int; `MaxShots` Int; `Spline Distance` Double; `Origin Location` Struct |
| `BP_CommandActor_A10_Strafe_2_C` | archive, 2026-09-07 | `Distance` Float; `Team` Int; `DamageInstigatorController` WeakObject; `Action` Class; `Action Destroyed` Bool; `Destroy Delay after Action Destroyed` Double; `CurrentShotsMade` Int; `MaxShots` Int; `Spline Distance` Double; `Origin Location` Struct |
| `BP_CommandActor_SU25_Rockets_Strafe_C` | archive, 2026-09-07 | `Distance` Float; `Team` Int; `DamageInstigatorController` WeakObject; `Action` Class; `Action Destroyed` Bool; `Destroy Delay after Action Destroyed` Double; `CurrentShotsMade` Int; `MaxShots` Int; `Spline Distance` Double; `Origin Location` Struct |
| `BP_CommandActor_FA18_Rockets_Strafe_C` | archive, 2026-09-07 | `Distance` Float; `Team` Int; `DamageInstigatorController` WeakObject; `Action` Class; `Action Destroyed` Bool; `Destroy Delay after Action Destroyed` Double; `CurrentShotsMade` Int; `MaxShots` Int; `Spline Distance` Double; `Origin Location` Struct |
| `CommandAction_UAV_MQ9_USMC_C` | archive, 2026-09-07 | `CategoryId` Byte; `EnrouteDuration` Float; `ActiveDuration` Float; `CooldownDuration` Float; `DisplayName` Str |
| `CommandAction_FA18CASStrafe_Rockets_USMC_C` | archive, 2026-09-07 | `CategoryId` Byte; `EnrouteDuration` Float; `ActiveDuration` Float; `CooldownDuration` Float; `DisplayName` Str |
| `CommandAction_FA18CASStrafe_Rockets_C` | archive, 2026-09-07 | `CategoryId` Byte; `EnrouteDuration` Float; `ActiveDuration` Float; `CooldownDuration` Float; `DisplayName` Str |
| `CommandAction_FA18CASStrafe_C` | archive, 2026-09-07 | `CategoryId` Byte; `EnrouteDuration` Float; `ActiveDuration` Float; `CooldownDuration` Float; `DisplayName` Str |
| `CommandAction_A10CASStrafe_C` | archive, 2026-09-07 | `CategoryId` Byte; `EnrouteDuration` Float; `ActiveDuration` Float; `CooldownDuration` Float; `DisplayName` Str |
| `CommandAction_Artillery_Creep_USMC_C` | archive, 2026-09-07 | `CategoryId` Byte; `EnrouteDuration` Float; `ActiveDuration` Float; `CooldownDuration` Float; `DisplayName` Str |
| `CommandAction_Artillery_Barrage_USMC_C` | archive, 2026-09-07 | `CategoryId` Byte; `EnrouteDuration` Float; `ActiveDuration` Float; `CooldownDuration` Float; `DisplayName` Str |
| `SQCommandActor` | live, 2026-09-07 (Sanxian Seed v1, idle) | `Distance` Float; `Team` Int; `DamageInstigatorController` WeakObject; `Action` Class |
| `BP_CommandActor_C` | live, 2026-09-07 | `Action Destroyed` Bool; `Destroy Delay after Action Destroyed` Double |
| `BP_CommandActor_ArtilleryBase_C` | live, 2026-09-07 | `Origin Location` Struct; `target location` Struct; `Max Drop Radius` Double; `Pre Warning Shells` Int; `Pre Warning Delay` Double; `Shells Per Barrage` Int; `Barrage Count` Int; `Current Prewarning Shells` Int; `Current Barrage` Int; `Projectile` Class |
| `SQCommanderActionDataArray`, `CommanderNomineeArray` | live, 2026-09-07 | `Items` Array |
