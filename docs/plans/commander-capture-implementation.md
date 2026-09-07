# Implementation plan: the commander, command-asset and drone capture

Tracker W17 (this plan) and W18 (the implementation). The contract is
`docs/command-assets-spec.md`; this plan never restates what a field
means, and where the two disagree the spec wins. Written 2026-09-07 from
the spec as corrected by its second fresh-session review, against the
code at commit a5b8729. Line numbers are of that commit; the function
names are the durable pointers.

## What this is

Five additive surfaces on the recording, one recorder-side identity fix,
their doctor rows and schema entries, and the viewer rules that read
them:

| Surface | Wire home | Spec |
|---|---|---|
| A — the team's commander block | `teams[].commander`, plus the fix to `commanderName` / `commanderEosId` | §3 |
| B — the server's commander rules | `gameState.commanderRules` | §4 |
| C — geometry and the action pointer on actor markers | `markers[].distance`, `.addDistance`, `.yaw`, `.action` | §5 |
| D — the per-call command actors | `commandActions[]` (new top-level list) | §6 |
| E — the drone pawns | `drones[]` (new top-level list) and the position line's `drones` key | §7 |

Nothing here is inferred on the recorder: every field is a read of a
named memory location resolved by reflection, and the four deliberate
exceptions are listed in spec §1. Every read gains a doctor row (§8).
Every viewer rule (§9) is derived from per-frame state and can be fixed
for every recording at once.

Not in scope: the stats engine (it reads none of these fields), the
platform's packer (see D21 below), the bomb-circle radii (tracker T9 f),
the shoot-down attribution of UAVs and aircraft (D18: unmeasurable), and
any UNN-specific behaviour (this repo is public and stays
upstream-compatible).

## How to work

Read, in this order: `CLAUDE.md` (the hard rules), `CONTRIBUTING.md`
("Reverse-engineered offsets" and the no-guess policy), the spec whole,
`docs/schema.md`, `docs/architecture-notes.md`, then this plan. Open the
journal (`docs/command-assets.md`) only where a spec sentence cites it
and the evidence matters to a code decision.

- Work on a branch off `Replay-Improvements`. One concern per commit,
  subject in the house voice (imperative, story-telling), the
  attribution trailer, and the tracker row of anything whose state
  changes updated in the same commit (`docs/tracker.md`, rules 3 and
  11–13). Changelog lines accumulate under `[Unreleased]` in the house
  narrative voice (cause → consequence, plain hyphens).
