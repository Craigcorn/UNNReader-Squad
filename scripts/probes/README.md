# Per-test probes

The capture harnesses behind the tracker's test register (table T; rule 9
in `docs/tracker.md`). The design, decided 2026-09-04:

- **One probe per test, one question per probe.** A harness reaches
  `harness-ready` on its own, independent of any play session, and a
  session's CAN/WILL selection composes probes without editing them. A
  session never gets a merged harness — at most a thin generated launcher
  that starts the selected probes and names where each writes.
- **This directory is the durable home.** A probe graduates here — 
  committed, reviewed, linted — when its test reaches `harness-ready`;
  "staged on the box" then means "arrived with `git pull`". `/tmp` on the
  box is for scratch during development and for probe *output*, never the
  canonical copy: a box cleanup must not be able to silently invalidate a
  `harness-ready` state.
- **Read-only, and loud on the wrong day.** Probes only ever read
  `/proc/<pid>/mem`. Inside a capture loop a bad read is survived and
  logged (a tracker must not die mid-flight); at *startup*, missing
  imports or unresolvable anchors fail immediately and visibly — a probe
  that silently degrades is discovered after the session, which is the
  failure the whole pipeline exists to prevent. No hardcoded fallback
  guesses: constants come from the sqreader modules, which the doctor
  keeps honest.
- **Shape.** Plain scripts, run as
  `.venv/bin/python scripts/probes/<name>.py` on the box; no main guard
  needed (they are never imported by the reader). Shared attach plumbing
  lives in `probe_common.py`. Output is JSONL to `/tmp/<probe>.jsonl`
  unless the test says otherwise; the session's decode step files the
  evidence into the rows the test serves.
