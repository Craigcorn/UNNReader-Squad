# Architecture notes

A module map of the backend and the viewer with file pointers, written so a
future session (human or Claude) can navigate without re-deriving it. Line
numbers are approximate as of 2026-08-28 (v1.4.4); trust names over numbers.

## Backend (`sqreader/`)

### Memory / UE layer

| File | Responsibility |
|---|---|
| `mem.py` | Read-only `/proc/<pid>/mem`: parses maps, one long-lived fd, `pread` per read, optional `process_vm_readv` batching. `ProcessMemory` :161 |
| `ue/uobject.py` | UE5 `GUObjectArray` reader + chunked bulk reads; discovery `find_gobjects_base` :592 |
| `ue/fname.py` | FName pool reader — indices → strings, cached |
| `ue/reflection.py` | Walks `UStruct`/`FProperty` chains to derive field offsets at runtime; `bool_property_mask` :392 resolves bitfield bools |
| `ue/value.py` | Typed value readers; never raise, return `None` on bad memory |
| `scanner.py` / `aob.py` | Startup-only signature scanning (string xrefs, wildcard AOB) |
| `addrcache.py` | Persists discovered globals keyed by binary fingerprint; advisory, always re-validated |

### Squad domain (`sqreader/squad/`)

| File | Responsibility |
|---|---|
| `snapshot.py` (~4000 lines, the core) | One object walk per tick, classify, read every entity, emit the snapshot dict. All hardcoded offsets + runtime override mechanism. `build_snapshot` :3065, `resolve_paths` :1033, per-entity readers (`read_player` :2544, `read_vehicle` :2483, `read_capture_zones` :1955), `clean_nonfinite` :150 |
| `walkdelta.py` | Incremental serial-diff walk (numpy-accelerated, pure-Python fallback); rolling resync instead of periodic full-walk spikes |
| `possample.py` | **The 4 Hz fast tier**: re-reads position/health/yaw only for a known entity set; same freshness gates; failing entities omitted, never guessed. `sample_positions` :126 |
| `buildworker.py` | Full ~1–2 s builds in a subprocess (`sqreader build-worker`), NDJSON over stdout, latest-wins handoff, auto-respawn — keeps the GIL off the 4 Hz sampler |
| `logtail.py` | Tails `SquadGame.log` for the authoritative kill feed; `find_squad_log` asks the running game where its log is |
| `capzones.py` | Merges static SquadCalc capture-zone geometry onto live zones; strict no-guess matching |
| `metadata.py` | Static display-name / layer-bounds catalogs |

### Recording / serving / stats

| File | Responsibility |
|---|---|
| `sqrx.py` | The container: header + one independent zstd frame per NDJSON line (crash-safe, append-only, passthrough-servable). `SqrxWriter` :46, `SqrxReader` :102 |
| `recorder.py` | Per-match state machine: 3-tick confirm on start/end; `.sqrx` + `.meta.json` sidecar; shutdown finalizes as `unverified` and the next boot resumes; `write_position_frame` :604 is deliberately side-channel (never touches tick counts or the state machine) |
| `recording_lifecycle.py` | Shared constants so recorder and HTTP gate cannot drift (`MATCH_TRANSITION_CONFIRM_TICKS = 3`, states `active`/`finalized`/`unverified`) |
| `httpsrv.py` | Stdlib threading HTTP server. Routes: `/api/recordings` (finalized only), `/api/recording/<id>` (NDJSON stream; **zstd passthrough** ships on-disk frames verbatim), `/meta`, stats API (`/api/players|leaderboard|weapons|heatmap|matches|match/<id>|layers`), `/icons/*`, `/sqmaps/*`, SPA, `/health` |
| `stats.py` | SQLite store (WAL) + additive idempotent migrations + read-side queries. `record_tick` :627 mirrors the recorder's 3-tick confirmation; `_finalize_match` :987 publishes `winner_team` only when `end_state == "finalized"`; matches stamped `server_id NOT NULL` |
| `elo.py` | Per-match ELO over the stats DB; `apply_match_elo` in the finalize transaction; `recalc_all_elo` :357 is the deterministic full rebuild |
| `synth_match.py` | Mints stable uuid5 match ids on unlicensed servers (globally-unique community scope — safe in a combined central store) |
| `plugins/` | Per-tick alert plugins on the serve thread: `base.py` contract (read-only snapshot, never raise, budget = tick), `cheat_detect.py` (3 active detectors + 1 off; 4 more documented as blocked on unrecorded fields), `notify.py` Discord webhook |
| Fleet/update | `ingest_client.py` (push finished matches, backlog + flush), `updater.py`/`update_sign.py` (Ed25519 staged self-update), `health.py` (`doctor` offset drift), `offset_client.py` (signed offset self-heal), `agent_creds.py`, `crypto_envelope.py` |

### Data flow per tick (serve mode, two-tier)

1. Full build (~1 Hz) in the build-worker subprocess → parent collects latest.
2. `cli.py` merges log-tailed kill events into the snapshot (**before** anyone
   consumes it) → recorder writes it → `record_tick` consumes the same dict →
   plugins run.