- Gates before any push: `python -m pytest`, `python -m ruff check .`,
  `python -m mypy sqreader` (at the configured target; on a machine whose
  numpy stub trips the 3.10 target, `--python-version 3.12` is the
  fallback, never a skip); `cd frontend && npm ci && npm run build` and
  `npm test` whenever `frontend/` changes; `scripts/stats_parity.py`
  against the test-box corpus before the final push, because the
  snapshot changes (CLAUDE.md, "Stats-from-replay parity is an
  invariant") — expected green, since `stats.py` reads none of the new
  keys, and a non-zero exit is a failed gate all the same.
- Emission rules are the spec's: emitted when the read succeeds, omitted
  when it cannot, `null` when the game's own value is empty (§2, "Absent
  reads"), with the one named exception on the 4 Hz line. Never default,
  never carry a value from the previous frame.
- Class default objects (`Default__…`) are never entities; the reader's
  existing walk already skips them by name (`snapshot.py:4218` and
  siblings). The level's template actors that exist for one second at a
  layer load are recorded as read (spec §6).
- Three names are taken from reflection on the box at implementation and
  recorded the same day in three places — the doctor row, spec §8 and
  §13, and the tracker row of W18: the `CommandAction_*` configs' common
  base class, the struct type behind `NomineeStatus`, and a Blueprint
  parent common to the command actors if one exists. No name is
  guessed; if reflection shows none, the spec's per-class rows stand.
- Every hardcoded offset the reader could read through must appear in
  `health.hardcoded_offset_tables()`; this work introduces none, and
  `tests/test_fleet.py::test_every_readable_hardcoded_offset_is_watched`
  fails the suite if one slips in.

## Decisions this plan makes (registered in the tracker for review)

- **D21 — both new lists travel whole.** The viewer's stream decoder
  (`frontend/src/state/replayUnpack.ts`, format 2) index-tracks only
  `players` and `vehicles` (`KEYED`, line 27); every other top-level key
  is diffed generically and needs neither a decoder change nor a version
  bump; the 4 Hz line passes through as `{"p": line}` (lines 109–111).
  The packer that would index-track a new list lives in the private
  platform repo, and changing it means a format bump there, here, and a
  regenerated cross-language fixture. `commandActions` holds a handful
  of entries and `drones` one or two, so the saving is negligible: send
  both whole, like `markers`. Revisit only under tracker D7 if the live
  view ever needs it.
- **Measurement is both builds against the same live state**, never an
  empty box alone (see "Measurement"). A baseline checkout of the
  pre-implementation commit stays on the box beside the live one for
  as long as the comparison is wanted.
- **Every viewer rule ships with a scenario fixture** the reviewer can
  open in the browser without a live match (see Phase 6).

## Where the code is

The map an implementer needs, by surface. Function names are the
pointers; line numbers are of commit a5b8729.

**The full frame** (`sqreader/squad/snapshot.py`):

- `resolve_paths` (1544–1987) resolves every class by name in one
  batched walk (`arr.find_all_by_names`, 1627); required names raise,
  optional names resolve to address 0 and every downstream read guards
  on it. `grab(layout, names)` (1724) turns a reflected layout into an
  offset dict. Struct hops use `struct_layout_for_field` (as
  `psd_offsets`, 1913–1917); struct strides use `read_field_struct` +
  `read_ustruct_header(...).properties_size` (`weapon_group_size`,
  1716–1722). Bool masks: `bool_property_mask` (`ps_bool_masks`,
  1921–1926). Lazy derivation for a class that loads later: the
  capture-zone example in `build_snapshot` (4660–4665).
- `SnapshotPaths` (1380–1541) carries the resolved offsets;
  `SnapshotCaches` (1071–1350) caches by class address (`is_*`
  subclass tests such as `is_healing_item`, 1115–1116; `class_name`;
  `partial_invalidate` on generation change).
- `read_team_state` (2116–2176) builds `teams[]`; the commander identity
  read at 2141–2164 is the W10 defect: it reads the `CommanderState`
  actor as a player state. `read_game_state` (2059–2113) builds
  `gameState`, one `if name in o:` guard per field, `worldTimeSec` at
  2099–2102.
- `read_marker` (2563–2586) builds actor markers from the hardcoded,
  doctor-watched `MARKER_OFFSETS` and the root-component position;
  `type` is the verbatim class name (2569). The FastArray player-placed
  markers are built inline at 4678–4747 and are not touched by this
  work.
- `read_vehicle` (3389–3455) is the model for a per-actor record: each
  sub-read independently fault-tolerant, attached only when truthy;
  `read_root_pos_yaw` (3363) gives position and yaw.
- The new-actor-family checklist, all seven steps or the entity never
  appears: class in `resolve_paths`' optional dict (1561–1626) and on
  `SnapshotPaths` (1380–1541) and in the constructor (1805–1987); an
  `is_*` cache on `SnapshotCaches` (1099–1134); a `_CAT_*` constant
  (4022–4030); a branch in `_classify` (4133–4158); a `KIND_*` in
  `sqreader/squad/walkdelta.py` (49–65); the `_visit` return (4219–4395)
  and the ledger rebuild branch (4442–4484); the reader function, the
  list and the key in the final dict (5017–5049) and `__all__`.
- Weak object pointers resolve through `_resolve_weak_obj` (2979–3005,
  index plus serial check); the medical block (3876–3911) is the model
  for a reflection-only, no-fallback read gated on a subclass test.
- Helpers: `read_fstring` (`sqreader/ue/value.py:80`), `read_ftext`
  (221), `read_fvector` (39), `iter_tarray_pointers` (153),
  `read_fweak_object_ptr` (191); reflection in `sqreader/ue/reflection.py`
  (`get_class_layout` 289, `struct_layout_for_field` 465,
  `read_farrayproperty_inner` 381, `bool_property_mask` 441).

**The position line** (`sqreader/squad/possample.py`, 196 lines):
`SampledEntities` (32–57, frozen dataclass of `(addr, key)` tuples,
`from_snapshot` 42–57), `sample_positions` (126–196) with the gates in
order — class pointer intact (`_class_ok` 69), health in range for
players only, `read_root_pos_yaw`, `_sane_pos` (finite and within
5,000,000 cm) — emitting `{id, x, y, z, h, yaw}` per player and
`{id, x, y, h, yaw, team}` per vehicle; a failing entity is omitted. The
serve loop interleaves lines in `sqreader/cli.py` 1221–1257 (full-frame
slot 1233–1244 takes `frame.entities` from the build worker; position
slot 1245–1256 writes the line to the recorder through
`write_position_frame`).

**The recorder and container**: `sqreader/recorder.py` `_handle_snap`
(414–700) → `_write_line` (732–743); `write_position_frame` (746–765)
is a side channel that touches no tick count. `sqreader/sqrx.py` is
line-agnostic; version 1 does not move for additive keys.

**The doctor** (`sqreader/health.py`): `required_reflection_names()`
(337–360) rows are `(type, meta-class, optional?, [names])`;
`hardcoded_offset_tables()` (77–270) with its exclusion register at
90–136; `check_target_names()` (378–398) unions them into one walk. An
optional row whose class is not loaded is skipped; a loaded class
missing a name is drift (`check_required_names` 522–546). `sqreader
doctor` prints a missing name as `Class.Field` and exits non-zero.
`tests/test_fleet.py` enforces the shapes and the coverage
(`test_required_reflection_names_cover_the_fallbackless_reads` 636–645
is where the new rows are asserted; `test_only_content_loaded_types_are_optional`
415–427 says only Blueprint classes may be optional).

**The schema register** (`docs/schema.md`): the full-frame table at
20–44 (rows for `gameState.commanderRules`, `teams[].commander`,
`commandActions` and `drones` are reserved at 30–31 and 43–44), the
position-line table at 52–59, then one section per capture (the medical
section at 63–99 is the model).

**The viewer** (`frontend/src`): types in `state/types.ts` (`Marker`
305–327, `TeamState` 352–373); per-frame reconstruction in
`state/replayReconstruct.ts` (`reconstructFromPosition` 44–121 splices
players by key and vehicles by id, everything else shared by
reference); drawing in `canvas/draw.ts` (`drawMarkers` 1235–1328
dispatching on `type`, `drawVehicles` 972–1064, `renderScene`
1752–1790 with the layer toggles from `state/viewerStore.ts` 45–78);
marker shape from `canvas/markerGeometry.ts` (`markerShape` 12, the
`arrowLength`/`arrowHeading` rule at 22–32); icons in `canvas/icons.ts`
(`markerIconUrl` 453); the team bar in `ui/TeamBar.tsx` (reads
`teams` at 44–50; the commander fields are typed and rendered nowhere);
the info panel in `ui/InfoPanel.tsx` and `ui/entityInfo.ts`. Tests are
plain node scripts (`*.test.mts`, `scripts/run-tests.mjs`).

**Tests** (`tests/`): `conftest.FakeProcessMemory` (16–63) is a segment
address space whose `try_read` returns `None` off-map, the way the real
class does; `tests/test_possample.py` shows the position-line pattern
(a `SimpleNamespace` of offset dicts, a monkeypatched
`read_root_pos_yaw`, an exact-dict assertion); `tests/test_fleet.py`
the doctor pattern (`_Arr`, `_Prop`, pre-seeded layouts);
`tests/test_recorder_position.py` the recorder pattern;
`tests/test_offset_autoresolve.py` the reflection-fake pattern.

## The phases

Each phase ends with its tests green and one or more commits. Surfaces
can be built in any order after Phase 1; the order below front-loads
the reads other surfaces reuse.

### Phase 0 — baseline, before any code

1. On the box, with the current build and the layer loaded: `sqreader
   profile-build --iters 50` twice (idle server), output saved to the
   Misc folder as the idle baseline. Note the Squad version and the
   commit.
2. Create the baseline checkout on the box: a second clone of this repo
   at the pre-implementation commit, its own `.venv`, editable install,
   owned by `ubuntu` like the live one. It exists only to run
   `profile-build` side by side later; it never runs a service.
3. Write `scripts/frame_size_delta.py`: given a `.sqrx` and a list of
   top-level keys (dotted for nested, e.g. `teams[].commander`,
   `players[].soldier.medical`), re-encode every line with those keys
   stripped, canonical JSON, one zstd frame per line at the recorder's
   level, and report raw and compressed bytes with and without them,
   per full frame and per position line. This is the house method of
   09-05 made repeatable; it is the storage gate's tool and a general
   one. Test it on a fixture recording built with `SqrxWriter`.

Exit: the two baseline files archived, the second checkout present, the
script committed with its test.

### Phase 1 — Surfaces A and B, and the identity fix

Reads, all in `snapshot.py` beside `read_team_state`:

1. `resolve_paths`: layouts for `SQCommanderState` and
   `SQCommanderManager` (native, required rows); struct layouts through
   `struct_layout_for_field` for `CommandIntervals` and `NomineeStatus`
   (their `Items` arrays) and for `CommanderCategories`; the inner
   structs `SQCommandActionDataFASItem` (its `Content`),
   `SQCommandActionData`, `CommanderVoteNominee`, `CommanderCategory`;
   strides from `read_farrayproperty_inner` + `read_ustruct_header`
   for struct arrays and from the element property's type for the float
   array `LastCategoryGameTime` (spec §1). Record the `NomineeStatus`
   struct type's name when reflection shows it.
2. The identity fix (W10): `commanderName` / `commanderEosId` come from
   `SQTeamState.CommanderState` → `SQCommanderState.CurrentCommander` →
   the player state, explicit `null` when `CurrentCommander` reads
   null. Keep `commanderStateAddr`.
3. `teams[].commander` per spec §3: the scalars, the vote block (arrays
   `[]` when empty), `cooldowns.categories[]` with the recorder's one
   own key `i`, `cooldowns.actions[]` with the config values read from
   the action class's default object. The default object is found as
   the object named `Default__<class name>` in the object array
   (`arr.find_all_by_names`), cached per class address and invalidated
   with the caches' generation; the config values (`CategoryId`, the
   three durations, `DisplayName`) are read by name on that class's
   layout, also cached per class. If reflection shows a pointer from
   the class to its default object, prefer it and register it; never a
   hardcoded offset.
