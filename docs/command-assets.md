# Command assets — exploration findings

Status: **exploration COMPLETE — every item that gates the recorder or
the viewer's correctness is closed (one cosmetic measurement, B2,
remains); implementation planning can begin** · live sessions on the
test server 2026-08-30/31
(solo dry-run, a 5-player commander session, both radius measurements),
2026-09-02 (a second 5-player session across two layers and three
factions: every remaining test on the checklist) and 2026-09-03 (two
precision-bombing calls with volunteers inside the blast area, closing the
last item from the game's own damage records), plus offline decodes of
the archived captures.
Everything below is live-verified against Squad v10.x unless marked open.
Offsets quoted are the values verified on the day - any implementation
resolves them by reflection name, never by constant (the 2026-08-31 Squad
update moved every quoted constant and reflection absorbed all of it; the
09-02 session found `CurrentCommander` moved again, 0x5b8 -> 0x598, caught
only by re-deriving before the session). Player names are deliberately
absent from this document.

## Request markers (SL -> commander requests)

The lifecycle is an actor swap between two blueprint twins:

- SL places `BP_MapMarker_Command_SLRequest_C` (pending, SL-only).
- SL approval destroys it and spawns `BP_MapMarker_Command_Request_C`
  (commander-visible) - the two classes have byte-identical 98-field
  layouts, so **the class name is the only pending/approved discriminator**
  (a `Request` bool at +0x330 reads 1 on both; `Distance`/`AddDistance`
  read 0.0 on request markers - but are NOT dead: on the asset-geometry
  markers they carry the footprint, see "Per-call actors"; the icon
  component's `Size` is 32.0 - UI pixels).
  There is a ~1-tick coexistence window at the swap (the source of the
  brief disappear/reappear seen in the replay viewer).
- Fuses, measured: a pending request lives **61 s**; an unused approved
  request also lives ~60 s. A request consumed by an asset call zeroes its
  position for a frame and despawns.
- **SL-side delete does not destroy the server actor** - the deleter's
  client hides it, the server runs out the fuse and other players keep
  seeing it (measured: deleted at ~30 s, actor lived 57.6 s). So
  deleted-vs-expired is server-indistinguishable; recordings show the
  server truth. Known game-side behavior - do not infer a delete.
- `OwnerPlayerState` (+0x2e0) verified live. The recording already carries
  every marker's `type`, position, team, squad and `ownerPlayerStateAddr`,
  which joins against `player._addr` in the same frame.

**Viewer implication (needs no format change, works on historic
recordings): pending vs approved icons/labels, requester attribution, and
bridging the swap blip by treating a same-squad same-position class change
as one request changing state.**

## The request circle (radius)

The circular area around an approved request in which the commander aims
the asset. Proven **absent from server memory** - checked the marker
actors, the map-icon component, the category configs (FText name/desc
blobs) and the action configs; nothing encodes it. It is client UI.

**MEASURED AND CLOSED (R1+R2, 2026-08-31): radius = 50 m, constant across
maps.** Two edge-stands - soldier placed on the circle's drawn edge,
marker-to-soldier distance read from memory - measured 49.95 m on
Al Basrah and 50.20 m on Fool's Road: the design value is plainly 50 m
(5000 cm), and it does NOT scale with the map (unlike FOB radii). The
100 m diameter exactly fills one grid subdivision, confirming the
standard 300 m grid; the earlier 26.4 m creep-origin datum was eyeball
placement INSIDE the circle, not evidence about its size. The viewer
draws a 50 m circle around approved requests as a documented game
constant - nothing to record.

## Commander state (`SQCommanderState`, one per team, always present)

Key reflected fields (past the AActor boilerplate):

| Field | Offset | Verified meaning |
|---|---|---|
| `bVoteInProgress` | +0x2b8 | vote lifecycle flag |
| `CommanderVoteTimestamp` / `CommanderVoteTimer` | +0x2bc / +0x2c0 | vote timing |
| vote-cooldown block | +0x2c4..0x2d0 | re-vote gating |
| `bCommanderIsActive` | +0x2d4 | **"commander system enabled", NOT "claimed"** - read 1 on both teams while team 2 had no commander |
| `CommanderCategories` | +0x2e8 | TArray, stride 0x18; per-element interval float at elem+0x10 (600.0 / 900.0 s seen) |
| `MinimumSquadSizeForVoting` / `MinimumSquadsRequiredForVoting` | +0x2f8 / +0x2fc | the claim requirements |
| `CommandIntervals` | +0x308 | FastArraySerializer; `Items` at absolute +0x410 |
| `LastCategoryGameTime` | +0x420 | TArray, empty until first use, then per-category game-time of last call - **the cooldown state** (remaining = interval − (gameTime − lastUse)) |
| `TeamCommands` | +0x430 | per-team commands object |
| `NomineeStatus` | +0x438 | FastArraySerializer; `Items` at absolute +0x540. Entry DECODED offline from the captured vote (2026-08-31): nominee's `SQPlayerState*` at entry+0x10, **live vote tally (i32) at entry+0x18** — observed 1 at vote start (self-vote), 2 when the SL voted ~4 s in, with the array replication key ticking in lockstep. Voter IDENTITIES are absent from the first 32 bytes; whether they hide deeper needs a wider slice (R4a). Entry persists after resolution |
| `CurrentCommander` | +0x5b8 (08-30 build) / +0x598 (09-02 build) | -> the commander's `SQPlayerState`; null when unclaimed - **the authoritative claimed-commander source**. Re-verified live 09-02 resolving both teams' commanders by name |

**Vote lifecycle, decoded from the captured window (2026-08-31)**: a
commander vote opens a **60-second window** — `bVoteInProgress` flips to 1,
`CommanderVoteTimestamp` stamps the game-time, and `CommanderVoteTimer`
counts down from 60 once per second (a per-tick recordable countdown).
Resolution lands exactly at timer expiry: `bVoteInProgress` back to 0,
timer to 0, and `CurrentCommander` set in the same instant. The whole
lifecycle — start, live tally per nominee, countdown, resolution — is
per-tick state in one actor.

**Confirmed recorder bug**: the shipped commander-identity read treats
`TeamState.CommanderState` as a PlayerState, reads empty strings off the
actor, and (correctly, per its own guard) emits nothing - verified live
with an active commander whose recording carried only the raw actor
address. **Fix: one hop through `CurrentCommander` before the identity
read** - the hop was verified live (resolved the sitting commander's
name). Bugfix, not a format debate.

Individual SL votes: **closed 09-02.** Three further votes were captured
with 96-byte entry slices (a contested claim, an unopposed enemy claim, and
a replacement vote): the server keeps **per-nominee tallies with precise
timing and no per-voter ballots anywhere in the entry** — the bytes past
each tally are zero. Individual attribution is only ever possible by
correlating tally-increment timestamps with announced voters. Entry
stride was confirmed from a genuine two-nominee pair (a teammate
contesting the claim and losing 2-0 before switching teams to claim the
other side), and the nominee's PlayerState pointer is recognisably present
in each entry.

**Replacement and removal (R4a/R4b, 09-02):** a second SL can start a
replacement vote against a sitting commander; the array carries both
nominees and on resolution `CurrentCommander` swaps A->B in place (observed:
challenger 2, incumbent 0). A voluntary **step-down clears
`CurrentCommander` to null with no vote in progress and no vote cooldown
armed** — the seat is immediately claimable again. A team switch by the
sitting commander behaves as a step-down on the old team.

## Per-asset action configs (`CommandAction_*` blueprint CDOs)

Static per-asset rulebook, read off the class defaults. Reflected fields:
`CategoryId` +0x60, `EnrouteDuration` +0x78, `ActiveDuration` +0x7c,
`CooldownDuration` +0x80, `MaxAngleFromBase` +0x88, `MinimumDistance`
+0x138, `MaximumDistance` +0x13c (plus DisplayName/Description/Texture/
CommandActor/MapMarkerClass pointers).

**Load trigger (R3, 09-02): a faction's `CommandAction_*` classes load when
its commander claim RESOLVES** — 8 CDOs with the irregular faction present
but commanderless, 11 the moment their vote landed. The catalog therefore
fills itself: every faction's first commander claim hands over its
rulebook, and a probe (or the reader) sees the classes appear. Values
swept across three factions so far (USMC, INS/IMF, AFU + generic bases):

| Action | cat | enroute | active | cooldown | minDist | maxDist |
|---|---|---|---|---|---|---|
| UAV MQ9 (USMC) / UAV TB2 (AFU) | 0 | 30 s | 300 s | 600 s | 100 m | 250 m |
| UAV (base) | 0 | 20 s | 300 s | 600 s | 100 m | 250 m |
| Drone (irregular) | 0 | 10 s | **600 s** | 600 s | **no distance fields at all** — commander-piloted, nothing to pre-place |
| F/A-18, A-10, SU-25 gun/rocket strafes | 1 | 15 s | 32 s | 900 s | 20 m | 60 m |
| SU-25 (AFU) / CF-18 **bomb** strafes | 1 | 15 s | 32 s | 900 s | 20 m | **120 m** |
| Artillery creep (USMC / AFU / base) | 1 | 60 s | 60 s | 1800 s | 175 m | 450 m |
| Artillery barrage (USMC / AFU / base) | 1 | 60 s | 60 s | 1800 s | 50 m | 150 m |
| Mortar barrage (INS / IMF) | 1 | 30 s | 60 s | 1200 s | **75 m = 75 m** — fixed, non-resizable footprint |

- **`min/maxDist` is asset geometry, not a placement leash** - verified on
  the creep (placed at max range, the live actor's `Distance` read exactly
  45000) and again on the bomb strafe (aim separation 12000 at max).
- The UAV and the drone need no request marker (player-confirmed);
  strikes and artillery are marker-based.
- Every asset family observed so far self-classifies through the same
  pattern (action CDO + `BP_CommandActor_*` + a geometry marker or a
  pawn). Only three factions have been swept; unseen factions still
  record correctly by design, and their configs join the catalog on their
  first commander claim.

**Cooldowns (R5, 09-02).** Both layers are real and both are captured:
- The **category gate is shared across assets**: calling the strafe
  stamped `LastCategoryGameTime[1]` (= 108343.2 game-seconds, at the exact
  tick the strike marker spawned) and the player observed every other
  cat-1 asset's timer jump at once. `CommanderCategories` carries the
  intervals (600 s cat 0, 900 s cat 1).
- **Cooldowns anchor to the call, not the asset's fate**: the drone was
  shot down a minute after its call and the UI showed ~9 minutes
  remaining — 600 s from the call, unchanged by the death; no redeploy
  within the window.
- **Per-asset state — decoded 2026-09-04 from the 09-02 captures, item
  struct reflected live.** `CommandIntervals` is a FastArray
  (`SQCommanderActionDataArray`) of 40-byte `SQCommandActionDataFASItem`
  entries, one per action the team can call, each wrapping an
  `SQCommandActionData`: `CommandActionData` (the action class, item
  +0x10), `GameTimeAtCreation` (float, +0x18), `CooldownTimeRemaining`
  (float, +0x1c), `IsDestroyedDuringActive` (bool, +0x20). The
  arithmetic, verified to within a second against every captured value:
  an action's **effective cooldown = Enroute + Active + Cooldown** from
  its config (strafe 15+32+900 = 947 s, artillery 60+60+1800 = 1920 s,
  drone 10+600+600 = 1210 s) and **ready_at = GameTimeAtCreation +
  effective**. A call rewrites the item's `GameTimeAtCreation` to the
  call time (the strafe item read 108343.2 = the category stamp; the
  bomb item followed all three Grach calls). `CooldownTimeRemaining` is
  NOT a live countdown: it stays 0 and is written once, at a commander
  change, with the remaining time at that instant (the 09-02 step-down
  wrote 689.5 s on the strafe item and 41.0 s on both artillery items —
  both reproduce from the formula). `IsDestroyedDuringActive` read 1 on
  the drone that was shot down. The "+10 minutes" reproduces exactly: at
  the strafe call the artillery items had 298.5 s left and the category
  gate then read 900 s. Recordable state: the category stamps and
  intervals plus, per item, the action class, `GameTimeAtCreation`,
  `CooldownTimeRemaining` and the destroyed flag; the viewer's "ready in"
  = max(category gate, item gate) − the frame's world clock.
- **New-commander gate — decoded 2026-09-04, matching the
  player-confirmed rule.** Assets are not available when a claim
  resolves: the game creates every action item at the claim with
  `GameTimeAtCreation` back-dated by (Enroute + Active), so each asset
  starts with exactly its `CooldownDuration` left (artillery 30 min,
  strafes 15 min, drone/UAV 10 min — the items read claim − 120 s and
  claim − 47 s on both captured claims: 1920 − 120 = 1800, 947 − 47 =
  900). A **change of commander re-stamps the items**: when the
  challenger won the 09-02 replacement vote, the sitting drone item
  (long since ready) was re-stamped so that 300 s remained — the
  manager's `ActionCooldownExtensionOnNewCommander = 300` (one sample;
  whether an item with more than 300 s left keeps the larger value is
  unobserved). A replacement vote won by the incumbent is
  player-confirmed to change nothing and was not captured; the recorder
  writes the stamps, never the rule. `LastCategoryGameTime` stays empty
  until the first real call — the claim gate lives in the items, not the
  category array. Manager config read live: `VotingTimeSeconds 60`,
  `VoteCooldownTimeSeconds 300` (a real countdown — `VoteCooldownTimer`,
  int at +0x2c8 — runs after every claim), `MinimumSquadSizeForVoting
  2`, `MinimumSquadsRequiredForVoting 3`; `TeamCommands` is the
  faction's action DataTable, not runtime state. **The only countdowns
  anywhere in the commander state are the vote timer and the
  vote-cooldown timer** (int32, once per second): an exhaustive scan of
  every 4-byte field across 2,900-5,300 s of per-second captures, as int
  and as float, found no other.
- **Cross-check against SquadCalc (2026-09-04; `github.com/sh4rkman/SquadCalc`,
  v45.0.2, and its public layer data).** Its commander-asset model is a
  single `delay` per asset in minutes — strike 15, artillery 30, mortar
  20, UAV/drone 10, i.e. exactly the config `CooldownDuration` — used
  both as the initial unavailability label ("Delayed: N min") and as the
  asset's own timer when the user marks it used; marking any non-UAV
  asset used sets every other non-UAV asset to a flat 15 min, and UAV /
  drone assets neither trigger nor receive that. It does not track the
  claim, a commander change, or the enroute/active windows. So it
  independently agrees with the two-layer structure, the own-cooldown
  values, the 15-minute strike/artillery gate, category 0 standing
  apart, and full-cooldown unavailability after a claim; the memory
  model is finer where they differ (effective = enroute + active +
  cooldown; the category gate is the later of the two gates, not a
  blanket 15:00; the 300 s new-commander re-stamp), and every finer
  point reproduces captured values. Its display-name table is a handy
  viewer mapping for the `CommandAction_*` classes.

## Per-call actors (what an asset use spawns)

Every call spawns a `BP_CommandActor_*` plus supporting markers - all
observed live:

- **UAV**: `BP_CommandActor_UAV_MQ9_C`, a controlled commander camera, and
  `BP_MapMarker_CommandRadius_Friendly_C` (the coverage circle marker -
  recorded in the marker stream with its position).
- **F/A-18 strafe**: `BP_CommandActor_FA18_Rockets_Strafe_USMC_C`, an
  airstrike camera, and `BP_MapMarker_CommandLine_C` (the strafe run
  line).
- **Artillery creep**: `BP_CommandActor_Artillery_Creep_C` and
  `BP_MapMarker_CommandPath_C`. The creep actor is the fire plan:
  `Distance` +0x2b8, `Team` +0x2bc, **`DamageInstigatorController` +0x2c0
  (weak ptr - commander kill attribution)**, `MaxDropRadius` +0x300 (7500
  = 75 m scatter), `PreWarningShells` +0x30c (2), `ShellsPerBarrage`
  +0x318 (12), `BarrageCount` +0x31c (7), `CurrentBarrage` +0x344,
  `OriginLocation` +0x348 and `target location` +0x360 (3 x f64 each),
  `Projectile` +0x378. Semantics: the path runs from Origin toward the
  click point (target) for `Distance`; the actor advances along it during
  the active window.
- **The asset markers carry their own geometry** (decoded offline
  2026-08-31 from the captured raws): the marker BP's `Distance` (+0x320,
  f64) and `AddDistance` (+0x338, f64) — 0.0 on request markers — encode
  the footprint on the geometry markers:

  | Marker | Distance | AddDistance |
  |---|---|---|
  | `CommandRadius` (UAV) | 10000 = 100 m circle radius | 0 |
  | `CommandLine` (strafe) | 6000 = 60 m run length | 0 |
  | `CommandPath` (creep) | 45000 = 450 m path length (matches the actor's fire plan exactly) | 7500 = 75 m drop scatter |
  | `CommandRadius_C` (mortar barrage) | 7500 = 75 m fixed footprint | 4500 = 45 m outer danger band |
  | `CommandLineRadius` (precision bombs) | the chosen aim separation (44.75-120 m) | 0 |

  **Four archetypes** — circle, line, path (with a scatter band), and
  point-pair — every one expressed through the same two named, reflected
  properties plus position and facing. No endpoint vectors exist in the
  actor raws, and none are needed. Piloted assets (the drone) spawn no
  footprint marker at all; their geometry is the live pawn.
- **Mortar barrage (irregular factions, 09-02)**: `BP_CommandActor_Mortar_Radius_C`
  (106 fields, same family as the creep actor: `Max Drop Radius`, `Shells
  Per Barrage`, `Barrage Count`...) plus `BP_MapMarker_CommandRadius_C`
  carrying `Distance = 7500` (the config's fixed 75 m footprint) and
  `AddDistance = 4500` (a 45 m outer band — the in-game UI draws two rings:
  the called area and a wider danger band). CDO defaults: 10 shells x 8
  barrages per call.
- **Strike aircraft are actors (09-02)**: the SU-25 command actor is a
  **shootable 1000 HP pawn** flying at 190 m/s (`Flight Speed 19000`) with
  `MaxShots = 2` for the bomb variant; the F/A-18 actor shares the family
  (128-field layouts both). Every CAS call therefore puts a trackable,
  killable aircraft in memory for its ~32 s active window.
- **Grach precision bombing (AFU, 09-02)**: config-wise a strafe-family
  variant (`CommandAction_SU25AFU_CASStrafe_Bombs_C`) whose 20-120 m bounds
  are the allowed **aim separation**. It spawns a fourth marker archetype,
  `BP_MapMarker_CommandLineRadius_C` (`Distance` = the chosen separation:
  44.75, 45.76 and 120.0 m across three calls; `AddDistance` 0), the
  aircraft actor, and **two `BP_Projectile_500lb_Bomb_C` projectiles that
  ARE in the recorded projectile stream** — with `firer` = the commander,
  `explosiveBaseDamage 250`, and full flight paths: a straight glide from
  ~325 m out along the aim bearing, constant sink, `hasImpacted` flipping
  as the actor freezes for ~20 s at its rest point (projectiles ride the
  full frames only — the 4 Hz position frames carry players and vehicles
  by design — but that linger means any full cadence captures the impact
  point). The bomb's own
  config (dumped mid-flight) explains the in-game circles: primary
  `ExplosiveDamageOuterRadius 10000` (100 m, falling to 5 damage) and
  `SecondaryExplosion` inner 1500 / outer 4500 (15 m / 45 m, base 2000)
  — the map's two circles per aim point match that 45 m / 100 m pair by
  ratio (≈0.45 in the 09-03 screenshot), though their absolute radii have
  never been measured from the UI (B2 below). **Observed across five
  calls (three on 09-02, two on 09-03): the two projectiles come to rest
  6 m and 32 m along the aim line from its start, 24-27 m apart,
  regardless of the commanded spread (44.75, 45.76, 75.4 and 120 m) and
  regardless of whether the second aim point lies inside the tactical
  call-in area** (09-03 call 2 had both points inside it, 42 m and 38 m
  from the request marker; call 1 had the far point 72 m outside — same
  result). Both bombs release within ~0.1 s of each other, 225-270 m
  short of the line start at ~140 m altitude and ~172 m/s, glide ~1.7 s
  and impact ~0.1 s apart.

  **The rest points ARE the detonations (B1 closed 09-03).** The victims'
  `LastTakeHitInfo` records carry the game's own radial-damage origins:
  the secondary blast 9 cm horizontally from the first bomb's rest point
  and 1 m above it, the primary blast 0.9 m from it and 10 m above it —
  exactly the config's `SecondaryExplosionDistanceFromImpact 100` and
  `ExplosiveDamageDistanceFromImpactNormal 1000` — and the server log
  shows the radial-damage applications at the two impact ticks. The
  radii are honoured as configured: the 5-point minimum at 97.6 m from
  the primary origin, 354 at 37.5 m inside the secondary band, a parked
  helicopter 18 m from the second bomb destroyed. **The second aim circle
  is never serviced**: the in-game map draws an identical circle pair at
  each aim point, and every bomb falls inside the FIRST pair's inner
  circle. One detail left unexplained: a soldier downed by the first
  blast, 12 m from the second bomb's rest point and beside the helicopter
  it destroyed, recorded no second hit.

  **Rendering rule (adopted 09-03, reviewed against the in-game map).**
  Aim point 1 = the marker's position; aim point 2 = that position plus
  `Distance` along the marker's facing (the facing matched the bombs'
  approach bearing to within a degree); a dashed circle pair at each aim
  point; the two impact points from the projectile rest positions where
  `hasImpacted` set, drawn with the bomb config's radii; the call-in area
  from the request marker (50 m). Provenance, so nobody mistakes the
  picture for a read: position, facing and `Distance` are captured; aim
  point 2 is derived from them; the circle radii are the config's 45 m /
  100 m, *assumed* to be what the map draws (the ratio matches, the
  absolute values are unmeasured). A rival reading — the line ending at
  the far edge of the second inner circle, i.e. the second pair pulled
  back by its own radius — was drawn and rejected: on this call it would
  put the second centre within a metre of bomb 2, but on the two 45 m
  calls it would stack both pairs on top of each other, and their inner
  circles were observed to overlap only slightly.
- **The drone (irregular factions, 09-02) is the one piloted asset**, and
  it behaves like nothing else: the call spawns `BP_CommandActor_Drone_C`,
  a transient spawner + item pair, and `BP_FlyingDrone_C` — a
  **Character-based pawn (178 fields) with the standard possession chain**
  (`PlayerState`, `Controller`, `LastHitBy`), position/yaw readable through
  the normal root transform (flight tracked live at ~40 m per 4 s). It is
  NOT an `SQVehicle`, so it is **invisible in recordings today** despite
  being a ten-minute, pilot-controlled, shootable actor. Possession model:
  de-possessing nulls `PlayerState`, `Controller` AND `PreviousController`
  (an orphaned pawn), re-possessing restores them — so "piloted by" is
  per-tick state with honest gaps, while the persistent owner is the
  command actor's `DamageInstigatorController` (+ `Team`). Possession IS
  logged (`OnPossess` lines with full identity); death is not: the pawn
  despawns on kill, the command actor survives (`Action Destroyed` stays
  0), the cooldown keeps its call-time anchor, and no redeploy is possible
  inside the window. Drones cannot harm players — observation only — so
  there is no drone-kill attribution question; a drone being shot down is
  the attributable event (its `LastHitBy` rides the capture to the last
  tick).
- **Commander kill attribution flows through the existing causer chain
  (R6, 09-02)**: a strafe wound event recorded `attacker` = the calling
  commander with `causerWeapon` = the rocket class, and bombs carry the
  commander as `firer`. The wire needs nothing new for commander kills.
- Command zones (`BP_CommandZone_HAB_C` / `_Vehicle_C`) exist around HABs
  and command vehicles - noted, not explored (parked, R8).
- **Commander actions write no server-log lines** — assets, votes,
  cooldowns, drone death are all memory-only (proven offset-controlled).
  The single exception is drone possession, which logs `OnPossess`.

## Capture gaps found (candidate wire additions - NOT yet proposed/agreed)

1. **Asset uses are only partly visible in recordings today.** Present:
   the geometry markers' type + position, and strike ordnance as
   projectiles (rockets, bombs — with `firer` = the commander) whose kills
   attribute through the existing causer chain. Absent: the geometry
   markers' `Distance`/`AddDistance`/facing (gap 2), the fire-plan detail
   on the command actors (shells, scatter, progress), the strike aircraft
   actors, and — the strongest case — **the piloted drone pawn**: a
   ten-minute pilot-controlled flight with no `SQVehicle` ancestry, so no
   stream carries it. A small tracked-entity addition (class, position,
   yaw, pilot via the possession chain, owner via the command actor) is
   what closes this; the probe captured a complete flight for the cost
   model.
2. **Command markers record no geometry fields today** - `arrowLength`/
   `arrowHeading` come back null for the line/path markers. SOLVED IN
   PRINCIPLE by the offline decode: the geometry lives in the marker's
   own `Distance`/`AddDistance` properties plus its facing - reading
   those for `Command*` markers in the existing marker path (reflected
   names, ~3 reads per command marker, near-zero cost) closes the gap
   without any command-actor stream. The actor stream remains relevant
   only for stats-side detail (shell counts, `CurrentBarrage` progress,
   the commander-attribution pointer).
3. Per-team commander state (identity fix + vote flag + cooldown state)
   and vote/asset events - storage measured ahead of design at
   **+0.25 %/match, ~0.009 ms/tick** (house canonicalized-zstd method,
   ceiling-flavored model), so the cost gate is pre-cleared for whatever
   shape is agreed.
4. The commander-identity **bugfix** (one hop via `CurrentCommander`)
   stands apart from the debate - it repairs an already-shipped field.
5. **Radial-damage origins are readable and unrecorded (09-03).**
   `SQSoldier.LastTakeHitInfo` (`FSQTakeHitInfo`, 464 bytes, fully
   reflected) embeds a `RadialDamageEvent` whose `Origin` (3 x f64, struct
   +0x190 on this build) and `Params` (base/min damage, inner/outer
   radius, falloff; +0x178) the game fills for every explosion hit, with
   `DamageEventClassID` (+0x20: 1000 on the radial hits seen, 1 point, 0
   generic) saying which event block is live. The killfeed read stops at
   the point-damage block today. Emitting `origin` on radial damage
   events would give every grenade, mortar, IED and bomb hit its blast
   location as memory truth — a candidate at ~24 bytes per radial event,
   not proposed yet.

## Open questions — status after the 09-03 session

Closed: **R1/R2** (50 m request circle, map-invariant), **R3** (load
trigger + three factions swept), **R4a** (replacement vote, A->B swap,
tallies-only confirmed), **R4b** (step-down clears to null), **R5**
(category gate real and cross-asset; call-anchored cooldowns), **R6**
(commander kill credit flows through the existing causer chain), **R7**
(drone lifecycle + mortar barrage + precision bombs — the self-classifying
pattern held across two new factions and a novel geometry), and **B1**
(09-03: bombs detonate at the projectile rest points, 6 m and 32 m along
the aim line; the second aim circle is never serviced, inside or outside
the call-in area; proven by the victims' own radial-damage origins — see
the Grach entry above).

| # | Open item | Needs | What it decides | Blocks |
|---|---|---|---|---|
| B2 | The map's bomb-circle radii and the second pair's exact centre are assumed (config 45 m / 100 m, centred on the aim points) | one edge-stand on the second inner circle during any Grach call, read the same way as the 50 m request circle | Whether the viewer's dashed circles match the in-game map exactly | viewer cosmetics only (low importance) |
| R8 | Command zones (`BP_CommandZone_HAB_C` / `_Vehicle_C`) | exploration only | Whether they gate anything display-worthy; parked as a decision, not a blind spot | nothing |

Nothing gates the recorder, and nothing gates the viewer's correctness;
B2 is cosmetic. The capture proposal can be written now.

Offline analysis: **done** — 08-31 decoded the nominee entry, the vote
lifecycle and the marker geometry; 09-02 decoded the cooldown stamps, the
bomb damage model and the three bombing trajectories; 09-03 decoded the
two detonation calls (10 Hz bomb tracks, the take-hit records, the
placements), all recorded above. All three sessions' raw captures
(per-tick instance snapshots, class layouts, log slices, the bomb config
dump, the take-hit struct layout) are archived off-repo.

## Harness notes (for whoever probes next)

- The probe resolves every `SQCommanderState` offset by reflection at
  startup — a field moved between sessions (`CurrentCommander`) and only a
  pre-session re-derive caught it. Never launch a probe on last session's
  offsets.
- Raw-dump priority: `BP_CommandActor_*` must rank with the state actors.
  On a layer dense with command zones and commander-variant vehicles the
  mortar actor's per-tick raws were crowded out of the dump cap, costing
  the direct fire-plan read (recovered from the CDO instead).
- Short-lived blueprint classes (the bomb projectile lives only in flight)
  need a trigger watcher: poll for the class, dump its CDO the instant it
  exists. The bomb config was captured that way mid-flight.
- Restart the probe after a layer change (class addresses churn); it
  survives game-server restarts by re-attaching.
- Detonation questions are answered by the victims, not the projectiles.
  The 09-03 tracker ran at 10 Hz (reflection-resolved `RootComponent`,
  the `bHasImpacted` bool mask, `Distance`/`AddDistance` on markers),
  polled every soldier's `LastTakeHitInfo` timestamp and dumped the whole
  struct on change, and wrote a 1 Hz placement roll-call of every soldier
  — so a null result (nobody hit) reads against the aim geometry and a
  wounded volunteer carries the blast origin. Reflection walks nested
  structs: `read_fstructproperty_struct` + `get_class_layout` two levels
  down gave the `RadialDamageEvent.Origin` offset in one startup pass.
- Put the volunteer inside the band under test, not outside it. The first
  09-03 call had the only volunteer 12 m beyond the outer radius and
  proved nothing by itself; the second, with one soldier 44 m along the
  line, closed the question in a single call.
