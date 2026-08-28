# Implementation plan: three detectors + seeding exclusion + two-tier enable

Status: **awaiting Craig's approval** · drafted 2026-08-28 · target branch: `Replay-Improvements`

This is a self-contained handoff spec. Before starting, read `CLAUDE.md`
(hard rules — they all apply here), `docs/stats-wishlist.md` (where these
items were derived and approved in discussion), and skim
`docs/architecture-notes.md` for the module map. Run the full gate
(`pytest`, `ruff check .`, `mypy sqreader`) before every push; frontend is
untouched by this plan. Changelog entries go under `[Unreleased]` in the
house narrative voice.

**In scope:** Workstreams A–D below.
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

### B2. `no_reload` via ammo-consumption rate

- **Signal:** `soldier.weapon.magazines` (list of current round counts for
  the held weapon) — no new fields. Squad is a per-magazine system: firing
  drains the summed total; reloading does not (the partial mag returns to the
  pool). Tick-over-tick drop in the sum is verified fire volume.
- **Tell:** rounds consumed inside a rolling game-clock window exceeding what
  any magazine-dump-plus-mandatory-reload cycle could produce.
- **Model:** per `(player, held weapon className)` track: estimated capacity
  = the largest single-magazine value ever observed for that weapon (memory-
  verified, no static tables), and a rolling consumption window. Alert when
  `consumed > capacity_est * ceiling_factor` within `window_seconds`, with an
  absolute floor so tiny-mag weapons can't trip on estimation noise.
- **Config defaults (deliberately loose until D):** `noreload_window_seconds:
  30.0`, `noreload_ceiling_factor: 3.0` (three full magazines through the gun
  in 30 s with zero reload pauses — belt-fed MGs at ~200-round boxes and
  ~8-second reloads stay under this), `noreload_min_rounds: 120`.
- **Guards:** reset the window on weapon-class change (the visible pool is a
  different gun's — same rule `infinite_ammo` applies), on respawn, on
  vehicle entry; ignore ticks where the magazines list is absent. Resupply
  only *increases* the sum → masks toward false negatives, which is the safe
  direction; say so in a comment.
- **Details payload:** weapon, rounds consumed, window seconds, capacity
  estimate, ceiling used.

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

1. **First, resolve the dirty working tree** at
   `/home/ubuntu/UNNReader-Squad`: `sqreader/httpsrv.py` carries an
   uncommitted hot-patch (backup `httpsrv.py.bak-http11`). Diff it against
   HEAD; if it is a real fix, commit it to the fork in house style (its own
   commit, before anything else); if obsolete, restore the file and delete
   the backup. **Do not pull over it.** Git commands on that clone run as
   `sudo -u ubuntu git -C /home/ubuntu/UNNReader-Squad …`.
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

1. Run `scripts/plugin_replay.py` with all three new detectors enabled over
   every recording on the test server (and the exported production
   recordings, once Craig provides them).
2. Produce a short report: alerts per detector per match, with the evidence
   payloads. Zero false accusations on known-clean matches is the bar —
   missed cheaters are acceptable, false accusations are not.
3. Tune thresholds only with written reasoning (the file's tradition), rerun.
4. **Enabling the detectors in production config is Craig's decision**, made
   on that report — not part of this plan's execution.

---

## Definition of done

- All A/B tests green; full gate passes; changelog updated.
- Seed matches produce no recording, no stats row, no upload path — while a
  configured test box can still opt out of exclusion.
- Three detectors exist, default-off, each with tests at two tick rates.
- Test server records two-tier (`positionFrames > 0`) from the branch build.
- Validation report from D delivered for the enable/no-enable decision.
- Commits are clean, self-contained, house-voiced; the B4 docstring fix and
  anything generic is shaped to be upstream-offerable.
