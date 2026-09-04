# Program tracker

The single register of this repo's side of the replay program: what is
being built and where it stands, what has not been decided, and what was
talked through without a decision. Seeded 2026-09-04 from the plan, the
topic docs, the changelog, the session notes and the branch history since
2026-08-28. Read this first when resuming.

## Rules

1. One row per item. Ids are permanent and never reused.
2. A row's state moves only on evidence — a commit, a deploy, a test
   result, a dated agreement — and the row names it.
3. A commit that changes an item's state updates its row in the same
   commit, the way the changelog rule already works. An agreement reached
   in discussion is recorded in the commit that writes it down.
4. A discussion that ends without a decision gets a row in table C, so
   "not decided" is on record and cannot be mistaken for decided or
   forgotten.
5. Status lives here and nowhere else. The plan's narrative sections are
   dated history, a doc's own status line describes that doc, and session
   memory holds pointers to ids, never states.
6. Before stating that an item is pending, done, decided or undecided —
   human or agent — read this file and `git log`.
7. Rows are never deleted. A finished item keeps its final state; a
   decision, once made, moves to the "made" list at the end of table B
   with its date and where it is recorded.
8. Two registers, one home per fact. Platform-side work is registered on
   the team's development board (decided 2026-09-04): the Squidhub
   project's "Game Integrations" milestone, every issue titled
   "Squad Reader: …". Program-wide decisions (table B) live here and are
   referenced by id from the board and the platform design doc; platform
   work lives on the board and is referenced here by issue key (SQH-1148
   to SQH-1153 as of 2026-09-04). Neither restates the other's state. Notes that cannot enter this public repo
   live in the Misc folder on Craig's machine and are listed in W87.
9. A testing session's checklist is authored FROM this register at prep
   time (decided 2026-09-04; wording clarified the same day). Three
   steps, all human-or-agent judgment, none of it a text-match or a
   tool: whoever preps the session reads the whole register and picks
   every row whose next step names the session or needs what it offers
   (live players, a drone, a populated recording); then expands each
   picked row into concrete steps and the evidence to capture, following
   the row's "detail lives in" pointers; and Craig reviews the resulting
   one-session checklist before play. The topic docs' run sheets remain
   the procedure reference the checklist draws on — how to fly it, which
   capture script, how to decode — never the register of what rides the
   session. A row that wants a session says so in its own next column;
   that is what the sweep finds, and the pre-play review is the backstop
   for what it misses.
10. Ids travel with their meaning (decided 2026-09-04). A row cited in
    chat, a commit message or a doc — W16, D4, C4 — is cited with
    enough of its item and state that the reader never has to open this
    file mid-sentence to learn what they are being told. The id is for
    finding the row afterwards, not a substitute for saying what it is.

States for work items: `discussed` → `decided` → `specified` →
`implemented` → `committed` → `deployed` → `verified`; also `parked`,
`reverted`, `deferred`, `blocked`, `standing` (a permanent check).
`verified` means checked live or against a real recording, not merely
tests green. Owner is Craig unless a row says otherwise. Commit hashes
are on `Replay-Improvements`.

## A. Work items

### Recording format and capture

