# CLAUDE.md — UNNReader-Squad

UNN's fork of [squadreader](https://github.com/cagrianilokumus/squadreader)
(upstream). Reads a running Squad dedicated server's process memory
(Linux-only), records matches into `.sqrx` replay files, computes stats/ELO,
and serves a replay viewer. This fork feeds UNN's community platform —
"SquidHub", private repo `unn-corp/Squidhub` — which ingests finished
recordings and owns the product stats/replay surface.

**The program in one line:** make the `.sqrx` recording the complete record of
a match; SquidHub computes and serves everything from recordings; the agent
trends toward record-and-upload. Full plan: `docs/replay-pipeline-plan.md`.

## Hard rules

- **No-guess policy** (see CONTRIBUTING.md — it binds everything). Attribution,
  stats, and detector signals show only data verified from memory or the server
  log. Unverifiable → `null`/omitted, never inferred. Applies with full force to
  every new recorded field and detector.
- **Stats logic exists exactly once.** `sqreader/stats.py` + `sqreader/elo.py`
  are the only implementation of the stats rules. Never port or duplicate them
  elsewhere — SquidHub runs this engine as a subprocess and projects its
  results into its own DB.
- **Stats-from-replay parity is an invariant.** Everything stats consume must
  be in the snapshot *before* the recorder and `record_tick` see it (log kill
  events are merged in `cli.py` pre-write; keep it that way). Any change that
  makes live-computed and replay-recomputed stats diverge is a bug, not a
  trade-off. It is measurable, so measure it: anything touching `stats.py`,
  `elo.py`, `recorder.py`, `possample.py` or the wire format runs
  `scripts/stats_parity.py` against the test-box corpus before push, and a
  non-zero exit is a failed gate — every diff is fixed or written down in the
  tool's exclusion catalog with its reason.
- **Every new memory read stays doctor-checkable.** Reflection-resolved by
  name wherever possible; any hardcoded offset the reader can read through
  (fallbacks included) is added to `health.hardcoded_offset_tables()` in the
  same change, reflection-only reads get a `required_reflection_names()`
  entry, and anything genuinely uncheckable goes in that function's register
  with its reason. This is what keeps drift loud and self-heal able to repair
  it — see CONTRIBUTING.md "Reverse-engineered offsets"; `tests/test_fleet.py`
  enforces it.
- **Recordings are immutable; consumers are bi-versioned.** Old `.sqrx` files
  must keep playing forever. Any wire-format change bumps the format version
  and extends the Python↔TypeScript cross-language fixture
  (`frontend/src/state/crosslang.test.mts`). The two decoders drifted silently
  once (the 4 Hz bug); the fixture is why it can't happen again.
- **Frozen surfaces.** The built-in stats dashboard and the SQLite read API
  receive fixes only — no new features. New stats/product work happens on the
  SquidHub side.
- **Stay merge-compatible with upstream.** Upstream is the source of
  re-verified memory offsets after every Squad update — protect that lifeline.
  UNN-specific behavior goes in config or build entitlements, never as code
  edits; general improvements are written as clean, self-contained commits
  (upstream-offerable); merge upstream after each of its releases.
- **This repo is public.** No UNN infrastructure details, secrets, hostnames,
  or SquidHub internals in code, docs, or commit messages. Platform-side
  design lives in the private SquidHub repo
  (`docs/features/squad-replay-pipeline.md` there).

## Gates before a push/PR

```
python -m pytest
python -m ruff check .
python -m mypy sqreader
```

If `frontend/` was touched: `cd frontend && npm ci && npm run build` (runs
`tsc --noEmit` first) and `npm test` (plain node scripts via
`scripts/run-tests.mjs` — no test framework; each `*.test.mts` exits non-zero
on failure).

## Environment notes

- Dev happens on Windows; the reader itself only runs on Linux
  (`/proc/<pid>/mem`). The test suite is fixture-based and runs anywhere.
  mypy deliberately checks as `platform = linux`.
- Changelog: entries accumulate under `[Unreleased]` in the house narrative
  voice (cause → consequence, plain hyphens); a release moves them into a
  version section — move, don't copy (1.4.4 once left duplicates behind).
- Commit messages follow the house voice: imperative, story-telling subjects
  ("Read the 4 Hz position lines the encoder was already sending").

## Key docs

- `docs/replay-pipeline-plan.md` — goals, decisions, build-out phases (this
  repo's side in detail)
- `docs/architecture-notes.md` — module map of the backend and viewer, with
  file pointers and the replay-playback pipeline explained
- `docs/offsets.md`, `docs/findings.md` — upstream's reverse-engineering
  record (authoritative for memory layout)
- SquidHub side: `unn-corp/Squidhub` → `docs/features/squad-replay-pipeline.md`
