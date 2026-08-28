# Implementation plan: three detectors + seeding exclusion + two-tier enable

Status: **approved 2026-08-28 — in implementation** · target branch: `Replay-Improvements`

This is a self-contained handoff spec. Before starting, read `CLAUDE.md`
(hard rules — they all apply here), `docs/stats-wishlist.md` (where these
items were derived and approved in discussion), and skim
`docs/architecture-notes.md` for the module map. Run the full gate
(`pytest`, `ruff check .`, `mypy sqreader`) before every push; frontend is
untouched by this plan. Changelog entries go under `[Unreleased]` in the
house narrative voice.

Execution notes: work lands as house-voiced commits on `Replay-Improvements`,
pushed as they complete. On this Windows dev box, full-repo mypy currently
aborts on unrelated numpy stubs in the system Python — judge by per-file
mypy and CI, don't chase it. Test-server access facts live in Workstream C.

**In scope:** Workstreams A–E below.

## Reference corpus (main server, Squad 10.5.x)

Four real finalized matches (108–139 peak players; three RAAS, one
Territory Control), recorded by the managed upstream agent, at
`C:\Users\CRAIG\Documents\UNN\Misc\SquadReader Replays` (`.sqrx` +
`.meta.json` each). This corpus is D's false-positive validation set and
already settled several verification questions on 2026-08-28:

- **The six ODK collector fields populate at 10.5.x** (captures up to 239,
  suppliesDelivered up to 40 971, etc.) — see Workstream E for what remains.
- **Causer mismatch confirmed:** explosive damage events name the projectile
  (`BP_40MM_Proj2_C`, `BP_AT4_HighPenetration_Rocket_Proj2_C`), bullets name
  the gun (`BP_L85A2_C`), vehicle weapons name the vehicle — the current
  `infinite_ammo` substring check discards all non-rifle events.
- **`projectiles[]` covers launchers**: 40 mm GL rounds, all rocket
  families, mortars, tank shells, ATGMs all appear with tracked classes.
- **Single-shot magazine shapes**: `BP_M136AT4_C → [1]`,
  `BP_RPG7V2_Tandem_2mag_C → [1, 1]`, `BP_M320_HE_C → [1, 1, 1, 1]`; note
  bayonets/binoculars present as `[0]` — capacity 0 must never arm B2.
- **The main server also records single-tier at 0.5 Hz** (`positionFrames:
  0`) — two-tier is aspirational everywhere today, so Workstream C's enable
  makes the test box the first two-tier deployment; treat its recordings as
  the first real exercise of that path.
**Explicitly out of scope:** the parity harness (own plan, next), all Tier 1/2
wishlist *stats* persistence (awaiting team reaction), the seek index and any
wire-format change, `remote_shovel` (needs a live probing session first),
SquidHub-side anything, and the frozen built-in stats dashboard.

---

## Workstream A — seeding exclusion at the source

**Decision being implemented** (plan doc, decision 6): a seeding match is
never recorded — no `.sqrx`, no stats, no upload. Primary key: the snapshot's
`gameState.gameMode` equals `"Seed"` (verified against a real recording:
`2026-08-27_195513_FallujahSeedv1…` has `"gameMode": "Seed"`). Layer-name
patterns are a config override hatch for scrims/events.

### Design

1. **Shared predicate, one implementation.** Add to
   `sqreader/recording_lifecycle.py` (it exists precisely so the recorder and
   other consumers cannot drift, and is dependency-free — keep it that way):

   ```python
   def is_excluded_match(snap, *, game_modes: frozenset[str],
                         layer_patterns: tuple[str, ...]) -> bool
   ```

   - `game_modes` match is exact, case-insensitive, against
     `snap["gameState"]["gameMode"]`.
   - `layer_patterns` are case-insensitive `fnmatch` globs against
     `layerName`, falling back to `mapName` if the layer is absent.
   - **Fail open:** if `gameMode` and both names are unreadable (`None`),
     return `False` — an unverifiable tick must never cost a competitive
     recording. Only a verified "Seed" skips (no-guess applied to skipping).

