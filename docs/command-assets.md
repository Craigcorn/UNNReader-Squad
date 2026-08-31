# Command assets — exploration findings

Status: **exploration paused, findings banked** · live sessions on the test
server 2026-08-30 (solo dry-run + a 5-player commander session, cut short by
an unrelated server crash). Everything below is live-verified against Squad
v10.x unless marked open. Raw probe captures (per-tick instance snapshots,
class layout dumps, the vote window) are archived off-repo for the pending
offline analysis. Offsets quoted are the values verified that day - any
implementation resolves them by reflection name, never by constant.

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

Evidence so far on its size: a creep-barrage start point placed by eye "at
the circle's edge" measured 26.4 m from the marker center; the circle
visually fills about one grid subdivision. Whether that means a ~25 m
radius, and whether it scales per map (the FOB-radius precedent says
per-layer scaling exists in this game), are the open questions tests R1/R2
answer precisely. Until then the viewer treats it as a display constant
with a documented uncertainty, not a recorded fact.

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
| `CurrentCommander` | +0x5b8 | -> the commander's `SQPlayerState`; null when unclaimed - **the authoritative claimed-commander source** |

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

Individual SL votes: the decode settled the core question — the server
keeps **per-nominee tallies with precise timing, not per-voter ballots**
(none in the entry's first 32 bytes). Individual attribution therefore
comes from correlating tally-increment timestamps with announced voters
(the staggered protocol), unless a wider entry slice in R4a reveals a
voter list deeper in the struct.

## Per-asset action configs (`CommandAction_*` blueprint CDOs)

Static per-asset rulebook, read off the class defaults. Reflected fields:
`CategoryId` +0x60, `EnrouteDuration` +0x78, `ActiveDuration` +0x7c,
`CooldownDuration` +0x80, `MaxAngleFromBase` +0x88, `MinimumDistance`
+0x138, `MaximumDistance` +0x13c (plus DisplayName/Description/Texture/
CommandActor/MapMarkerClass pointers).

Values swept on the USMC layer (only the loaded faction's actions exist -
see R3):

| Action | cat | enroute | active | cooldown | minDist | maxDist |
|---|---|---|---|---|---|---|
| UAV MQ9 (USMC) | 0 | 30 s | 300 s | 600 s | 100 m | 250 m |
| UAV (base) | 0 | 20 s | 300 s | 600 s | 100 m | 250 m |
| F/A-18 strafe (all variants) | 1 | 15 s | 32 s | 900 s | 20 m | 60 m |
| A-10 strafe | 1 | 15 s | 32 s | 900 s | 20 m | 60 m |
| Artillery creep (USMC) | 1 | 60 s | 60 s | 1800 s | 175 m | 450 m |
| Artillery barrage (USMC) | 1 | 60 s | 60 s | 1800 s | 50 m | 150 m |

- **`min/maxDist` is asset geometry, not a placement leash** - verified on
  the creep: placed at max range, the live actor's `Distance` read exactly
  45000 (450 m), the config's `MaximumDistance`.
- Category intervals (600/900) and per-action cooldowns coexist and
  disagree for artillery (1800 vs the category's 900) - which gate wins is
  open (test R5, passive observation).
- The UAV needs no request marker (player-confirmed); strikes and
  artillery are marker-based.

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

  Position + facing + these two named, reflected properties fully define
  every asset's footprint — no endpoint vectors exist in the actor raws,
  and none are needed.
- Command zones (`BP_CommandZone_HAB_C` / `_Vehicle_C`) exist around HABs
  and command vehicles - noted, not yet explored.
- **No commander action writes a single server-log line** (proven
  offset-controlled, same method as the medical finding). Memory is the
  only source.

## Capture gaps found (candidate wire additions - NOT yet proposed/agreed)

1. **Asset uses are invisible in recordings today.** The `BP_CommandActor_*`
   actors are not vehicles, markers or projectiles, so nothing of a UAV
   flight or a barrage enters the wire except the radius/line/path
   markers' positions. The full fire-plan geometry (origin/target/length,
   shells, scatter) and the commander attribution live only on the actor.
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

## Outstanding tests (return here)

| # | Test | Needs | Answers |
|---|---|---|---|
| R1 | Edge-stand: place SL request, stand a soldier on the circle's edge, measure marker->soldier distance | solo, 2 min | exact request-circle radius; calibrates the map's real grid size |
| R2 | Same edge-stand on a different layer | solo, 2 min, any visit | whether the circle is constant or map-scaled (the FOB-radius concern) |
| R3 | Other-faction asset sweep: opposing team claims commander (opening the menu likely suffices to load their `CommandAction_*` CDOs), then sweep | one player on the other team | the irregular factions' full asset rulebook |
| R4a | Replacement vote: a second SL votes out the incumbent, votes staggered ~10 s apart and announced. Probe prep: widen the nominee slice (AUX_ELEM_BYTES) to hunt a voter list past entry+0x20 | full squad quorum | contested entries (stride via two nominees; whether voter identity exists deeper); `CurrentCommander` A->B swap |
| R4b | Step-down / disconnect | commander | the clean clear to null |
| R5 | Passive: after using one cat-1 asset, note when the other cat-1 asset shows available | nothing extra | which cooldown gate wins (per-action vs category) |

Offline analysis: **done 2026-08-31** — the nominee entry (identity +0x10,
tally +0x18, no voter list in the sampled bytes), the vote lifecycle
(60 s window, per-second countdown, resolution semantics), and the marker
geometry (`Distance`/`AddDistance` carry every asset's footprint) are all
decoded above. Next: the capture proposal goes to review with the usual
measured costs.
