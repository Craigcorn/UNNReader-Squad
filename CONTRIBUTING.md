# Contributing to sqreader

Thanks for helping — sqreader is a community tool and issues/PRs are welcome.

## Dev setup

```bash
git clone https://github.com/cagrianilokumus/squadreader.git
cd sqreader
pip install -e ".[dev]"
```

Real data needs a live Squad server on Linux, but the **test suite runs
anywhere** — it uses fixtures, not a live process.

## Before opening a PR

Run the same gate CI runs:

```bash
python -m pytest           # unit tests
python -m ruff check .     # lint
python -m mypy sqreader    # types
# only if you touched the web UI:
cd frontend && npm ci && npm run build
```

Please keep the project conventions:

- **English code and identifiers; docs may be Turkish.**
- **No-guess policy** — attribution (killer, placer, spotter, …) shows only
  data verified from memory. If it isn't certain, leave the field blank; no
  heuristics or "nearest player" guesses.
- **No new runtime dependencies** without discussion (the reader ships with one:
  `zstandard`).
- Add a test for any new pure-logic helper.

## Sign off your commits (DCO + licence grant)

Contributions are accepted under the [Developer Certificate of
Origin](https://developercertificate.org/). Sign off each commit:

```bash
git commit -s -m "your message"
```

The sign-off certifies you wrote the change, or otherwise have the right to
submit it. **You keep the copyright in what you write** — this is a licence,
not a hand-over.

By signing off you also grant the project's maintainer a perpetual,
irrevocable, worldwide, royalty-free licence to use your contribution and to
distribute it **under the project's current licence and under any later licence
the project adopts**, including a commercial one.

Why the second paragraph exists, plainly: this project is
[AGPL + Commons Clause](LICENSE), so the only party who may sell it is the
copyright holder. Without that grant, changing the licence later — or offering
anyone a commercial exception — would require tracking down and getting written
agreement from every past contributor. A plain DCO does **not** give it: a DCO
certifies where code came from and nothing more. That has already cost this
project a stalled licence change once, which is why it is spelled out here
rather than assumed.

If you are not willing to grant that, say so in the pull request. A change can
still be taken as a suggestion and reimplemented, or simply declined — that is
a better outcome for both of us than a contribution nobody can safely build on
later.

## Reverse-engineered offsets

Memory offsets target a specific Squad build. If a Squad update breaks reads,
`sqreader doctor` reports which offsets drifted; `docs/offsets.md`
explains how they were derived.

**Adding a memory read? It has to stay checkable.** A Squad update that moves
a struct is silent: the reader keeps reading the old address, gets
neighbouring bytes, and ships plausible garbage. `doctor` is what makes that
loud — and it can only check what it is told about. So, in the SAME change
that adds the read:

- **Prefer reflection.** Resolve the field by name in `resolve_paths` and it
  cannot drift at all: a moved field is re-found on the next start, a renamed
  one blanks (an honest null, never a wrong read). Most new fields need
  nothing else.
- **Any hardcoded offset the reader can read THROUGH — including one used
  only as a reflection fallback — goes in `health.hardcoded_offset_tables()`**
  (`sqreader/health.py`). That one table feeds both the human `sqreader
  doctor` and the machine `run_doctor` the self-heal gates on, so a drifted
  constant is both reported and repairable by a signed offset pack. Mark the
  row `optional=True` when the type legitimately may not be loaded in a level
  (no emplacement built, no FOB placed) — absent then means skipped, not
  drift.
- **A reflection-only read with no fallback still needs a line in
  `health.required_reflection_names()`.** It cannot drift, but a rename makes
  it vanish from recordings silently; that entry turns the rename into a drift
  report instead of data quietly going dark.
- **An offset addressed inside a struct rather than on a class has a table of
  its own** — `health.struct_field_tables()`, which walks the struct hops by
  reflection (`LastTakeHitInfo` → `PointDamageEvent` → `FHitResult`) and
  compares the same constants the reader reads through.
- **Can't check it?** Add it to the register in `hardcoded_offset_tables()`'s
  docstring with the reason (unverified property name, no single owning class,
  struct-internal, never read). `tests/test_fleet.py` fails on any offset
  constant that is neither watched nor registered, so the register is the only
  way out and it costs one honest sentence.
