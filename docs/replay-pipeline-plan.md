# The Replay Pipeline — plan (UNN)

Status: phase 1 largely delivered · stats wishlist awaiting team reaction ·
drafted 2026-08-28 · **read "Program state" below first when resuming**

## Program state — as of 2026-08-29

Everything here is on `Replay-Improvements` (through `870328b`) and deployed
to the test box unless noted. Detail lives in each plan doc and the
changelog's `[Unreleased]`; reports naming players live in
`C:\Users\CRAIG\Documents\UNN\Misc\` (never the repo).

**Shipped and verified:**

- Seeding exclusion (gameMode-keyed; test box opts out via its config).
- Two-tier recording live and validated end to end (`--hz 1 --record-hz 4`);
  recordings carry `endFrames` (match endings) since the parity fix.
- The parity harness (`scripts/stats_parity.py`) — green on the box corpus
  and a standing CLAUDE.md push-gate. It found and fixed two engine bugs:
  recordings missing their endings, backfill fed position frames.
- Projectile `firer` (Instigator-pawn chain, live-verified 94/94) and
  projectile `team`.
- Attribution repairs: 330 s wound-correlation TTL; killerEos resolved in
  the log's own id namespace; viewer one-tick hold + death-frame evidence
  outranking stale survived wounds (13 phantom kills corrected across five
  real matches, evidence reviewed and accepted); two-tier tick/Hz display.
- Kill-feed suicide labeling; HTTP/1.1 chunked fix; SPA deep links; doctor's
  collector checks (event-gated verdict, not drift — offsets fine at 10.5.3).
- **Detectors ENABLED on the test box** (2026-08-29, `plugins_config.json`):
  stamina_hack, no_reload (windowed + single-shot rate + midcap
  double-magazine w/ class census), fire_no_ammo (launch billed only to a
  held one-round weapon), remote_mine — plus the default three. Validated:
  zero false accusations over 24 recordings. Alerts are **DB-only**
  (`alert_webhook: null`) until Discord is set up.

**Open decisions (owner: Craig unless noted):**

| Decision | State |
|---|---|
| Stats wishlist reaction (`docs/stats-wishlist.md`) | awaiting team — gates the rest of Phase 1 |
| Shared community-wide ELO | still Recommended, never finally confirmed |
| Discord alert webhook + delivery path (direct vs platform) | deferred by choice |
| Replay retention policy | team discussion pending |
| Main-server stats DB export (cross-version parity corpus 2) | offered, not yet provided |
| speedhack 18.0 borderline | decided: leave; revisit at 20.0 only with wider-archive measurement |
| Production rollout path for the fork | Phase 2/3 decision (see open question 4) |
| HEAD-request support | task chip pending |

**Efficacy caveat that must survive every summary:** detector validation
proves no false accusations; it proves nothing about catching cheaters until
the first real incident — which becomes the first labeled recording and the
day thresholds are reviewed against a true positive.

Make the `.sqrx` recording the single source of truth for every Squad match,
move stats and replay serving onto UNN's community platform (SquidHub, private
repo `unn-corp/Squidhub`), slim the game-server agent toward record-and-upload,
and finish with an audited, admin-only live view. Six phases across two repos.
This document carries the program overview and **this repo's side in full
detail**; platform internals live in the SquidHub repo
(`docs/features/squad-replay-pipeline.md` there).

## Goals

1. **Richer recordings** — improve the replay files and the statistics
   derivable from them. A stat that isn't in the recording can never be
   computed later, so the format grows first.
2. **Serve from the platform** — replays and statistics served by SquidHub
   instead of the agent's built-in interface, eventually including an
   admin-only live match view. The agent streamlines toward producing replay
   files.
3. **Better viewer, redesigned stats** — improve the web replay interface;
   rebuild the statistics experience as a native part of the platform.

## Verified findings the plan rests on

- **Stats-from-replay parity is already the design.** Log-tailed kill events
  are merged into each snapshot *before* it is recorded (`cli.py:1086`), and
  the live stats writer and the replay backfill consume the identical stream
  (`record_tick` callsites `cli.py:1116/1410`). Nothing outside the snapshot
  feeds stats. `stats-backfill`'s docstring states the idempotent-reproduction
  guarantee outright. What's missing is proof, not machinery.
- **The store is multi-server by design.** Every match row is stamped
  `server_id NOT NULL` (`stats.py:48`); defensive logic is scoped per server
  (`stats.py:790`); synthetic match ids hash in a globally-unique community
  scope precisely so a shared central store cannot collide
  (`synth_match.py:25-31`). Per-server attribution is one join away everywhere.
- **Live cheat detection is effectively free.** Detectors are dictionary math
  over a snapshot the reader builds anyway — well under a millisecond against
  a ~2 s tick budget (`plugins/base.py`). Four more detectors are documented
  as blocked *only* on fields the reader doesn't record yet
  (`plugins/cheat_detect.py:26-51`).
- **The platform has prior art.** SquidHub already ingests external game data
  into its own stat tables and serves it (SquadJS integration and one other
  game); replay ingestion is a third instance of an existing pattern, with
  object storage, a task queue, RBAC, and audit logging in place.
- **Viewer limits are structural and known.** Whole recording decoded into RAM
  before playback (`frontend/src/api/recordings.ts:23`), kill-feed precompute
  synchronous on the main thread (`api/replay.ts:107`), timestamps re-parsed
  every animation frame (`canvas/MapCanvas.tsx:179`), forward-only playback
  (`api/playback.ts:108`).
- **Range requests must come from object storage.** The platform's app routes
  don't serve HTTP Range; its object store's presigned URLs do. Chunked replay
  loading targets presigned URLs, never an app proxy route.

## Decisions

| # | Decision | Status |
|---|----------|--------|
| 1 | Cheat detection stays live on the agent; alerts additionally surface on the platform; new detectors also run at ingest, retroactively, via the offline runner | Decided |
| 2 | Live viewing is admin-only, access-controlled, audited. No public live surface, ever (`tests/test_public_no_live.py` stays) | Decided |
| 3 | Platform stats via **projection, not a port**: the unmodified sqreader engine runs as a subprocess (per-file `stats-backfill`), writing its own SQLite working store; a small adapter projects results into the platform DB, which is all the website queries | Decided |
| 4 | Pre-change history is frozen, not migrated. Platform stats start empty ("season one"); the freeze line is the **agent upgrade date** — post-upgrade recordings are backfillable whenever the pipeline lands | Decided |
| 5 | One viewer codebase (this repo's `frontend/`), mounted on the platform against the same recordings API contract. The agent's built-in stats surface freezes | Decided |
| 6 | **Seeding is excluded at the source.** All servers (2–3) mix seeding and competitive play. Primary key: the snapshot's `gameMode` field literally reads `"Seed"` on seed layers (verified against a real recording), so exclusion keys on that, with a config layer-name pattern list as the override hatch for scrims/events. A seeding match is never recorded: no file, no stats, no upload. Cheat detection still runs live during seeding (plugins consume snapshots, not recordings) | Decided |
| 7 | Replays are public — no auth wall (presigned file URLs, standard expiry). Recommended default, separable: platform-convention rate limiting on the public JSON routes to keep scrapers off the DB; invisible to real viewers | Decided |
| 8 | Shared ELO: with seeding excluded at the source, everything ingested is competitive — one combined engine store rates it all, one community-wide rating per player. Per-server leaderboards from DB filtering; true per-server ratings possible later by replaying the archive through extra stores | Recommended — needs final yes |
| 9 | Track upstream; keep the fork merge-compatible (see CLAUDE.md hard rule). UNN specifics in config/entitlements; general work as clean upstream-offerable commits; merge after each upstream release | Recommended |

## Build-out order

Sequenced by dependency: the format grows before anything consumes it, the new
pipeline proves itself before the old one is retired, UI lands on contracts
that exist. Phase 4 is the designated parallel track.

### Phase 1 — Replay completeness & enrichment (this repo, branch `Replay-Improvements`)

- **Parity harness first.** Record real matches; compute stats live and via
  backfill from the same recordings into two DBs; diff field-by-field. Close
  every gap found (prime suspect: the `unverified` shutdown-resume path). This
  is the proof that unlocks everything downstream, and it does **not** wait on
  the stats wishlist.
- **Enrich the format** (wire v2 → v3): the fields the four blocked detectors
  need — sprint stamina, `bFiring`/`bReloading`, `ShovelAction`, mine
  `OwnerPlayerState` — plus whatever the stats wishlist decides. Recording
  gains are forever; gaps are forever too.
- **Seek index** in the format (or sidecar): per-frame byte offsets so the
  viewer can later fetch ranges instead of whole files.
- **Seeding-layer exclusion**: config pattern list (default `*Seed*`); the
  recorder skips those matches entirely.
- **Bi-versioned consumers**: Python encoder, browser decoder, and
  `crosslang.test.mts` all extended; v2 recordings keep playing everywhere.
- Tooling: `stats-backfill` reads `serverId` per recording from its meta
  instead of one `--server-id` flag per run.

**Done when** N production matches show zero stats diff between live and
replay-derived computation, and a v3 recording plays alongside a v2 one.

### Phase 2 — Platform ingest & serve (SquidHub repo; contract summary here)

What this repo must provide: HMAC-authenticated upload of finished `.sqrx` +
meta from the agent (`ingest_client` retargeted via entitlements/config); the
engine invocable per-file in match-start order; plugin alerts relayed with
their match id. Platform detail — worker, storage, projection tables, viewer
mount, dual-run validation — lives in the SquidHub doc.

**Done when** a match finishing on a game server appears on the platform —
watchable replay, projected stats — matching the agent's local DB, hands-off.

### Phase 3 — Record-only agent mode (this repo)

Config mode disabling the local stats DB and public HTTP surface: the agent
records, uploads, retains, self-updates, reports health, and keeps running
plugins live. Built-in dashboard formally frozen. UNN deployments flip only
after Phase 2's dual-run has held. Nothing is deleted.

### Phase 4 — Viewer improvements (this repo, parallel-friendly)

Cheap wins first: cache numeric timestamps once at load; move decode +
kill-feed precompute into a worker. The structural one: chunked seek-on-demand
loading using the Phase 1 index against presigned Range URLs — start latency
and heap stop scaling with match length. UX on top of a smooth base.
Everything except chunked loading can start any time.

### Phase 5 — Stats redesign (SquidHub repo)

Platform-native pages over the Phase 2 tables; per-server and community-wide
views from the same rows; fresh leaderboards framed as season one. The page
designs feed back into the projection shape — sketch early, ship last.

### Phase 6 — Admin live view (both repos)

Agent-side relay pushes live ticks to the platform over an authenticated
persistent channel (the only genuinely new transport in the plan); platform
side authorizes at connect, audits every session, and feeds the viewer's
dormant live mode (snapshot buffer and render-delay machinery are intact).

## Agent-side infrastructure

- **Push target**: point enrolment / `ingest_client` at the platform endpoint
  (build entitlement or config); backlog-and-flush already handles the
  platform being briefly down.
- **Early rollout**: deploy Phase 1 agents as soon as the format lands — this
  sets the history freeze line and banks ingestable recordings before the
  pipeline exists.
- **Local retention**: keep pruning as today; once uploads are confirmed
  durable centrally, local copies can be shorter-lived.
- **Record-only flip**: Phase 3 config change per server, gated on dual-run
  parity.

## Risks & guardrails

- **Two implementations of anything.** The 4 Hz decode bug and the
  fabricated-winner bug both survived because nothing compared two
  implementations. Guardrails: engine-as-subprocess (one stats
  implementation), the cross-language decoder fixture, Phase 2 dual-run.
- **Retiring the old path too early.** Phase 3 is gated on dual-run parity,
  not the calendar.
- **Format immutability.** Version stamps, bi-versioned decoders, no-guess
  applied to every new field (absent beats invented).
- **ELO order dependence.** Feed matches in match-start order; on out-of-order
  arrival (offline backlog flush), run the deterministic full recalc
  (`recalc_all_elo`) as a scheduled job rather than letting insertion order
  rewrite ratings.
- **Ghosting via live view.** Admin-only authorization at connect, per-session
  audit, no public live surface anywhere.
- **License boundary.** sqreader is AGPL + Commons Clause; the platform is
  private. The engine runs as a separate program (CLI subprocess exchanging
  files and DB rows) — never imported into the platform process. The public
  fork satisfies the AGPL source obligation for the agent.

## Open questions

1. **Stats/enrichment wishlist** — under exploration. Every stat must trace to
   a memory-verifiable signal (no-guess). Candidates: blocked-detector fields,
   revive give/receive, vehicle occupancy time, per-role playtime,
   capture-zone participation, distance traveled. Format design waits on
   this; the parity harness does not.
2. **Replay retention** (platform storage) — team discussion pending on disk
   budget. ~15 MB/match ⇒ ~110 GB/year/server at 20 matches/day, less with
   seeding excluded. Needed before the platform retention job is written.
3. **Alert delivery path** — direct Discord webhook from the agent vs routing
   through the platform. Parked; decide before the Phase 2 alert relay.
4. **Production rollout path for the fork** — the public server currently runs
   a managed upstream instance that cannot be modified (its stats DB and
   recordings can be exported). When Phases 1–2 are proven on the test server,
   decide whether the fork replaces that instance or first runs alongside it
   in record-only mode (readers are read-only and can coexist; the cost is
   CPU). Until then, UNN's new pipeline runs on the test server only, and the
   freeze line for production history is whenever the fork reaches the public
   box. Decide during Phase 2/3.

## Glossary

- **Parity harness** — a repeatable check that one match produces identical
  stats computed live during play and recomputed afterward from its recording
  alone: two databases, diffed row by row. The proof that a replay file is a
  complete record.
- **v2 / v3** — recording wire-format version numbers. Today's files are v2;
  enriched files become v3; old files stay v2 forever and every reader in this
  repo handles both. The platform only ever ingests v3 (decision 4).