3. Between fulls, the 4 Hz sampler emits `{"t":"pos"}` frames → recorder only.
4. Match end (3 confirmed inactive ticks) → recording finalized → stats
   finalize + ELO in one transaction → `on_finalize` hook → central push queue.

CLI subcommands (`cli.py`): `serve` (production), `snapshot`, `summary`,
`watch`, `stats-backfill` (replay archive → stats DB; **the parity/ingest code
path**), `stats-elo-recalc`, `doctor`, `retention`, `enroll`, `build-worker`,
`profile-build`, `version`, `selftest`, `apply-staged-update`.

## Viewer (`frontend/`)

React 18 + zustand + Vite, TypeScript strict. Tests are plain node scripts
(`*.test.mts`) run by `scripts/run-tests.mjs` (esbuild JS API — the `.cmd`
shim EINVALs on Windows). Dev proxy targets the agent on `127.0.0.1:8080`.

### Replay playback pipeline

1. **Fetch** — `api/recordings.ts` streams NDJSON (`fetchRecordingFrames`),
   decoding line-by-line during download.
2. **Decode, two composed layers** — `state/replayUnpack.ts` (wire format v2:
   per-frame key diff + keyed entity tables; unchanged sub-objects shared by
   reference across frames — the point is memory, not bandwidth) then
   `state/replayReconstruct.ts` (splices 4 Hz `pos` frames onto the last full
   snapshot; empties `damageEvents` to avoid double counting). Cross-checked
   against the Python encoder by `state/crosslang.test.mts` + fixture.
3. **Buffer** — all frames materialized up front into `replay.frames`
   (~50 MB heap for a 30-min match; no windowing yet — Phase 4 target).
4. **Clock** — `api/playback.ts`: one rAF loop, wall-clock anchored
   (`targetSnap = baseSnapMs + (now − baseWallMs) × speed`); publishes
   `state/replayClock.ts` (module-level, deliberately outside zustand so 60 fps
   writes don't re-render React). Pause freezes the mid-span playhead;
   seek-while-playing and speed changes rebase explicitly.
5. **Interpolate** — `canvas/interpolation.ts`: positions/yaw/turrets/capture
   lerped between bracketing frames; teleport guard scales with the real tick
   interval; respawn (new pawn addr) snaps; mounted soldiers use the vehicle
   threshold.
6. **Draw** — `canvas/MapCanvas.tsx` render loop grafts frame-a discrete state
   onto interpolated entities, then `canvas/draw.ts` (`renderScene`) emits
   pixels. Hit-testing uses the exact drawn snapshot (`displayRef`).

### Directory map

- `src/api/` — fetch + the replay hooks (`replay.ts` loader, `playback.ts`
  clock, `playerStats.ts` dashboard fetches)
- `src/canvas/` — pure rendering/geometry: `draw.ts` (all pixels),
  `worldToScreen.ts`, `hitTest.ts`, `icons.ts`, `projectiles.ts`,
  `capVisibility.ts` (RAAS pre-roll fog), `markerGeometry.ts`,
  `mapFallback.ts` (map when the layer name is unknown), `ruler.ts`
- `src/killfeed/` — `diff.ts` pure tick-diff with five-tier weapon
  attribution; live vs replay drivers
- `src/state/` — one zustand store `viewerStore.ts` (all slices, fine-grained
  selectors); non-reactive side channels `replayClock.ts`, `replayLoad.ts`;
  decode layers; `types.ts`
- `src/ui/` — TopBar, TimelineBar (binary-search seeks on the timestamp
  axis), BufferOverlay, KillFeed, Scoreboard, PlayerStats dashboard,
  RecordingPicker, SettingsMenu
- `src/data/` — static weapon/vehicle catalogs, lazily imported/code-split

### Known structural limits (Phase 4 targets)

- Whole recording in RAM before playback starts (`recordings.ts:23`).
- Kill-feed precompute is a synchronous main-thread pass at load
  (`api/replay.ts:107`).
- `Date.parse(timestamp)` re-run every rAF (`MapCanvas.tsx:179`,
  `playback.ts` advance loop) instead of a cached numeric timestamp array.
- Playback advances forward only; scrubbing works because seeks set the index
  directly.

The public build is replay-only: `canLive: false` is hard-wired and
`tests/test_public_no_live.py` asserts the gate. The live code paths
(snapshot buffer, 6 s render delay) are dormant but intact — Phase 6 feeds
them from a socket instead of SSE.

### Regressions with named guards (do not reintroduce)

Pause rewinding to the frame boundary (`playback.ts:47`); seek-while-playing
freeze (`playback.ts:86`); helicopter stutter from a fixed teleport threshold
(`interpolation.ts:34`); markers vanishing when paused (`MapCanvas.tsx:186`);
kill feed stale after seek (`useReplayKillFeed.ts`); carry-over resurrecting
end-of-match ghosts on backward seek (`viewerStore.ts:308`); 4 Hz position
lines silently discarded (`replayUnpack.ts:99` + the cross-language fixture).