4. `gameState.commanderRules` per §4: the six scalars from the first
   live `SQCommanderManager` instance found, class defaults excluded
   (several instances exist and read alike).
5. Failure handling as the spec's §2: a class lacking a field omits it;
   a null pointer gives `null`; an empty array gives `[]`.

Tests (`tests/test_commander_block.py`): a `FakeProcessMemory` image
with two team states, one commander state each (one seated, one empty),
a manager, two nominees, two categories, three action entries whose
config class's default object carries the five values; assert the exact
emitted dicts, the `[]` cases, the `null` seat, the identity fix, and
that a missing name omits its key. A second test asserts the cached
config values survive a generation bump only through re-resolution.

Doctor: rows per spec §8 for `SQCommanderState`, `SQCommanderManager`,
the four inner structs, the two FastArray struct types, the config base
class (its name from reflection; until then the row is written against
the name the box reports, and the spec's §8 updated in the same
commit). `test_fleet.py` gains the coverage assertions.

Schema: rows and a "Commander block" section in `docs/schema.md`
mirroring the spec's field tables (keys, types, since 2026-09-xx, "sent
whole").

Exit: the fixture tests green, the doctor rows asserted, the schema
rows written, the changelog line, the W18 row moved to `implemented
(A, B)`.

### Phase 2 — Surface C, the marker fields

