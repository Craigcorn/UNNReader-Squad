# Handoff: finishing the commander & drone capture spec

Written 2026-09-06 for a fresh session. This is orientation, not a
register: every state word below is a pointer to a `docs/tracker.md` row,
and the row wins if they ever disagree (tracker rule 5). Read this, then
the tracker rows it names, then the spec. Do not describe any row or doc
from this handoff's summary of it; open the file (tracker rule 6).

## 1. The work, in one paragraph

The recorder is gaining everything about the commander role, the squad
leaders' tactical requests, the command assets those produce, and every
drone pawn. The contract for what it writes is `docs/command-assets-spec.md`
(tracker **W15**). It was drafted on 2026-09-05 from a week of live
sessions, corrected the same day against a fresh-session review and a
mechanical name check, and had four decisions folded in on 09-05/06. What
remains before the implementation plan (**W17**) is written: two small
observations that gate two fields, and one more fresh-session review of
the final text. That is the whole scope of this handoff.

Principles that decide everything here (CLAUDE.md hard rules): the
recorder writes direct memory reads only, resolved by reflection name;
interpretation lives in the viewer and can be corrected for every
recording at once; additions are additive and never change an existing
field's meaning; nothing is recorded that has not been observed.

## 2. The documents and what each one is

| File | Role | Treat it as |
|---|---|---|
| `docs/command-assets-spec.md` | the contract: five wire surfaces (§3–§7), doctor rows (§8), viewer rules (§9), exhaustive not-recorded list (§10), acceptance (§11), self-check questions (§12), every name as resolved (§13) | stateless; cites tracker ids where an interpretation rests on a test; its §12 question 6 forbids state words about itself |
| `docs/command-assets.md` | the journal: dated findings in the order made, including statements later superseded; the 09-04 "Agreed capture" sections are the decisions the spec transcribes; the 09-05 sections record the reflection, archive decodes and the artillery phases | evidence, chronological; never edit old entries, append dated ones |
| `docs/drones.md` | drone findings (five recon flights 09-05, one commander flight 09-02) and the drone test table with dated results | evidence; its "Wire shape" section points at the spec |
| `docs/tracker.md` | the register: work items, open decisions and the made list, tests (table T) | the only place state lives; rules 1–13 at its top govern how it is written |
| `scripts/probes/README.md` | the probe conventions: one probe per test, canonical in the repo, staged by `git pull`, dump whole layouts | binding for any new probe |
| `docs/schema.md` | the frame-key register; `commandActions` and `drones` sit there as planned rows | updated at implementation, not now |

Probes in `scripts/probes/`: `drone_track.py` (T7 done, T10 pending),
`soldier_inventory.py` (T1), `spec_names_check.py` (T11, live and
archive modes), `marker_action.py` (T12), `probe_common.py` (attach
plumbing). The command-assets probe used in the 08-30 to 09-03 sessions
(`cmd_probe_b1.py`) has **not** graduated to the repo; it lives on the box
in `/tmp` and in the Misc archives, and T8's harness cell says it must
graduate before T8 is harness-ready.