2. **Config** (`sqreader/config.py` + `sqreader.config.example.json` + README
   config table): `seeding_game_modes` default `["Seed"]`,
   `seeding_layer_patterns` default `[]`.

3. **Recorder** (`sqreader/recorder.py`): evaluate the predicate at the same
   point a confirmed new match would open a writer (`_handle_snap`'s
   open-decision path). If excluded: record the matchId in a small
   `_excluded_ids` set and do not open; subsequent ticks and 4 Hz position
   frames for that matchId are ignored without re-evaluating (sticky per
   match — a mid-match gameMode flap must not flip the decision either way).
   Verify `write_position_frame` is inert when no writer is open (it should
   already be — it is a side-channel; add a test, not an assumption).

4. **Stats** (`sqreader/stats.py`): `record_tick` applies the same predicate
   (constructor takes the same two config values) so a seeding match never
   opens a stats row. Same stickiness by matchId.

5. **Backfill** (`cmd_stats_backfill` in `cli.py`): skip recordings whose
   sidecar meta `gameMode` is in the excluded set — the archive already
   contains pre-exclusion seed recordings (the 6-hour Fallujah file) and the
   future SquidHub ingest reuses this path.

6. **Plugins are untouched**: cheat detection consumes live snapshots and
   must keep running during seeding.

### Tests (extend `tests/test_recorder_lifecycle.py`, `tests/test_stats_match_end.py` patterns)

- gameMode "Seed" → no recording opened, no stats row, position frames inert.
- Layer pattern (e.g. `*Skirmish*` configured) → skipped.
- `gameMode=None` torn tick → records normally (fail-open).
- Sticky: excluded match stays excluded across a flap; competitive match
  mid-stream is never closed by a transient "Seed" reading.
- Backfill skips a seed-meta recording, processes a competitive one.

### Changelog entry

Added — seeding matches are excluded at the source, keyed on the game's own
mode field, with layer patterns as the override hatch (house voice; explain
why: a 6-hour 2-player seed session is noise dressed as a match).

---

## Workstream B — three detectors in `sqreader/plugins/cheat_detect.py`

**Safety posture for all three (non-negotiable):** ship **disabled by
default** (`detect_* = False`) exactly like `magic_bullet`, with a comment
saying they stay off until measured against the archive (Workstream D). A
detector is an accusation generator; the 1.4.4 changelog explains the
philosophy. Follow the file's established mechanics: thresholds anchored to a
physical/mechanical limit with a wide margin and the reasoning written down,
durations accumulated in **game-clock seconds** (`elapsedSec`), per-player
state in `_PlayerState` (extend `__slots__`), `_cooled_down` per alert type,
streak resets on `cache_reset` / respawn (`soldier.addr` change) / context
change, and evidence-rich `details` dicts so a human can judge from the
replay. `on_tick` must never raise. Access `ctx.snapshot["deployables"]`
directly (read-only — never mutate the snapshot).

### B1. `stamina_hack`