| id | item | state | detail lives in | next |
|---|---|---|---|---|
| W1 | Two-tier recording (`--hz 1 --record-hz 4`): full frames plus 4 Hz position frames | verified 2026-08-28; live on the test box since (the only two-tier deployment) | plan "Program state"; `docs/plans/detectors-and-seeding.md` | none |
| W2 | Seeding exclusion at the source (`gameMode` reads "Seed"; config pattern override; test box opts out) | verified 2026-08-28 (e633774) | plan decision 6 | none |
| W3 | Match ending frames (`endFrames`) | verified 2026-08-28 (b4fbd9e) | changelog | none |
| W4 | Projectile `firer` (reflected Instigator chain) and `team` | verified 2026-08-28 (3487994, 92675a3; 94/94 live) | changelog; `docs/findings.md` | W24 |
| W5 | Emplacement crew capture: a built gun is an `SQDeployableVehicle`, joined to its baseplate by `owningDeployable` | verified 2026-08-29 (03e74ff, a7f7b5b, 16589d9) | plan "Program state" | none |
| W6 | Emplacement ammo and aim (`turrets` shape plus `pitch`) | verified 2026-08-29 (3fefbc8, cc0a082) | plan "Program state" | viewer polish is W73 |
| W7 | Squad lock flag (`bIsLocked`) | deployed 2026-08-30 (dbc747f) | changelog | none |
| W8 | Per-player last team-switch time | deployed 2026-08-30 (35bf153) | changelog | none |
| W9 | Seat names from the game's own seat config | deployed 2026-08-30 (27ffc06) | changelog | none |
| W10 | Commander identity on the team record | deployed 2026-08-30 (9bc0b22, ac0659e) but **defective**: it reads the CommanderState actor as a PlayerState and emits nothing | `docs/command-assets.md` "Confirmed recorder bug" | fixed inside W18 (one hop through `CurrentCommander`) |
| W11 | Medical capture: `players[].soldier.medical` while a healing item is held, `reviveEvents` from the log | verified live 2026-08-30 (eea3470, 994e1dc); deployed | `docs/schema.md` "Medical capture"; `docs/medical-stats.md` | one check owed: a real in-game revive seen inside a `.sqrx` (only live output has been checked) |
| W12 | Vehicle seat inventory: `turrets[].weapons` and a `seat: "driver"` record | verified 2026-09-04: committed and pushed (6546d35), deployed the same day once the bombing test was over, live-verified on the updated build (36 turret records with `weapons`, 11 driver records, at 0 players) | `docs/schema.md` "Vehicle seat inventory"; `docs/findings.md` 2026-09-03 | eyeball a populated-match recording in the viewer when one lands |
| W13 | 4 Hz projectile sampling | shipped and reverted the same day, 2026-08-29 (b25e656, bd79d14); the decoder keeps the optional `projectiles` key in position frames | changelog; session note | re-enabling is undecided: C1 |
| W14 | Command-asset exploration: request markers, commander state, cooldown model, per-call actors, bombs, drones (three live sessions) | verified 2026-09-03; all nine format decisions closed 2026-09-04 | `docs/command-assets.md`, `docs/drones.md` | W15 |
| W15 | Commander capture spec: a contract-only `docs/command-assets-spec.md` (wire shapes, viewer rules, doctor names, run sheets, one open table), then a fresh-session comparison against the journal on a written checklist | decided 2026-09-04; not started | this session's review found 8 conflicts and 5 gaps for it to resolve | after W16; order agreed 2026-09-04: tracker → rule commit (done) → session B → spec |
| W16 | Session B: solo recon-drone capture settling the drones doc's D1 (flight time), D3 (pickup/redeploy identity), D4 (speed), D7 (team) | decided 2026-09-04; tracker script staged on the box; not run | `docs/drones.md` run sheet (procedure) | run whenever Craig has 30 minutes: build the checklist by the rule-9 sweep first, then decode into the drones doc |
| W17 | Implementation plan for the commander, command-asset and drone capture | not started | W15's spec is its input | after W15 |
| W18 | Implement decisions 1–7: commander block and `gameState.commanderRules` on every full frame, marker `distance`/`addDistance`/`yaw`, `commandActions` list, `drones` list plus the 4 Hz drone key, the identity fix (W10), doctor names, schema entries, viewer rules | specified 2026-09-04 | `docs/command-assets.md` "Agreed capture" sections; `docs/drones.md` | after W17; gates: tests, lint, types, frontend build, parity harness |
| W19 | Session A: six-player acceptance run with the probe as the oracle | decided 2026-09-04 | `docs/command-assets.md` run sheet | after W18 |
| W20 | Open commander items that gate nothing: A1 shoot-down attribution (`LastHitBy`), B2 bomb-circle radii (cosmetic), R8 command zones, C1 direct category-gate test (optional), the re-stamp rule for a still-cooling asset at a commander change and the claim rule after a step-down (unobserved branches) | open | `docs/command-assets.md` open table and run sheet | observed during W16 and W19; W15's spec carries them in its one open table |
| W21 | Killfeed memory enrichment never reaches recordings: the serve loop replaces the memory-derived damage events with the log list every tick (since upstream 1.4.0) | found 2026-09-04; unfixed | plan addendum 2026-09-04; session note | design the merge (memory detail onto the matching log event) and run the parity harness; keep it upstream-offerable |
| W22 | Blast origin on radial damage events | parked 2026-09-04 (decision 8) | `docs/command-assets.md` capture gap 5 | preconditions: W21 fixed, W23 answered |
| W23 | Are hand grenades tracked projectiles? | open (none among 664 rounds in one production recording) | `docs/command-assets.md` capture gap 5 | check a production recording that has grenade kills |
| W24 | Projectile `firer` reads null on lingering corpse records (Instigator chain decay suspected) | open | session note projectile-4hz | probe before relying on corpse-time firer joins |
| W25 | Pace the full-frame builder to `--hz` (it free-runs; `--hz` only sizes caches) | deferred, low priority (Craig, 2026-09-03) | plan Phase 4 remedy 1 | none until it matters |
| W26 | Seek index: per-frame byte offsets (a container-layout change) | not started | plan Phase 1 and Phase 4 | bundle with any other container change; direction is D7 |
| W27 | Compact packer, built and owned here | not started; after the format additions land | plan Phase 4 remedy 2 | acceptance: pack-and-unpack a real recording of the finished format and require equality with the raw frames; generate the cross-language fixture from a recording that carries every registered key (written into plan Phase 4 remedy 2, 2026-09-04) |
| W28 | Frame-key register in `docs/schema.md` (every top-level key of the full and position frames, date added, keyed or not), and retire that doc's kickoff-era preamble (upstream's Phase 0/1/3 text) | done 2026-09-04: `docs/schema.md` "Frame-key register", kickoff-era preamble retired | `docs/schema.md` | a commit that adds a top-level key adds its row in the same commit |
| W29 | `stats-backfill` reads `serverId` per recording from its meta instead of one `--server-id` flag per run | not started (the flag is still the source, `cli.py`) | plan Phase 1 tooling | any time |
| W30 | Stats wishlist Tier 2 fields | partly shipped (W7, W8, W9, W10); suppression rounds and logi drop fields not done | `docs/stats-wishlist.md` | gated on D1 |
| W31 | Stats wishlist Tier 3 probe (`ShovelAction`; `bFiring`/`bReloading`) → the `remote_shovel` detector | not started; needs one live session | `docs/stats-wishlist.md` | gated on D1 |
| W33 | Stats engine additions: every new statistic (wishlist tiers, the medical catalog, revives, capture-zone participation, squad and vehicle boards) is computed once in `stats.py` / `elo.py` and written to the engine's store; the platform only projects and displays (W82). The built-in dashboard stays frozen, the engine does not | gated on D1 and D11; nothing built yet beyond the recording-side fields | `docs/stats-wishlist.md`, `docs/medical-stats.md`; CLAUDE.md hard rules; board SQH-1152 projects what this produces | after D1; parity harness on every change |
| W32 | Second Squad update since the doctor learned to heal: offset table refreshed to the layout the resolver had already proved | verified 2026-09-04 (6b2ed9f, pushed and deployed; parallel session): the update landed during the W12 deploy and the self-repair absorbed it before the refresh | changelog Unreleased; session note doctor-hardening | none |