1. In `read_marker`: after the existing hardcoded reads, a reflected
   read of `Distance`, `AddDistance` and `Action` by name on the
   marker's class layout, cached per class address (a `marker_geometry`
   cache on `SnapshotCaches` holding the three offsets or their absence
   per class), emitted only where the class carries the name; `yaw`
   from `read_root_pos_yaw` on the classes that carry `Distance`;
   `action` as the pointed class's name, `null` when the pointer is
   null (spec §5). Never a class-name test.
2. Doctor: optional rows for `BP_MapMarker_CommandMaster_C` (`Distance`,
   `AddDistance`, `Action`) and `BP_MapMarker_DirectorMaster_C`
   (`Distance`), with the observed reason the register demands.
3. Schema: the `markers` row updated, and a short section.

Tests: a fixture with a Command-family marker, a Director-family marker
and a squad-data marker; assert the fields on the first two, their
absence on the third, `action` `null` on a request-class marker and a
class name on a footprint.

Exit: as Phase 1, W18 row `implemented (A–C)`.

### Phase 3 — Surface D, the command actors

1. The seven-step actor family: class membership by a Blueprint parent
   common to the family if reflection shows one, else the
   `BP_CommandActor_` prefix on the class name (the classification is
   cached per class address either way); `KIND_COMMAND_ACTOR`;
   `read_command_actor` producing the common fields of §6 — `id`,
   `class`, `team`, `action`, `callerEosId` (weak pointer →
   `_resolve_weak_obj` → controller → player state), `position` and
   `yaw`, `actionDestroyed`, `distance` — and the family fields by name
   where the class carries them (strike: `shotsMade`, `maxShots`,
   `splineDistance`, `originLocation`; artillery: the ten fire-plan
   fields; the drone call actor: `health`, `ownerEosId`). No `health`
   or `dead` on the strike and UAV families (D18).