- **Signal:** `soldier.stamina` / `soldier.staminaMax` (already read;
  `snapshot.py` soldier block) + position-derived speed (reuse the speedhack
  machinery's `_xy`/dt plumbing).
- **Tell:** sustained movement at sprint-class speed while stamina never
  depletes. Sprinting drains stamina universally in Squad; a full bar at
  sprint speed for long enough is mechanically impossible.
- **Config defaults (tune in D):** `stamina_sprint_min_mps: 5.5` (above walk,
  below the 7.8 sprint cap), `stamina_full_fraction: 0.98` (bar ≥98% counts
  as "not draining" — absorbs float jitter), `stamina_sustained_seconds:
  20.0`, plus the shared cooldown.
- **Guards:** skip when `staminaMax` unreadable or ≤0; skip attached/vehicle
  occupants (`_occupied_eos`); reset on the same events as speedhack.
- **Details payload:** speed, stamina fraction, sustained seconds, sprint cap.

### B2. Firing without ammo accounting — primary and secondary models

**The premise question (Craig's challenge — settle it in D before trusting
either model).** Whether a cheater's *server-side* ammo falls at all is
unconfirmed and probably not: Squad's client holds enough authority over the
firing path for these cheats to exist at all, and the inherited
`infinite_ammo` detector's own design — damage keeps landing while "their
carried ammo never went down" — documents that in the cheats it was tuned
against, the server's copy did **not** fall. Consequence: the primary signal
must not depend on ammo moving; the consumption models are kept only as a
secondary net for the variant that removes the reload timer while leaving
ammo accounting honest.

**B2-primary — verified shots vs a static ledger.** Implement as an upgrade
to the existing `infinite_ammo` (keep its cooldown/staleness machinery —
that's where the tuning history lives). Shot evidence, no ammo dependence:
(a) **launchers** — `projectiles[]` spawns carry a memory-verified `firer`
(`read_projectile`, snapshot.py:1531-1543); diff by projectile id per tick,
with the same restart-flood guard as B3; works with zero damage events
(spraying a treeline). (b) **bullets** — damage events, **after fixing
causer matching**: confirmed against the reference corpus — explosives name
the projectile class and vehicle weapons name the vehicle, so the substring
check has been silently discarding every non-rifle event; the fix maps
projectile/vehicle causers before matching, or matches only rifle-shaped
causers and leaves launchers to the projectile path. Alert when `fire_no_ammo_min_shots` (default
4) verified shots span `fire_no_ammo_min_window_seconds` (default 10) while
the held weapon's summed ammo never decreases; any sum *increase* (resupply)
resets the observation. Config flag `detect_fire_no_ammo: False` until D.

**B2-secondary — consumption anomalies** (the models below; same ledger
plumbing, same default-off posture):

- **Signal:** `soldier.weapon.magazines` (list of current round counts for
  the held weapon) — no new fields. Squad is a per-magazine system: firing
  drains the summed total; reloading does not (the partial mag returns to the
  pool). Tick-over-tick drop in the sum is verified fire volume.
- **Tell:** rounds consumed inside a rolling game-clock window exceeding what
  any magazine-dump-plus-mandatory-reload cycle could produce.
- **Model:** per `(player, held weapon className)` track: estimated capacity
  = the largest single-magazine value ever observed for that weapon (memory-
  verified, no static tables), and a rolling consumption window. The legit
  ceiling is the mandatory dump-then-reload cycle, so it must scale with
  capacity, not multiply it flatly (a flat "3× capacity" default was
  considered and rejected: a legit rifle player mag-spamming at robot speed
  reaches ~150 rounds/30 s, which would have crossed a 120-round flat
  threshold — a false accusation waiting):

  ```
  cycle_min = capacity_est / MAX_RPS + RELOAD_MIN
  ceiling   = capacity_est * (window / cycle_min + 1)   # +1 = partial mag at the edge
  alert when consumed > ceiling * noreload_margin
        and consumed >= noreload_min_rounds
  ```

- **Config defaults (provisional until D):** `noreload_window_seconds: 30.0`,
  `noreload_max_rps: 17.0` (~1000 rpm — faster than any infantry weapon),
  `noreload_reload_min_seconds: 3.0` (faster than any real reload),
  `noreload_margin: 1.5`, `noreload_min_rounds: 120` (floor against
  capacity-estimation noise). Worked numbers to keep in the comment: 30-round
  rifle → ceiling ≈ 217, ×1.5 ≈ 325, vs a cheater's continuous ~350–500 in
  30 s — detectable; 200-round belt box → threshold ≈ 690, so a no-reload
  MG (~300) is invisible, and acceptably so: belt weapons reload so rarely
  the cheat barely helps there. The detector's value is on magazine weapons,
  where reloads are constant.
- **Single-shot path (grenade launchers, rockets — where UNN actually sees
  this cheat).** The windowed model is structurally blind there: capacity is
  1, the whole pool is a handful of rounds, and the 120-round floor can never
  be reached. For observed capacity below `noreload_smallmag_capacity: 10`,
  switch to a **shot-spacing rule**: a drop of `>= 2 + floor(dt /
  noreload_reload_min_seconds)` rounds within one tick interval means two
  shots closer together than one mandatory reload — mechanically impossible
  (dt in game-clock seconds; at dt≈2 s vs the 3 s universal reload, any
  2-round drop violates). Count such strikes; alert on
  `noreload_strikes: 2` within the cooldown window. The `min_rounds` floor
  applies **only** to the windowed path. Arm either path only when
  `capacity_est >= 1`: bayonets and binoculars present as `[0]` magazines
  in real data and can never fire.
- **Guards (both paths):** reset on weapon-class change (the visible pool is
  a different gun's — same rule `infinite_ammo` applies), on respawn, on
  vehicle entry; ignore ticks where the magazines list is absent. Resupply
  only *increases* the sum → masks toward false negatives, which is the safe
  direction; say so in a comment.
- **Details payload:** weapon, path taken, rounds consumed, dt/window,
  capacity estimate, ceiling or strike count.

**D verification items for B2 (gate both models on these):**

1. **Baseline legit fire on the test server** — a player firing normally
   while snapshots are watched: confirm the server-side ammo sum decrements
   per shot, and record how a reload refill presents at 1 Hz sampling.
2. **Labeled cheater data: none exists.** Craig confirms no cheaters have
   been observed since sqreader was deployed, so the archive is presumed
   clean. Validation therefore means **false-positive cleanliness**: both
   models run over the clean archive and must produce zero accusations.
   Detection efficacy — including the open question of whether a real
   cheater's server-side ammo moves — stays unproven until the first live
   incident, which (with alerts wired) becomes the first labeled recording;
   revisit thresholds and the primary/secondary split on that day.
3. **Launcher mechanics — mostly answered by the reference corpus** (see
   that section: magazine shapes, projectile coverage including 40 mm GL
   rounds, real causer strings). One item remains: whether disposable tubes
   (AT4-style) despawn as a weapon swap after firing — observable by
   following an AT4 carrier across ticks in the corpus.

### B3. `remote_mine`

- **Signal:** `deployables[]` entries — first appearance, `classShort`, the
  memory-verified `placer` link, and positions (all already recorded).
- **Tell:** a mine materializes far from its placer's body. Legitimate
  placement is arm's reach; the budget only has to absorb tick drift.
- **Model:** per tick, diff deployables by stable identity (addr/id) against
  the previous tick's set; for each *new* deployable whose `classShort`
  matches `mine_class_keywords` (config, default `["mine", "ied"]`,
  case-insensitive substring — verify real class names against archive
  recordings during D and widen if needed): resolve the placer to a player,
  take both positions from the same tick, alert when distance >
  `remote_mine_max_dist_m` (default `50.0` — sprint drift over ~2 ticks is
  ~16 m; 50 clears it with margin while real remote placement is hundreds).
- **Guards:** no placer resolvable, or either position unavailable → no
  accusation (the `remote_melee` rule). First tick after attach/cache reset:
  treat the entire deployable set as pre-existing (no "new" entries), so a
  reader restart mid-match cannot mass-accuse.
- **Details payload:** deployable class, distance, drift budget, positions.

### B4. Housekeeping (separate commit, upstream-offerable)

Rewrite the stale "What is NOT here" docstring block
(`cheat_detect.py:26-51`): stamina and deployable-placer data now exist (and
B1/B3 consume them); `no_reload` is solved from ammo data without the
firing/reloading flags; only `ShovelAction` remains genuinely unread. Keep
the section's format — it is documentation of the no-guess boundary.

### Tests (`tests/test_cheat_detect.py` has the harness patterns)

Per detector: a clean-player sequence (no alert), a cheating sequence
(alert with correct evidence fields), the guard cases (torn reads, respawn
reset, vehicle occupancy, weapon swap for B2, restart-flood for B3), cooldown
suppression, and config-off means fully inert. Durations tested at two
different tick rates to prove the game-clock scaling.

---

## Workstream C — enable two-tier recording on the test server

Ops, over SSH (`ssh squad`), after A+B land on the branch. The unit currently
passes `--hz 0.5` and no `--record-hz`, so recordings have `positionFrames: 0`.

1. **First, clear the working tree** at `/home/ubuntu/UNNReader-Squad`. The
   dirty `sqreader/httpsrv.py` is the intentional HTTP/1.1 chunked-transfer
   fix, now committed to this branch as `25467d8` (see CHANGELOG) — the
   hot-patch is superseded. At deploy: `sudo -u ubuntu git -C
   /home/ubuntu/UNNReader-Squad checkout -- sqreader/httpsrv.py`, delete
   `sqreader/httpsrv.py.bak-http11`, then fetch/checkout the branch; the
   committed fix takes over. Validate afterwards that a replay still plays
   through the traefik route (the bug this fix exists for).
2. Deploy the branch: fetch + checkout `Replay-Improvements`, `pip install
   -e .` in `.venv` (idempotent), edit the systemd unit's ExecStart to
   `--hz 1 --record-hz 4`, `daemon-reload`, restart `sqreader-prod`.
3. **Test-server config quirk:** the box mostly runs seed layers, which
   Workstream A would now stop recording. Set `seeding_game_modes: []` in
   that box's `sqreader.config.json` so pipeline testing continues; the
   default stays `["Seed"]` for real deployments.
4. Validate: service healthy; the next finished recording's meta shows
   `positionFrames > 0`; a spot-check replay in the viewer moves smoothly at
   4 Hz.

---

## Workstream D — validate detectors against the archive, then decide

1. Run `scripts/plugin_replay.py` with all new detectors enabled over the
   reference corpus (`C:\Users\CRAIG\Documents\UNN\Misc\SquadReader
   Replays` — four real 100+-player matches; this is the corpus that
   matters) plus the test-server archive for completeness.
2. Produce a short report: alerts per detector per match, with the evidence
   payloads. Zero false accusations on known-clean matches is the bar —
   missed cheaters are acceptable, false accusations are not.
3. Tune thresholds only with written reasoning (the file's tradition), rerun.
4. **Enabling the detectors in production config is Craig's decision**, made
   on that report — not part of this plan's execution.

---

## Workstream E — Squad 10.5.x collector fields + serving fixes

Context (from the deployment findings of 2026-08-27): the test server runs
**Squad v10.5.3** against offsets derived for v10.4.1. `sqreader doctor`
passes every core check — anchors, reflection layouts, SQ-class offsets,
lane graph, markers — the self-healing design working as intended. But the
six ODK stats-collector field checks fail even with a player online:
`captures`, `defenses`, `fobsBuilt`, `fobsDestroyed`, `vehicleDamage`,
`suppliesDelivered` ("no player carried it — offset drift?").

1. **RESOLVED 2026-08-28: event-gated, not drifted.** The decisive test ran
   with a player online during a live round on the test box: his stats
   block read `fobsBuilt: 1`, `suppliesDelivered: 3000`, `defenses: 32` —
   the six fields resolve and track real actions at 10.5.3. Step 2
   (re-derivation) is **not needed**. Remaining E work: fix `doctor`'s
   heuristic to accept present-with-zero as verified (skip, don't fail, on
   an idle server). Footnote for the stats redesign, not this plan:
   `captures` read 0 despite a real capture while `defenses` accumulated —
   the ODK counters' *semantics* need understanding before display.
2. **If drifted:** re-derive the six offsets with
   `scripts/dump_struct_layout.py` against the live process, update the
   offset table + `doctor` entries. These feed leaderboard aggregates
   (kills/deaths are unaffected — log + reflection paths pass), and the
   wishlist's Tier 1 depends on several of them being real.
3. **SPA deep links 404** on hard reload: only `/`, `/viewer`,
   `/viewer.html`, `/viewer-next` map to `index.html`. Add a catch-all →
   index for non-API GET paths. Upstream-offerable, like the HTTP/1.1 fix.

---

## Definition of done

- All A/B tests green; full gate passes; changelog updated.
- Seed matches produce no recording, no stats row, no upload path — while a
  configured test box can still opt out of exclusion.
- Three detectors exist, default-off, each with tests at two tick rates.
- Test server records two-tier (`positionFrames > 0`) from the branch build.
- Validation report from D delivered for the enable/no-enable decision.
- E resolved: the six collector fields either confirmed event-gated or
  re-derived for 10.5.x; deep links survive a hard reload.
- Commits are clean, self-contained, house-voiced; the B4 docstring fix and
  anything generic is shaped to be upstream-offerable.
