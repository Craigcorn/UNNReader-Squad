# Stats wishlist — Phase 1 proposal

Status: **awaiting team reaction** · drafted 2026-08-28 from a full survey of
what the snapshot records, what stats consume, and what the game exposes.
React by marking tiers or single items keep/cut; Tier 3 also needs one
live-server session before its cost is known.

## Headline findings (they change the plan's assumptions)

1. **Two of the four "blocked" detectors aren't blocked.** The docstring in
   `plugins/cheat_detect.py:26-51` is stale: `stamina`/`staminaMax` are
   already read (`snapshot.py:2839-2860`) and deployables already carry a
   verified `placer` (`snapshot.py:1772-1780`). `stamina_hack` and
   `remote_mine` can be built **today, retroactively, against banked
   recordings** — and `no_reload` turns out to be derivable from existing
   data too (see Tier 1), leaving only `remote_shovel` needing a new field.
2. **Enrichment is cheaper than the plan assumed.** `.sqrx` stores raw
   snapshot NDJSON verbatim (`sqrx.py:79-81`), so a new snapshot key needs no
   container change, and the browser's key-diff decoder passes unknown scalar
   fields through untouched (`replayUnpack.ts`). The strict v2→v3 boundary
   really applies to the **seek index** and to any new top-level arrays — not
   to added fields.
3. **The compact wire encoder is not in this repo.** The viewer's decoder
   mirrors `central/replaypack.py` (upstream's central platform, private);
   the agent itself serves raw NDJSON with zstd passthrough. Phase 2 choice
   for SquidHub: serve raw (works today, simplest) or reimplement the packer
   (bandwidth/heap win; the TS decoder + cross-language fixture exist to
   validate against). Recommend **raw first, packer later if numbers justify**.
4. **The single highest-value free win is in the server log.** The revive
   line carries reviver name + both EOS ids and is currently discarded — the
   regex only clears correlation state (`logtail.py:50-51,207-215`). Capturing
   it gives verified medic stats with zero memory work.
5. **Precision caveat to state up front:** stats computed in backfill see full
   ticks (~0.5–1 Hz) only — the 4 Hz position frames deliberately don't enter
   `record_tick` (`stats.py:649-664`). Distance/time-in-state stats are
   full-tick accurate; don't promise more.

## Tier 1 — recommend shipping with Phase 1 (zero probing; DB + record_tick work only)

Everything here is **already in every recording**; these stats can even be
computed retroactively. Additive migrations follow the existing template
(`stats.py:109-124`, `_m004` pattern).

| Stat | Source | Why |
|---|---|---|
| **Revive events**: giver→receiver, wound→revive latency, medic leaderboard, revive locations | log line (`logtail.py:50`) + existing tick-join | Resolves the ambiguity in today's `revived_points` counter; biggest community-visible win |
| Incapacitation outcomes: time wounded, bled-out vs saved | `soldier.lifeState/isWounded/isBleeding/isDying` (`snapshot.py:2878-2898`) | Pairs with revives; medic-response metrics |
| Capture-zone participation: possession time, flips, contested seconds | `captureZones[]` (`snapshot.py:1891-1952`) | Answers the plan's open "capture-zone participation" item |
| Deployables placed per player; FOB economy timeline (supply, siege, overrun) | `deployables[].placer` + FOB fields (`snapshot.py:1772-1819`) | Builder leaderboard + match-flow analytics; also the `remote_mine` detector |
| `stamina_hack` detector + exhaustion profile | `soldier.stamina/staminaMax` | Blocked-detector #1, unblocked |
| `no_reload` detector via **ammo-consumption rate** | `soldier.weapon.magazines` (`snapshot.py:2940`) | Sum of carried rounds falls on firing and not on reload (per-magazine system), so rounds-consumed-per-window is verified fire volume; a window exceeding any legit dump+reload cycle is the tell. Sustained-abuse only at tick rate; weapon-swap reset like `infinite_ammo`; resupply only masks toward false negatives. Replaces the `bFiring`/`bReloading` probing need |
| Foot distance travelled / movement profile; stance profile | `soldier.position` per tick; `soldier.stance` | Accumulator already exists verbatim at `stats.py:1075-1082` |
| Squad-level stats: per-squad K/D, SL identity, marker/command activity, rally uptime | `squads[]`, `markers[]`, `rallyPoints[]` | SL/squad leaderboards — community requested territory |
| Vehicle crew: seat-time by seat, engine/component damage taken | `vehicles[].seats/engine/components/turrets` | Extends existing `vehicle_session` |
| Ticket curve over the match | `teams[].tickets` per tick | Match timeline for the redesign; only final value stored today |
| Hit-location (bone) distribution | `damageEvents[].bone` (`snapshot.py:3619`) | Recorded, currently dropped at insert |
| Logi split: ammo vs construction delivered | unfold `snapshot.py:2775-2777` | Two stats where one is stored |
| Ping per match; voice-channel keying time | `player.ping`, `player.voiceChannel` | Server health; commander/SL activity |

## Tier 2 — cheap adds (one reflection-name each, ~an hour incl. test; no probing)

Names come from `docs/findings.md` offsets or already-declared-but-unread
offset table entries; reflection-by-name means a Squad rename blanks the field
instead of reading junk.

| Stat | Add |
|---|---|
| Role playtime, requested-vs-deployed role | `DeployRoleId/DeployRole` → grab list `snapshot.py:1206-1211` |
| Time-on-team, team-switch count | `LastTeamChangeTime` (same list) |
| Squad lock rate | one word: `bIsLocked` missing from grab list `snapshot.py:1237-1243`; the read branch already exists (dead today) |
| Commander identity/uptime per team | `CommanderState` grabbed (`:1229-1232`) but never read |
| Named seat roles (driver/gunner, not index) | `SEAT_CONFIG` offset declared (`:258`), never read |
| Suppression rounds; logi drop rate/quantity | declared offsets `:233`, `:421-425` |

## Tier 3 — needs one live session on the test server before costing

Run `scripts/dump_struct_layout.py --name SQSoldier` on the `squad` box and
grep for `ShovelAction` (and `bFiring`/`bReloading` as an optional refinement
of the Tier 1 ammo-rate `no_reload`):

- Present as a reflected property → `remote_shovel` collapses to Tier 2 cost.
- Absent → it needs a value-correlated memory probe (~a day, like the
  deployable-placer discovery, `snapshot.py:1005-1011`). Only worth it if
  remote digging is a cheat class UNN actually observes.

Also probe on that session: real `SquadGame.log` line shapes for
connect/disconnect/admin actions (no fixtures exist for them in this repo —
treat as unverified until seen).

## Rejected under the no-guess policy

- **Assists** — no real counter exists in the player-state offsets; anything
  else is inference.
- **Squad cohesion / "fought near whom"** — proximity heuristic.
- **Spotting/intel credit** — inference chain across unrelated signals.
- **Per-seat kill attribution** — the seat↔kill join is one tick (~2 s) old,
  the same staleness that keeps the `magic_bullet` detector off. Recommend
  not shipping; revisit only with an explicit "approximate" label.

## Sequencing

- **Tier 2/3 change what lands in the file → they must precede the agent
  rollout that sets the history freeze line.** This is the true format work.
- **Tier 1 changes only what is computed → can land any time**, including
  retroactively against every recording banked from rollout day onward.
- Housekeeping to fold in as standalone, upstream-offerable commits: the stale
  `cheat_detect.py` docstring, and the dead `bIsLocked` branch.