2. The `(0, 0, 0)` exclusion from §2 applied to the list; the drone call
   actor's `(0, 0, z)` is not excluded (the viewer ignores it, §9).
3. Doctor: one optional row per archived actor class per §8, or the
   parent's row if reflection shows one. Schema: the `commandActions`
   row and section.

Tests: a fixture with one actor of each family and a class-default
object of one of them (which must not appear); assert each record's
keys and the family gating; assert the exclusion.

Exit: W18 row `implemented (A–D)`.

### Phase 4 — Surface E, the drones and the 4 Hz key

1. The pawn family: `SQFlyingDrone` subclass test through a
   `SnapshotCaches.is_flying_drone` cache (`_is_subclass_of`, the
   medical block's pattern); `KIND_DRONE`; `read_drone` producing §7's
   fields — `id`, `class`, `position`, `yaw`, `dead`, `health` and
   `maxHealth` through `HealthComponent` (the component's class layout
   cached), `pilotEosId` (`PlayerState`), `ownerEosId` (`SQ PC` →
   player state), `commandAction`, `batteryLifetimeMax`,
   `lastHitByEosId` (`LastHitBy` → controller → player state) — with
   the `(0, 0, 0)` exclusion.
2. `SampledEntities` gains `drones: tuple[tuple[int, str], ...]`
   (pawn address, id); `sample_positions` emits a `drones` array of
   `{id, x, y, z, yaw}` under the existing gates plus the `(0, 0, 0)`
   drop, adding `dead: true` once the pawn's `Dead` bit is set and
   `lastHitBy` (the hitter's EOS id) once the pointer resolves to a
   player state, each key only when set (D19; spec §1's fourth act).
   The pawn's `Dead` mask and the `LastHitBy` offset ride on
   `SnapshotPaths` like the vehicle team offset does.
3. Doctor rows per §8 (`SQFlyingDrone`, the two Blueprint classes,
   `HealthComponent_C`, `Controller`). Schema: the `drones` row and
   section, and the position-line table's new row with the two
   conditional keys.

Tests: `tests/test_possample.py` gains a drones case asserting the
exact dict with and without the conditional keys; a `read_drone`
fixture test; a recorder test that a position line carrying `drones`
passes through `write_position_frame` unchanged.

Exit: W18 row `implemented (A–E)`; the parity harness run once here
because `possample.py` changed.

### Phase 5 — doctor, schema and spec closure

1. `sqreader doctor` on the box (operator step) with the new rows: the
   three reflection-taken names filled in, every required row present,
   every optional row skipped with its reason or present.
2. Spec §8 and §13 updated with the discovered names; the tracker's W18
   row records them with the date.
3. A `docs/schema.md` pass: every new key in the register with its
   "since" date, the sections cross-linked to the spec.

Exit: doctor clean on the box, spec and register consistent.

### Phase 6 — the viewer, reviewable without a live match

The rules are spec §9; the shapes and constants are the spec's. The
deliverable is twofold: the rendering, and a scenario for every rule.

1. Types (`state/types.ts`): `TeamState.commander`, `GameState.commanderRules`,
   the marker fields, `CommandAction`, `Drone`, the position-line
   `drones` entry.
2. Reconstruction (`state/replayReconstruct.ts`): `drones` spliced by
   `id` from position lines the way vehicles are; `dead` and
   `lastHitBy` carried forward within the interval between full frames
   only (never across a full frame that says otherwise).
3. Derived state, as pure functions with unit tests (`*.test.mts`):
   request lifecycle (pending, approved, consumed, expired, deleted per
   the sweep model), the ready-in arithmetic with its cases (first
   claim, call, commander change, step-down, re-claim, category gate),
   the artillery and mortar timelines, shoot-down detection from
   `actionDestroyed`, the drone killer from the first 4 Hz sample
   carrying `dead`, remaining flight time, team from owner.
4. Rendering (`canvas/draw.ts` and friends): the request circle and
   pending/approved/deleted styling; the asset shapes by `type`,
   `distance`, `addDistance`, `yaw`, with the UAV coverage class named;
   director de-duplication; command actors as icons with their
   `displayName`; drones with pilot and owner; the commander seat and
   vote state in `TeamBar.tsx`; the cooldowns panel with ready-in per
   action and per category; layer toggles for the new layers.
5. **Scenario fixtures**: under `frontend/src/state/__fixtures__/commander/`,
   one small hand-built frame sequence per rule (a few full frames and
   position lines each, JSON, built by a script kept beside them so they
   regenerate deterministically), and a scenarios entry in the viewer —
   a dev-only route or a query parameter — that lists them and loads
   one at a time. Each scenario names the spec rule it demonstrates.
   `docs/viewer-scenarios.md` lists every scenario, what it shows, and
   the rule, so the reviewer opens each in the browser, discusses it,
   and the fixture then stands as the regression test. Minimum set:
   a pending request; the same request approved with the pending twin
   still present; a request deleted; each footprint shape (UAV
   coverage, static barrage, creep path, strike line, mortar radius);
   a vote in progress with tallies; a seated commander with cooling
   and ready assets; a commander change re-stamping entries; a
   step-down; a UAV shot down (early `actionDestroyed`); a creep and a
   mortar mid-barrage; a recon drone flying, exited, and shot down with
   the killer resolved; a commander drone with its call actor.
6. Build and tests: `npm run build` and `npm test` green.

Exit: every rule rendered and demonstrated by a scenario; the reviewer
has walked the scenarios and the changes agreed are applied; W18 row
`implemented (viewer)`.

### Phase 7 — the box: deploy, measure, record, accept

Operator steps, run with the box access. Before each service restart,
`sqreader summary` to see who is on; never trust a sidecar.

1. Deploy: `sudo -u ubuntu git -C /home/ubuntu/UNNReader-Squad pull`,
   `.venv/bin/pip install -q -e .`, then `systemctl restart
   sqreader-prod` when the server is empty or between matches. `sqreader
   doctor` clean. `journalctl -u sqreader-prod -f` for the first minutes.
2. Idle measurement: `profile-build --iters 50` from the live checkout
   and from the baseline checkout, alternating twice; storage of the
   first idle recording through `frame_size_delta.py`.
3. The small commander session (four players by the claim rule, one
   team, twenty minutes): claim, call the UAV and one artillery asset,
   let a request run its life, fly the drone and have it shot, with
   `command_assets.py`, `marker_action.py` and `drone_track.py` running
   as the oracle. During it, `profile-build` from both checkouts
   alternating, three rounds each, once with the UAV up and once with
   the UAV, an artillery call and the drone up together. `SQREADER_PROFILE=1`
   on the service for one such window if the delta needs attribution.
4. Decode: the recording's new keys against the probe rows at the same
   instants; `frame_size_delta.py` on that recording; the numbers into
   the journal and the W18 row.
5. Acceptance run T8 (spec §11): six players, the probe as oracle,
   after which the commander implementation is `verified`.

Exit: the measurement bars met, the recording decoded, T8 run and
decoded, W18 `verified`, the baseline checkout removed.

## Validation

- **Unit tests** as listed per phase, all fixture-based on
  `FakeProcessMemory`; every emitted dict asserted exactly, including
  the `[]` and `null` cases and the omitted-on-missing-name cases.
- **Doctor**: `test_fleet.py` shape and coverage tests updated; on the
  box, `sqreader doctor` clean after every deploy.
- **Recorder and container**: the position-line passthrough test; a
  round-trip test writing a full frame with every new key and a
  position line with `drones` through `SqrxWriter` and reading them
  back.
- **Viewer**: `tsc` through `npm run build`; `npm test` for the pure
  functions and the reconstructor; the scenario fixtures load without
  error in the scenarios entry.
- **Parity**: `scripts/stats_parity.py --recordings-dir <box corpus>
  --live-db <copy>` before the final push, expected green.
- **Live**: the small commander session's recording decoded against
  the probes; the acceptance run.

## Measurement

The additions cost nothing on an empty box beyond the state and
manager reads; the real cost appears only with entries, footprints,
actors and drones live. So every number is taken from both builds
against the same state, alternating, and the idle number is kept as
the floor.

| Quantity | How | Bar |
|---|---|---|
| Full-build p95, idle | `profile-build --iters 50`, both checkouts alternating twice | new − baseline ≤ 5 ms |
| Full-build p95, commander-active (UAV up; then UAV + artillery + drone) | both checkouts alternating, three rounds each, during the session | new − baseline ≤ 10 ms, and ≤ 10 % of the baseline's p95 |
| Position sample p95 with two drones live | the same runs | new − baseline ≤ 0.5 ms |
| Storage per match | `frame_size_delta.py` on the first new-build recording with all new keys | ≤ 3 % of the compressed file (09-05 estimate: 1.4–1.7 %) |
| Per drone per 4 Hz sample | the same | ≤ 150 B raw live; ≤ 200 B after a hit |

A bar missed is not a failure of the work but a stop: attribute it with
`SQREADER_PROFILE=1`, then either fix the read or bring the number to
the register as a decision. The journal's analytical estimate (under a
millisecond per frame against a 130 ms build) is what the bars are set
from.

## Acceptance criteria

The recording made by the new build during the commander session, read
against the probes' rows at the same instants, shows:

1. `teams[].commander` on both teams every frame: the seat `null` on
   the empty team and the player on the other; `vote.inProgress`,
   `timer`, `endsGameTime`, `cooldownActive`, `cooldownTimer`,
   `cooldownEndsGameTime` matching the state raws; `nominees[]` `[]`
   before a vote and the tallies during one; `categories[]` with
   `lastUseGameTime` `null` before the first call and the stamp after;
   `actions[]` `[]` before the claim and, after it, every entry with
   `action`, `displayName`, `createdGameTime` back-dated as the spec
   says, the config values, and `destroyedDuringActive` after a kill.
2. `commanderName` / `commanderEosId` present with the seat, `null`
   without it.
3. `gameState.commanderRules` every frame with the six values.
4. On every command marker: `distance` and `addDistance` as the probe
   read them, `yaw` present, `action` `null` on request markers and the
   config's class name on every footprint; nothing new on squad-data
   markers.
5. `commandActions[]` present only while an actor exists, with the
   common fields and the family fields the class carries, no `health`
   or `dead` on strike and UAV actors, `actionDestroyed` true while a
   shot-down actor lingers, `callerEosId` the commander.
6. `drones[]` for every pawn with the §7 fields, `pilotEosId` `null`
   while exited, `ownerEosId` constant, `lastHitByEosId` the killer at
   the first dead frame; the position line's `drones` entries every
   sample with `dead` and `lastHitBy` only once set.
7. `sqreader doctor` clean; the parity harness green; all gates green.
8. The viewer draws every scenario and the session's recording without
   error, and the reviewer has signed off the scenarios.
9. The measurement bars met and the numbers recorded.

## Definition of done

W18 is `verified` when the acceptance run T8 has been decoded with no
disagreement between the recording and the probe, the doctor is clean,
the parity harness is green, the viewer scenarios are agreed, the
schema register and the spec's §8/§13 carry the implementation's
names, the changelog tells the story, and the tracker rows W10, W18,
W20, W72 and this plan's W17 are in their final states.

## Out of scope

- Index-tracking the new lists in the packed stream (D21; tracker D7).
- Any stats or ELO derivation from the new fields (tracker W33).
- The bomb-circle radii (T9 f) and the shooter of a UAV or aircraft
  (D18).
- The platform side (SquidHub ingestion of the new keys).
- Re-enabling 4 Hz projectile sampling (C1).

## Risks and how the plan meets them

- **A name the spec expects is absent on the box's build** (a Squad
  update between the spec and the implementation): the doctor reports
  it, the read omits the field, and the fix is a re-verification of the
  name, never a guess.
- **The config default-object lookup** costs an object-array walk per
  new class: cached per class address, invalidated only with the
  caches' generation, measured in Phase 7.
- **Several manager instances**: the first live one is read; the
  session showed them identical; the doctor covers the class.
- **Layer roll**: class addresses churn; the existing cache
  invalidation handles it as it does for every other class.
- **Performance at 110 players**: the additions are per-team, per-marker,
  per-actor and per-drone, not per-player; the bars are set from the
  test box and re-read on the first production-sized recording.
- **Viewer review friction**: the scenario fixtures exist so the
  reviewer never waits for a match to see a rule drawn.