### Detectors and alerts

| id | item | state | detail lives in | next |
|---|---|---|---|---|
| W40 | Four detectors (stamina_hack, no_reload, fire_no_ammo, remote_mine) plus the default three | verified: implemented 2026-08-28, enabled on the test box 2026-08-29, zero false accusations over 24 recordings | `docs/plans/detectors-and-seeding.md`; plan "Program state" | none |
| W41 | Alerts leaving the database: Discord webhook | not started; alerts are DB-only until Discord is set up | plan "Program state" | needs D3 and the webhook |
| W42 | Speedhack 18.0 borderline | resolved 2026-08-29: a hull-rider standing on a moving BMP-1's roof, correctly flagged; seat tracking fine | plan (formerly its open-decisions table) | none |
| W43 | Detector efficacy | standing caveat: validation proves no false accusations, nothing about catching cheaters until the first real incident, which becomes the first labeled recording and the day thresholds are reviewed | `docs/plans/detectors-and-seeding.md` | on the first incident |

### Doctor and offsets

| id | item | state | detail lives in | next |
|---|---|---|---|---|
| W50 | Doctor hardening: machine/human parity, batched resolution, struct-internal tier, collector verdicts on the machine path | verified 2026-08-31 to 09-01 (4903a60, e8777cb, 1ce55da, ce6dc4e); 24 h soak passed | session note doctor-hardening; changelog | none |
| W51 | 2026-08-31 Squad update: 48 constants refreshed with the doctor dictating | verified (f04399b); recordings 2026-08-31 03:07–03:54Z carry junk positions and are excluded from positional use and parity | changelog | none |
| W52 | Upstream 1.4.5 merged and hardened (anchor gaps declared, served pack outranks the binary, `stale_source` telemetry) | verified; deployed to the box at b192740 (2026-09-02) | changelog; session note | merge upstream again after its next release |
| W53 | Placer slots live confirmation on a populated match (the probe saw 0 players) | pending | session note doctor-hardening | first populated match with a fresh recording |
| W54 | Upstream-offerable doctor findings (reflected `VehicleComponentState` and `CachedVehicleInventory`, declared gaps plus the gap-consistency test, the relative-move fix, dict-entry anchors) and the W21 fix once made | banked, not sent | session note doctor-hardening | offer as clean commits after the next upstream merge |
| W55 | Offset central: UNN signs its own packs (platform is the authority, test box the verifier, the existing client the consumer) | decided with Craig ~2026-09-01; design in the Misc folder (W87), destined for the private repo; the prerequisite "intersection verdict" fix is not found in the branch under that name — status unverified | session note doctor-hardening; Misc `squidhub-offset-central-design.md`; board: SQH-1151 | verify whether the prerequisite landed (feded6d is pack precedence, not the same thing); platform side is SquidHub work |
| W56 | The standing acceptance test: within one check-in of a Squad update the doctor reports everything that moved as `stale_source` | standing; passed twice (2026-08-31, and the update handled by W32) | session note doctor-hardening; changelog | whoever is live on the next update goes and looks |
| W57 | HEAD-request support in the built-in HTTP server | task chip pending | session note test-server | any time |
| W58 | Drift-automation lanes: an auto-refresh PR bot for the paperwork lane (`stale_source` → a PR carrying the resolver's measured was→now values, human-merged) complementing the pack central (W55), which rescues only unresolvable `drift` | discussed 2026-09-04 — the W32 update proved self-repair makes the rescue lane rare and the paperwork lane routine (the 6b2ed9f refresh was by hand exactly what the bot would have opened) | session note doctor-hardening; changelog Unreleased | Craig: decide scope and order — it is one of D9's seven proposals, recommended alongside severity classes as the first built; the doctor already emits the exact values, so the spec is mostly plumbing |

### Stats and parity

| id | item | state | detail lives in | next |
|---|---|---|---|---|
| W60 | Stats parity harness (`scripts/stats_parity.py`) | verified 2026-08-28; corpus 1 green; a standing CLAUDE.md push gate | `docs/plans/parity-harness.md` | corpus 2 needs D6 |
| W61 | Attribution repairs: 330 s wound-correlation TTL, killer id resolved in the log's namespace, death-frame evidence, one-tick hold | verified 2026-08-29 (13 phantom kills corrected across five real matches, reviewed and accepted) | `docs/plans/attribution-repairs.md` | none |
| W62 | Small fixes batch: suicide labels in the kill feed, HTTP/1.1 chunked, SPA deep links, doctor collector verdicts event-gated, two-tier tick display | verified 2026-08-28 | changelog | none |
| W63 | Parity corpus 2 (the managed production instance's stats DB) | blocked on D6 | `docs/plans/parity-harness.md` | run when the export arrives |

### Viewer

| id | item | state | detail lives in | next |
|---|---|---|---|---|
| W70 | 2026-08-29 viewer batch: an emplacement drawn as one thing, mortar elevation, guided-missile trails and interpolation, dead-missile handling | verified 2026-08-29 | changelog | none |
| W71 | Per-weapon rows in the vehicle panel | deployed 2026-09-04 with W12 | changelog | none |
| W72 | Viewer rules for the commander work: 50 m request circle, pending/approved icons, swap-blip bridging, shape by marker type, director de-duplication, "ready in" arithmetic with its two special cases, bomb rendering, SquadCalc display names | specified 2026-09-04, scattered across the journal | `docs/command-assets.md` | gathered into one list by W15; built in W18 |
| W73 | Emplacement ammo, aim and health-join surfacing | deferred (Craig: "UI later") | plan "Program state" | none until asked |
| W74 | Viewer scalability measured against production-sized recordings | done 2026-09-03 | plan Phase 4 | feeds D7 |
| W75 | Chunked seek-on-demand loading | not started; needs W26 | plan Phase 4 remedy 3 | after D7 |
| W76 | Viewer cheap wins: cache numeric timestamps once, decode and kill-feed precompute in a worker | not started | plan Phase 4 | any time |

### Platform and rollout (this repo's obligations; platform work is registered on the platform side, W86)

| id | item | state | detail lives in | next |
|---|---|---|---|---|
| W80 | Phase 2, this repo's side: authenticated upload of finished recordings, the engine invocable per file, alerts relayed with their match id | not started | plan Phase 2; platform side: `docs/features/squad-replay-pipeline.md` in the private repo (see W86 for its state); board: SQH-1149 | after the format additions |
| W81 | Phase 3: record-only agent mode | not started; gated on Phase 2 dual-run parity | plan Phase 3; the dual-run gate is the last item of SQH-1149 | after W80 |
| W82 | Phase 5: the platform's stats pages and the projection shape they need (leaderboards, match pages, season-one framing, per-server and community views). Never computes a statistic: every rule lives once, in the reader's engine (W33) | not started (platform work) | plan Phase 5; platform doc as W80; board: SQH-1152; CLAUDE.md "Stats logic exists exactly once" | after W80; page sketches early because they feed back into what the engine must store |
| W83 | Phase 6: admin-only live view | not started | plan Phase 6; platform doc as W80; board: SQH-1153 | last |
| W84 | Early rollout of Phase 1 agents, which sets the history freeze line | blocked on D5 | plan "Agent-side infrastructure"; the same D5 gates board SQH-1149 | after D5 |
| W85 | Platform retention job | blocked on D4 | plan open question 2; board: SQH-1150 (step 2) | after D4 |
| W86 | Platform-side register and design-doc sync. Found 2026-09-04: the platform design doc (`docs/features/squad-replay-pipeline.md`, private repo) exists only on the unmerged branch `docs/squad-replay-pipeline` (one commit, 2026-08-28); it restates open decisions D1–D4 as its own open-items list, and carries two facts corrected here since (the retired "v3" label; storage math of 15 MB per match, since measured at 60–100 MB); the team board has no milestone or issue for the pipeline. Read in full 2026-09-04: a sound outline of the platform's conventions, but it predates platform code that has since shipped (the canonical Steam64↔EOS identity model; Squad server groups) and it never meets what the agent actually sends — `recordingState` (an `unverified` file closed at shutdown while the match may still be live, the parity harness's prime suspect), a match split across files by a mid-match restart, idempotent re-upload from the agent's backlog flush, and per-file recorder and container versions; its serving section assumes 10–20 MB files served whole and must follow D7 | found 2026-09-04; register decided the same day (the team board, "Game Integrations" milestone, "Squad Reader" title prefix); six board issues created 2026-09-04: SQH-1148 doc refresh (todo), SQH-1149 Phase 2, SQH-1150 retention (todo), SQH-1151 offset central, SQH-1152 Phase 5, SQH-1153 Phase 6; doc refresh not started | this file; the platform doc | owner Craig with the platform team: merge the doc; replace its open-items list with pointers to D1–D4 and its two stale facts with pointers to the plan; the doc issue is land-and-refresh, not land-and-point: the three sync fixes, the identity and server-group sections rewritten against shipped platform code, and a new section on what the agent sends with the unhandled cases listed as open design points; the ingest rules for those cases are the design step of the Phase 2 issue, and the serving section waits on D7. Open the "Squad Reader" issues: the doc refresh now, one parent per phase carrying its components as a checklist (split into sub-issues when picked up), retention as decision-then-job, the offset central on its own; then W80–W85 cite the issue keys |
| W87 | Private notes in the Misc folder (kept out of this public repo): the offset-central design, destined for the private repo beside the pipeline doc and carrying its own open questions plus the prerequisite fork fix (W55); the gamepanel host-networking change on the test box (test environment only, not platform or production work; applied live 2026-08-27 with backups beside the patched files; no action); the 08-27 deployment findings write-up; three stat-feasibility reviews that feed Phase 5; the doctor-hardening plan; the 08-31 incident write-up (D9); the medical capture plan; the detector validation and attribution evidence reports; the speedhack verdict; parity reports | inventoried 2026-09-04; two stale status lines corrected (the doctor plan said awaiting go, the medical plan said ready to implement) | the Misc folder | the offset-central design moves to the private repo under board SQH-1151 |

### Program hygiene

| id | item | state | detail lives in | next |
|---|---|---|---|---|
| W90 | This tracker, and the rule that keeps it current | created 2026-09-04; rule in CLAUDE.md and in session memory | CLAUDE.md "One register" | keep it current |
| W91 | Stale status lines corrected on seeding: the medical stats catalog said its capture was pending (shipped 08-30); the detectors plan said enablement awaited a decision (enabled 08-29); the session memory index said the medical capture was pending; the seat-inventory note's summary said it was undeployed | done 2026-09-04 | this file | none |

## B. Open decisions

| id | decision | owner | must be made before | inputs needed | detail lives in |
|---|---|---|---|---|---|
| D1 | Stats wishlist reaction: keep or cut by tier or item | the team | the agent rollout that sets the history freeze line (Tier 2/3 change what lands in the file) | `docs/stats-wishlist.md`; Tier 3 needs one live session for its cost | `docs/stats-wishlist.md`; gates board SQH-1149 and SQH-1152 |
| D2 | Shared community-wide ELO (one engine store rating everything; per-server boards by filtering) | Craig | Phase 2 projection | none outstanding; recommended in the plan | plan decision 8; gate of board SQH-1149 (engine-store scope) |
| D3 | Alert delivery path: direct Discord webhook from the agent, or routed through the platform | Craig | the Phase 2 alert relay; also gates W41 | none | plan open question 3; decides the alert-relay item of board SQH-1149 |
| D4 | Replay retention policy | the team | the platform retention job (W85) | measured: 60–100 MB per match on this fork, roughly half a terabyte per server per year at 20 matches a day | plan open question 2; board: SQH-1150 (step 1 is the decision) |
| D5 | Production rollout path for the fork: replace the managed instance, or run alongside it record-only first | Craig | Phase 2/3; sets the freeze line (W84) | Phases 1–2 proven on the test server | plan open question 4; gate of board SQH-1149 |
| D6 | Main-server stats DB export for parity corpus 2 | Craig | W63 | the export | `docs/plans/parity-harness.md` |
| D7 | Viewer scalability direction: the packer we own (W27), chunked loading with the seek index (W26, W75), or both; and whether any further list is keyed | Craig | the last format addition that touches the file layout — the seek index rides the same container bump as anything else that does | a production-sized measurement on the finished format; additions still ahead: W18, the wishlist (D1), C1 | plan Phase 4 "Viewer scalability"; decides the serving component of board SQH-1149 (SQH-1148 marks that section as waiting) |
| D9 | The seven doctor improvements proposed in the 2026-08-31 incident write-up (severity classes the top pick) | Craig | none forced; before the next Squad update ideally | the write-up (private notes) | session note doctor-hardening |
| D10 | Where the drone wire shape lives: the spec (recommended, so every wire shape has one home) with the drones doc keeping findings and tests, or the drones doc | Craig | W15 | none | this session |
| D11 | Medical stats catalog: which stats to build, later-phase | the team | Phase 5 | `docs/medical-stats.md` | `docs/medical-stats.md`; gate of board SQH-1152 |
| D12 | Offset-central design review (W55): where the signing key lives in the platform deployment; the channel strategy if a second server runs a different fork release; whether a pack's application is reported back in the next check-in; when to offer the doctor hardening upstream | Craig | before the central is built (a "Squad Reader" issue on the board once the design moves to the private repo) | the design doc in the Misc folder (W87) | Misc `squidhub-offset-central-design.md`, "Open questions for review"; board: SQH-1151 |

**Made** (for the record; the text lives where cited):

- Plan decisions 1–7 (cheat detection stays live; live view admin-only; stats by projection, not a port; history frozen at the agent upgrade date; one viewer codebase; seeding excluded at the source; replays public) — 2026-08-28, plan "Decisions".
- Track upstream and stay merge-compatible — a CLAUDE.md hard rule.
- Two-tier recording enabled on the test box; detectors enabled — 2026-08-28/29.
- The speedhack 18.0 borderline is a correct flag — 2026-08-29 (W42).
- "v3" is not a version number; additive fields change no version — 2026-09-03 (565dcb2).
- Commander decisions 1–9 (additive fields; the commander block contract; votes derived, not evented; stamps only; marker geometry fields; the `commandActions` list; the `drones` list in the fast tier; blast origin parked; two verification sessions) — 2026-09-04, `docs/command-assets.md` "Agreed capture" sections and `docs/drones.md`.
- Offset central: UNN signs its own packs — ~2026-09-01 (W55).
- Format rule reworded to name its three surfaces (frames additive; the container version moves with the file layout; the packed stream's version with its index-tracked lists), the frame-key register created, the packer's acceptance test written into the plan — 2026-09-04 (was D8); CLAUDE.md "Recordings are immutable", `docs/schema.md` "Frame-key register", plan Phase 1, Phase 4 remedy 2, Glossary.
- The medical capture shape (a per-tick `medical` block while an item is held; revive events from the log) — 2026-08-30.
- Session checklists are built from this register at prep time; topic-doc run sheets carry procedure only — 2026-09-04, rule 9.
- Ids are cited with their meaning, never bare — 2026-09-04, rule 10.

## C. Discussions without a decision

| id | topic | what was established | what is missing | detail lives in |
|---|---|---|---|---|
| C1 | Re-enabling 4 Hz projectile sampling, airborne-only | Impacted rounds linger at their rest point (median 54 s), so full frames already capture the explosion point; an airborne-only sampler would cost roughly 0.5–2 % of a production file and ~3 reads per round; smoothness alone does not justify it; the corpse-linger analysis was done 2026-09-02 | Craig's decision on whether smoother flight paths are worth the cost; not part of the commander plan | session note projectile-4hz; changelog (bd79d14) |
| C2 | 4 Hz ammo sampling (held-weapon magazine sum in the position frame) to shrink the ammo-observation window | Put on the wishlist 2026-08-29 (b02a8d5) as the real fix for the `no_reload` blind spot | a decision, inside D1 | `docs/stats-wishlist.md` Tier 2 |
| C3 | Mirroring this tracker to the team's issue board at milestone level | Raised 2026-09-04; the board has no project for this repo; item-level mirroring would recreate the two-places problem | Craig's view on whether team visibility is wanted | this file |
| C4 | Full player inventory capture: a `weapons`-style list per soldier (every carried weapon/item with magazines), the player-side twin of W12's seat walk | Raised 2026-09-04. Feasibility is high: soldiers carry the same `SQPawnInventoryComponent` the seat walk already reads, held-weapon magazines already read through the same offset, and W12's doctor rows/stride check/autoresolve cover the component — the only new derivation is the soldier's inventory-component pointer. Estimated from W12's measured numbers: ~2–5 ms/tick at 50 players (full frames only, never the 4 Hz tier); storage is the real cost, naive upper bound +25–30 % per full-frame line (~15–30 MB per match), likely +10–20 % after compression. Would close the `no_reload`/`fire_no_ammo` one-slot blind spot and make suppression rounds (Tier 2) and most of C2 derivable. Cheaper variants: guns-only (~half the payload), or C2 as written | The inventory-pointer probe and a walk timing (piggyback on W16, one live soldier suffices); a real storage measurement (piggyback on W19, six players); then Craig's keep/cut decision inside D1, with the storage number feeding D4 | this session; session note seat-inventory-capture |