Evidence archives, on Craig's machine under
`C:\Users\CRAIG\Documents\UNN\Misc\`, kept out of the public repo:

| Folder | Holds |
|---|---|
| `command-probe-2026-08-30/` | `cmd_session.tgz`; `cmd_layouts/` extracted to plain JSON (33 classes incl. the creep and UAV actors, the geometry markers) |
| `command-probe-2026-09-02/` | `cmd_session2.tgz`; `cmd_layouts/` extracted to plain JSON (41 classes incl. the mortar, drone, strike actors, the action CDOs, the commander state and manager) |
| `command-probe-2026-09-03/` | `b1_session.tgz` (the bombing session: bomb tracks, take-hit struct, log slice) |
| `command-probe-2026-09-05/` | `drone_track.jsonl` (five flights), `struct_layouts_0905.txt` (the four inner structs and the two marker masters, live), `spec_names_check.live.jsonl` / `.archive.jsonl` (T11 output), `creep_raw_0830.jsonl` (the 08-30 creep call's per-tick raws, source of the D14 values), both drone class layouts, `soldier_inventory.jsonl` |

Layout JSON shape: a flat dict, property name → `{"offset": int,
"type": "…Property"}`, merged over the class chain. The
`spec_names_check.py --archive DIR…` mode reads these.

## 3. How the spec has been validated, and what has not

Done, all on 2026-09-05 and recorded in the tracker's W15 row and the
spec's §12:

1. **Fresh-session review** of the first draft by a subagent with no
   memory of the work, against the spec's six §12 questions. 23
   corrections; the largest was inherited from the journal: the
   artillery actors' field names were a transcription without spaces
   (`MaxDropRadius`), the real names have spaces (`Max Drop Radius`), and
   a by-name read would have found nothing. All applied.
2. **Mechanical name check** (test **T11**, `spec_names_check.py`): every
   class, struct and property the spec names, resolved live on the box
   for 18 loaded classes and from the archives for 19 per-call classes.
   157 names, none missing. The one name reflection has not supplied is
   the native base class the `CommandAction_*` configs share; the configs
   load only when a commander claim resolves. Its four properties resolved
   on three archived CDOs.
3. **Exhaustive not-recorded list** (spec §10) from T11's full layouts:
   about 130 further properties, grouped where classes are identical,
   each with its reason or a decision id.
4. **Archive decodes** that settled things a session would otherwise
   have been scheduled for: the creep's field spellings (08-30 layout),
   the UAV actor's layout (08-30), and the two D14 artillery fields'
   values from the 08-30 creep call's per-tick raws (journal, "Artillery
   phases decoded 2026-09-05").

Not done:

- **A second fresh-session review of the final text.** The spec was
  rewritten and amended several times after the first review; only its
  author has read the result, plus a grep for state words. Run the same
  six questions again, same method (§3.1 below), before W17 is written.
- **T12, solo half** (D13's gate): ten minutes with Craig online as a
  squad leader; see §5.
- Observations that need a commander or a second player and do **not**
  gate W17: T13 (action display names), T9 items (a)–(f), T10 (drone
  hand-off), T8 (the acceptance run, after W18). Each row names what
  settles it.

### 3.1 Running the fresh-session review

Spawn a subagent with no prior context. Give it only: the spec, the
journal, the drones doc, the tracker (to resolve ids only), and the
archive folders for name checks. Ask it the six questions of spec §12
verbatim, plus "anything ambiguous enough that two implementers would
build it differently", and ask for ranked suggested corrections with line
numbers. Expect it to be right about most things and to be wrong where it
cannot see an archive; verify its factual claims against the archives
before applying (the first review's "unarchived" struct names were real
and were reflected live the same day). Record the run on the W15 row and
in spec §12's preamble, which already describes the first run.

## 4. Decisions: made, and what still gates a field

All recorded in the tracker's table B "made" list, dated 2026-09-05:

- **D13** — record the command marker's `Action` class pointer as
  `markers[].action`: **yes, after test T12 has observed what it holds.**
  The spec (§5, §10) says the field joins when T12 decodes. The pointer is
  on every command marker class (master and all seven subclasses, T11).
- **D14** — artillery extras: **yes to two fields**, `Pre Warning Delay`
  and `Current Prewarning Shells`, already in the spec (§6) with their
  values from the 08-30 archive; no to the scatter variances and the
  barrage interval (a timer handle). Viewer rule "Artillery timeline" in
  §9.
- **D15** — the action configs' text: **yes to `DisplayName`, no to
  `Description`**, joining the spec once test **T13** has read the strings
  (never captured; the 09-02 sweep recorded the fields exist). Spec §9
  and §10 cite T13.
- **D16** — placement bounds: **no**. Spec §10 carries the reason.
- **D17** — the UAV actor's `dead` flag beside `health`: **yes**; its flip
  is unobserved (no UAV or aircraft shot down in any session) and rides
  T9 item (a). No last-hit field exists on any command actor's layout;
  whether another source names an aircraft's destroyer is T9 (a).

Two spec design choices Craig accepted after explanation, not decisions
rows: the absent-read rule is two-way (`null` = the game's own value is
empty and was read; omitted = the recorder could not read), restoring the
journal's "explicit null" for the identity fields and generalising it;
and the vote block is emitted every frame from plain reads, with nominees
whenever the game's array holds entries, so the recorder keeps no memory
across frames.

## 5. The next concrete steps, in order

1. **T12 solo half** (any time Craig is online, ~10 min). On the box:
   `sudo -u ubuntu git -C <fork checkout> pull` if behind, then from the
   checkout `sudo .venv/bin/python scripts/probes/marker_action.py`
   (root for `/proc/<pid>/mem`), started after the layer is loaded.
   Craig, as a squad leader: place a request, approve it, let it expire
   (~60 s each), then place one and delete it. The probe writes
   change-only rows to `/tmp/marker_action.jsonl` (`new`, `change`,
   `gone`). Stop it with `pkill -f "marker_actio[n]"` — the bracket
   pattern avoids the pkill self-match that has bitten this project five
   times. Archive the JSONL to `Misc/command-probe-<date>/`. Decode: what
   `action` reads on `Command_SLRequest` and `Command_Request` markers,
   before and after approval; record on T12's row (state → decoded for
   the solo half; the call half rides T8), then add `markers[].action` to
   spec §5 (a class name, `null` when the pointer reads null — §2 rule),
   the doctor row for `Action` in §8, and the name in §13. D13 closes.
2. **Second fresh-session review** of the final text (§3.1). Apply what
   survives verification. Record it.
3. **Hand over to W17**, the implementation plan, written from the spec.
   Out of this handoff's scope, but the destination: the plan copies the
   spec's names into code, so steps 1–2 come first.

Riders for whoever preps the next commander-capable session (four or
more players, per T9's needs): T13's two string reads must be added to
the command-assets probe's CDO dump before that session, and that probe
must graduate to `scripts/probes/` before T8 can be harness-ready.

## 6. Rules that bit this work, so the next session does not relearn them

- **Read the row, quote the row** (tracker rule 6). Every wrong statement
  made in the previous session about the register came from recollection.
- **Rows carry state and dated facts only** (rules 11–13). No chore rides
  on a row; a chore is done now, or gets its own row naming the work that
  needs it, or is dropped. A finished test's row is a record.
- **A test's needs are capabilities, never a head-count** (rule 9). "A
  shooter" not "a second player": the pilot shot his own landed drone.
- **A bundled test names the evidence that settles each question** (rule
  9). T9 lists six branches, each with its settling evidence.
- **Dump whole layouts, never ask for names** (probe README). The marker
  `Action` pointer was missed for a week because an enumeration asked 163
  classes for three names.
- **Check the archives before scheduling a session.** The creep's names,
  the UAV layout and both D14 values were on disk for a week.
- **Generate tables from data, never transcribe.** The spec's §10 and §13
  were generated from the probe output; the journal's unspaced names were
  a hand transcription.
- **The spec's own check**: `grep -n -i -w "open|pending|outstanding|decoded|done|todo"`
  over the spec should hit only game states (a vote being open, a request
  pending) and the §12 sentence that names the words.
- **Box discipline**: git on the box only as `sudo -u ubuntu`; probes run
  as root from the checkout; `/tmp` holds probe output only; outputs are
  archived to Misc before anyone cleans up.
- **Commits**: house voice (imperative, story-telling subject), the
  attribution trailer, one concern per commit; every state change updates
  its tracker row in the same commit (rule 3); push after each commit has
  been the working pattern.

## 7. What this handoff deliberately leaves out

The platform side (board issues SQH-1148 to SQH-1153), the format-rule
history, the drift-automation and doctor threads, the stats wishlist and
retention decisions, and the implementation plan itself. All are in the
tracker with their own rows; none of them changes what the spec says.
