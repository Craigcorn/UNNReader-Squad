# Implementation plan: the stats parity harness

Status: **approved 2026-08-28 — in implementation** · target branch: `Replay-Improvements`

Read first: `CLAUDE.md` (hard rules), `docs/replay-pipeline-plan.md` (this is
Phase 1's core deliverable), and `docs/plans/detectors-and-seeding.md` for the
execution conventions that worked (same gates, same house voice, same
environment notes — the mypy/numpy quirk and test-server access facts live
there and still apply).

## What this is

The proof that **a `.sqrx` replay file alone reproduces the stats the agent
computed live during the match** — and, from then on, the permanent regression
gate that every future change to the engine, recorder, or format runs through.
The whole SquidHub pipeline (platform computes everything from uploaded
files; agents go record-only) stands on this property. Today it is believed,
not proven; the prime suspect for a gap is the `unverified` shutdown-resume
path.

Two design principles, both are the point:

1. **Schema-driven, not field-listed.** The differ discovers tables and
   columns from the live DB's actual schema. When the stats wishlist lands
   new columns, they are covered automatically, with zero harness changes.
   The harness must be immune to the enrichment churn it exists to protect.
2. **Whole-archive comparison, not per-match.** ELO is order-dependent:
   replaying one match into a fresh DB cannot reproduce a rating that
   depended on prior matches. So the harness replays the **entire recordings
   archive in chronological order** into a fresh DB — exactly what SquidHub
   ingest will do — and diffs the result against the live DB wholesale.

## The tool

`scripts/stats_parity.py` (same home as `plugin_replay.py`; scripts/ is
outside mypy's scope but write it clean anyway).

```
stats_parity.py --recordings-dir DIR --live-db PATH [--out REPORT] [--json]
```

Behavior:

1. **Snapshot the live DB first.** Never read the live file directly — the
   writer may be running. Take a consistent copy via Python's
   `sqlite3.Connection.backup()` into the scratch dir; diff against the copy.
2. Create a fresh temp DB and replay every eligible `.sqrx` in
   match-start order through the **real backfill code path** (reuse
   `cmd_stats_backfill`'s internals or invoke the CLI — do not reimplement
   any engine logic; one implementation is the entire point).
3. **Schema-driven diff**, table by table, row by semantic key:
   - `matches` → `match_id` · `player_matches` → `(match_id, eos_id)` ·
     `players` → `eos_id` · `player_elo` → `eos_id` ·
     `kill_events` → the existing dedupe key
     `(match_id, victim_name, attacker_name, ts, killed, wounded)` —
     never autoincrement ids · `vehicle_session` → implementer picks the
     semantic key and documents it.
   - `plugin_alerts` is **excluded by design**: plugins run live, backfill
     does not run them. Say so in the report, don't silently skip.
   - Values: exact for ints/text; floats get a small documented tolerance
     (each tolerance carries a written reason). Timestamps derive from
     snapshot timestamps, so they are expected exact — if one drifts, that
     is a finding, not a tolerance.
4. **Scoping**: the live DB legitimately holds rows the archive cannot
   reproduce — matches recorded before deployment, matches whose recordings
   were retention-pruned. Rows for match ids with no `.sqrx` present are
   reported as *unscoped* (counted, listed), never as failures. Everything
   with a recording present is in scope, `unverified` matches included —
   that path is a prime suspect, not an exclusion.
5. **Report**: human-readable text (per-table row counts, unscoped counts,
   then every diff with key, column, live value, replay value) plus `--json`
   for tooling. Exit nonzero iff any in-scope diff exists.

## Legitimate-difference catalog

Every diff the first runs surface gets exactly one of two fates: **fixed**
(engine/recorder bug — fixing gaps is in scope for this batch, each its own
house-voiced commit) or **excluded with a written reason in the report
output**. No third bucket, no silent tolerance widening. Candidates to
expect: the shutdown-resume path; rows touched by `finalize_open_match` on a
boot the archive never saw; float accumulation order.

## Tests

- The differ is pure: unit-test it against small synthetic DB pairs — known
  identical, known single-cell diff, unscoped rows, float tolerance edges,
  a column that exists on one side only (schema drift must be a loud
  finding, not a crash).
- End-to-end: build on the existing synthetic/fixture machinery
  (`synth_match`, the recorder/stats test harnesses) — drive synthetic
  snapshots through the live path (recorder + `record_tick`) in-process,
  then backfill the file it produced into a fresh DB, and assert the differ
  reports zero. Mutate one recorded value and assert it reports exactly one.

## Corpora and bars

1. **Test-box archive vs its live DB** (same code, same box) — **the gate:
   zero unexplained in-scope diffs.** Copy both from the server to this
   machine for iteration (the DB via the backup API, recordings via scp);
   re-run on-box at the end to confirm against the genuinely live article.
2. **Main-server replays vs the exported main-server stats DB**
   (cross-version: upstream's agent computed the live side) —
   *informational, no gate*: catalog and explain differences; they preview
   what ingesting upstream-recorded files would look like. **Requires the DB
   export from Craig** — if it hasn't arrived, run corpus 1 and note this
   pending.

## Definition of done

- Differ + end-to-end tests green; full gate passes; changelog entry under
  `[Unreleased]` (house voice).
- Corpus 1 runs clean: zero unexplained in-scope diffs, with any real gaps
  found along the way fixed in their own commits.
- Corpus 2 run and written up if the export is available.
- `CLAUDE.md` gains one line under the hard rules: changes touching
  `stats.py`, `elo.py`, `recorder.py`, `possample.py`, or the wire format
  run `scripts/stats_parity.py` against the test-box corpus before push.
- Report format stable enough that the next batch (wishlist Tier 1) can use
  "harness stays green" as its own acceptance bar.

## Out of scope

Wishlist stats and any format change (next batch, through this harness);
the `projectiles[].firer` probe (own session — needs live fire; note the
`dump_struct_layout.py` quirks hit on 2026-08-28: its pid-finder assumes
upstream's filesystem layout, and `--name SQProjectile` resolves nothing on
this box — the probe session should budget for fixing the tooling first);
`captures` semantics; SquidHub; CI wiring; any scheduled/continuous runs.
