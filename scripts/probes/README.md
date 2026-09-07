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

- **Dump whole layouts, never ask for names.** When a probe first meets a
  class it records the class's complete reflected layout, not the handful
  of properties the day's question needs. The 09-04 marker enumeration
  asked 163 classes for three names and missed the `Action` class pointer
  sitting between them; the 08-30 archive had held every command marker's
  full layout all along. A full dump costs nothing and is what the next
  question is answered from.
- **Skip the class defaults.** Every class has a `Default__` object that
  passes a class filter and never changes; on an empty server the marker
  probe listed three of them as placed markers (2026-09-07 smoke test).
  Read a CDO on purpose — the action configs are read that way — and skip
  them everywhere else.
- **Stop a probe from a script file, never from an inline command.** An
  ssh command that contains a probe's path kills its own shell under
  `pkill -f`: the bracket trick guards the pkill's own line, not the
  wrapper that carries the whole command. Run the stop as a script file
  (`stop.sh` in the session folder) or hide the path in a shell variable
  (2026-09-07, the sixth time this bit the project).
- **Chat is not in the server log.** Step marks typed in all-chat land
  nowhere; the log carries possessions, damage traces and revives. Time a
  session from the operator's probe reads, and restart every probe after
  a layer roll — class addresses churn (2026-09-07).
