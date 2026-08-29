# Implementation plan: the two remaining attribution repairs

Status: **complete 2026-08-29** — both repairs shipped and deployed. The
validation surfaced a third difference class the plan under-enumerated
(named → a different name, from the game's own late killed event); both such
rows were traced to raw frames, reviewed by Craig with the full 13-case
evidence file, and accepted. Revive-clearing was dropped via the escape
hatch for a measured reason (respawns dominate wounded→healthy transitions
1384:584 and the discriminating field isn't in the viewer type); precedence
alone closes the mislabel.

Read first: `CLAUDE.md` (hard rules and gates), and the execution-convention
sections of `docs/plans/detectors-and-seeding.md` (house voice, the local
mypy/numpy quirk, test-server access). Background: the Narva investigation of
2026-08-29 — a "?" death at 31:41 next to a correct "Suicide" at 31:39 —
found three attribution flaws. The first (the 180 s wound-correlation TTL
losing any incap held past three minutes) is already fixed as `1925ec3`.
This plan covers the remaining two. Both are display/attribution only:
kill and death *counters*, leaderboards, and ELO are computed from the
game's own memory counters and are not affected by either flaw.

**In scope:** Repairs A and B below, plus the shared validation run.
**Explicitly out of scope:** anything touching the recorder, wire format,
detectors, or stats schema; the wishlist; SquidHub.

---

## Repair A — resolve the death line's credited killer in the log's own namespace

### The flaw, fully

When a downed player finally dies, Squad's `Die()` log line carries the
**credited killer's controller EOS id** — the same credit the in-game
scoreboard awards. The reader keeps it (`killerEos`) precisely as the
last-resort attribution for deaths whose wound correlation has expired;
the code comments call it "the ONLY thing that still identifies the killer"
and "what kills the bare '?' rows."

It does not work on licensed servers, and the main server is licensed. The
resolution (`resolve_event_names` in `sqreader/squad/logtail.py`) maps
`killerEos → name` through the **memory roster's** `eosId` — which on a
licensed server is a UUID (`OnlineUserId`), while every id in the log is a
32-hex EOS ProductUserId. Two different namespaces; the map can never hit.
This is the same namespace split that made the damage-event detectors inert
(fixed for them in `74632f9` by falling back to names — but here there is no
name to fall back to; recovering the name is the whole job).

The same mismatch also breaks the **self-death check** on licensed servers:
"is `killerEos` the victim's own id" is answered today via the roster's
`name → eosId` map, in the wrong namespace.

Evidence: in the Narva recording, Ruby's give-up event carries
`attacker: null, attackerEosId: null` despite the game crediting her
wounder — with the (now-fixed) TTL expired, the rescue was her only chance
and it was structurally dead.

### The fix

Resolve log ids against **the log itself**. `DamageLogParser` already reads
an attacker's display name *and* 32-hex EOS from every `ActualDamage` line
(`_ACTUAL_RE` captures both) — same namespace as `killerEos` by
construction. So:

1. **Parser-side id cache.** `DamageLogParser` maintains
   `_eos_names: dict[str, str]` (log-EOS → display name), fed from every
   ActualDamage line. Bounded (a few hundred entries; evict oldest past
   ~1024), lifetime = parser lifetime (names are stable per session; a
   cross-match stale name resolves to the same person's current name via
   `resolve_event_names`' existing tag-stripping anyway).
2. **Extend `_REVIVE_RE`** to also capture both parties' EOS ids from the
   revive line (the raw line carries them; today only the victim's *name*
   is captured) and feed those pairs into the same cache — this teaches the
   cache players who never dealt damage (pure medics, fresh joins).
3. **At `Die` time**, when the wound correlation produced no attacker but
   `killerEos` is present and valid:
   - `killerEos == the victim's own cached log-EOS` → emit
     `selfInflicted=True` (the self-death check, now in the right
     namespace).
   - `killerEos` in the cache → emit `attacker=<cached name>` (and the
     cached eos as `attackerEosId`), exactly as if the correlation had
     survived.
   - otherwise → keep today's behaviour: pass `killerEos` through for
     `resolve_event_names`' roster attempt (still correct on unlicensed
     servers, where the namespaces can coincide) and emit unattributed if
     that also misses. Honest nulls stay honest.
4. Keep the roster-based path in `resolve_event_names` as the fallback it
   now is; do not remove it.

**Coverage note to record in code:** the cache learns players from damage
and revive lines, so a player who has neither dealt damage nor been party
to a revive is still unresolvable — rare (they must also have been downed
past the 330 s TTL for it to matter) and it degrades to today's "?", never
to a guess.

### Tests (`tests/test_logtail.py`, existing helpers; note TTL is now 330 s so expiry setups need >330 s gaps and a bystander death to trigger the GC)

- Correlation expired, `killerEos` seen earlier on an ActualDamage line →
  killed event attributed by name, `selfInflicted` false.
- Correlation expired, `killerEos` equals the victim's own cached id →
  `selfInflicted=True`, no attacker.
- `killerEos` unknown to the cache → event unattributed (no guess), and
  `killerEos` still passed through to `resolve_event_names`.
- Revive-line ids populate the cache (a pure medic later credited via
  `killerEos`).
- Existing `test_die_eos_recovers_attacker_after_cache_expiry` (the roster
  path) still passes — the fallback must not regress.

---

## Repair B — a death frame's own evidence outranks a stale buffered wound

### The flaw, fully

The viewer's kill feed keeps a ~45 s buffer of wound/kill events so a death
that arrives seconds later (bleed-out, give-up while downed) can be
attributed to whoever put the player down. Correct and necessary. Two
details combine into the flaw:

1. A **revive does not clear** the victim's buffered wound (the backend
   clears its own correlation on revive; the viewer buffer has no such
   rule).
2. The buffer is consulted **before** the death frame's own events.

So: downed by Bob → revived → then, within ~45 s of the original wound, a
death whose evidence never enters the buffer — a give-up (self-inflicted
events carry no attacker and are never buffered) or a world death (fall,
drown; same reason) — and the stale wound wins: the feed prints
"Bob killed X" while the death frame is *holding the correct evidence in
its other hand*. The backend already sends the right answer; the viewer
overrules it.

Bounded on both sides: a post-revive death to a **new attacker** is safe
(the new kill event is buffered and wins by recency), and outside the 45 s
window the buffer has expired and the death-frame evidence is consulted.
This is the rarest of the three Narva-era flaws — but it is the only one
that prints a **wrong name** rather than an honest "?", and it credits a
kill the game itself denied.

### The fix

Precedence, primary; revive-clearing, secondary:

1. **Precedence** (in `frontend/src/killfeed/diff.ts`, inside the
   death-resolution path added with the one-tick hold): before accepting a
   buffered match that is **wounded-only**, scan the death frame's events
   (and, via the existing `pendingDeaths` hold, the next advanced tick's)
   for a `killed` event naming the victim. If one exists and is
   self-inflicted or attacker-less-with-damage-type, it wins over the
   wounded-only buffer entry. A buffered **killed** event (the real
   bleed-out attribution, where backend and buffer agree) keeps its
   precedence unchanged.
2. **Revive clearing** (belt-and-braces, only if cleanly detectable): when
   a player transitions wounded → healthy in the snapshots *without* their
   deaths counter incrementing, mark their unused buffered entries
   consumed. Detection fields to verify against real snapshots:
   `soldier.isWounded`, `lifeState`, `health`. If detection proves noisy
   (torn reads flapping `isWounded`), ship precedence alone — it already
   closes the mislabel — and record why.

### Tests (`frontend/src/killfeed/diff.test.mts`, plain node harness)

- Downed by Bob → revived → give-up within 45 s, death frame carries a
  self-inflicted killed event → **Suicide**, not Bob.
- Same, but the self-inflicted event arrives one tick late (the held-death
  path) → still Suicide.
- Downed by Bob → revived → fall death (attacker-less event with a damage
  type) → world cause shown, not Bob.
- Downed by Bob → bleed out, death-tick killed event names Bob → Bob
  (backend and buffer agree; no regression).
- Downed by Bob → death with **no** death-frame event within the hold →
  buffer still attributes Bob (the fallback the buffer exists for must
  survive).
- Downed by Bob → revived → shot dead by Carl → Carl (recency, unchanged).

---

## Shared validation — one pass over real data

After both repairs: run the feed precompute (decode + `diffSnapshot` over
every frame, as `useReplayLoader` does) with old and new code over the
**five main-server recordings** in `C:\Users\CRAIG\Documents\UNN\Misc\
SquadReader Replays` (the four originals plus Narva), and diff the two
timelines row by row. Expected differences, exhaustively:

- rows gaining an attacker name or a Suicide label where Repair A's cache
  resolves a formerly-null `killerEos` — **only** on rows that were "?"
  before;
- rows changing from a named attacker to Suicide/world-cause **only** in
  Repair B's exact revive-window pattern;
- nothing else. Any other attribution flip is a bug in the repair — stop
  and report it, don't rationalize it.

Write the diff summary (it names players) to
`C:\Users\CRAIG\Documents\UNN\Misc\attribution-repairs-validation.md`,
never into the repo. Note: Repair A changes what the *backfill/parser*
emits, so the recordings' stored events don't change — the validation
exercises the parser only through recordings' already-stored events for B,
and through fed log-line tests for A; A's real-data proof is the next live
match on the test box (fire, wait past 330 s… impractical) — accept the
unit tests plus the namespace evidence as A's proof, and say so in the
report.

## Execution notes

Backend gates: `pytest`, `ruff check .`, per-file mypy
(`--no-site-packages`). Repair A touches the event stream ahead of the
recorder, so run `scripts/stats_parity.py` against the test-box corpus
before push (CLAUDE.md gate — logtail feeds what stats consume). Frontend:
`npm test` + `npm run build`, commit the rebuilt dist alongside per house
convention. Changelog entries under `[Unreleased]`, house voice, one per
repair. Deploy the branch to the test box afterwards (pull + restart; the
box may hold an active empty seed recording — check for live players via
`sqreader summary`, not the sidecar, before restarting). Two task chips
exist for these repairs (`task_af748d59`, `task_b0038430`); they are
superseded by this plan once approved.

## Definition of done

- Both repairs implemented with every listed test green; full gates pass;
  parity harness green on the test-box corpus.
- The validation diff over the five recordings shows only the two expected
  difference classes, written up with counts.
- Changelog tells both stories; dist rebuilt and deployed to the test box.
