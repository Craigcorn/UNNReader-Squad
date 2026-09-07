# Viewer scenarios

Sixteen small recordings, hand-built, one per viewer rule, staged on Al
Basrah AAS v1 and playable in the browser with no server, no live match and
no `.sqrx`. They exist so the
commander rules of `docs/command-assets-spec.md` §9 can be reviewed and
argued about without waiting for a match to happen, and so that once a rule
is agreed the scenario that showed it stands as the regression test.

This document describes the scenarios. Their state — reviewed, agreed,
changes applied — lives in `docs/tracker.md` (W72).

## Opening them

```
cd frontend && npm run dev
```

then, in the browser:

- `http://localhost:5173/?scenario` — the list, with nothing loaded
- `http://localhost:5173/?scenario=<name>` — one scenario, loaded and paused
  at its first frame

The picker sits bottom-left and switches between them; the url follows, so a
scenario can be linked to. Space plays and pauses, the timeline scrubs, and
every panel works exactly as it does on a real recording — the frames go
through the same reconstructor and the same render path a `.sqrx` does. A
built SPA (`npm run build`) serves them the same way; each scenario is its
own lazily-loaded chunk, so a viewer that never asks for one never downloads
one.

The commander panel is behind the seat line under a team's tickets: click
"Ruby" (or "no commander") in the team bar. Assets, drones and markers are
clickable for their info panel. `Esc` closes any panel.

## Where they are staged

Every scenario plays on **Al Basrah AAS v1**, and carries the layer block a
real recording of that layer carries: the same texture name and the same
world bounds, a 4 km square running from -200000 to 200000 on both axes.

They are on a real map because scale is the claim these fixtures make. A
footprint is drawn on the ground at true size, and the point of
`footprint-mortar-radius` is that a mortar's 75 m circle is *small* while the
point of `footprint-creep-path` is that a creeping barrage's 450 m path is
not. Against the bare grid these scenarios used to draw on, those are two
shapes of arbitrary size and a reviewer has nothing to hold them against;
against Al Basrah they are a circle that covers a compound and a barrage that
walks the length of the airfield.

The action sits on the north-west side of that airfield, with the cast around
(-110000, -120000) — the same corner of the map as the session the spec was
written from, whose players read about x -128000, y -143000. One offset,
`MAP_ORIGIN` in `generate.mjs`, carries every scenario there in one piece, so
every figure the spec recorded is untouched — a distance is a distance
wherever it is drawn — and only the absolute placement moves. Every shape of
every scenario lands on playable ground: the creeping barrage's 450 m path,
the widest thing staged here, clears the western out-of-bounds band by more
than 140 m at its closest, and the outermost point of any scenario — the
static barrage's 225 m outer band — is still 475 m inside the layer bounds.

One position is deliberately *not* carried: the drone call actor's
`(0, 0, z)`, which is a memory read rather than a placement. On this layer
that is the centre of Al Basrah, 1.5 km from the drone it spawned — so
`drone-commander-call` now fails loudly if the viewer ever draws it.

The map image is served by the reader from `--sqmaps-dir`, and the dev server
proxies `/sqmaps` to it (`T_AlBasrah_Minimap` resolves to `albasrah.webp`).
Run `npm run dev` with no reader behind it and the texture 404s, so the
viewer draws its grid exactly as it does for any recording whose texture is
missing — the geometry, the bounds and the scale are the same either way.

## What each one shows

| Scenario | Shows | The rule it demonstrates |
|---|---|---|
| `request-pending` | One squad leader's request still waiting: the 50 m circle in outline, the panel row reading pending with the age it has run. | §9 *Request circle*; §9 *Pending versus approved, and deletes* |
| `request-approved-with-twin` | The commander approves. The approved marker appears at once and the pending twin stays until the sweep — both on the map for four frames — and the viewer draws ONE request with an inner tick saying the twin is still there. | §9 *Pending versus approved, and deletes* (T15, 2026-09-07) |
| `request-deleted` | A pending request that vanishes 55 s after it was placed, inside the 61 s fuse, with no approved twin: its squad leader took it back, and the panel says so once the playhead passes the removal. | §9 *Pending versus approved, and deletes* (decision D20) |
| `footprint-uav-coverage` | A `CommandRadius_Friendly` marker at the 16608 radius the commander chose, drawn at true map scale, with the MQ-9 on station inside it under its own display name. | §9 *Asset shapes*; §5 `distance` |
| `footprint-static-barrage` | A `CommandRadius` marker at 15000 with a 7500 outer band — circle filled, band dashed outside it — and the guns at their origin through the plan. | §9 *Asset shapes*; §5 `addDistance` |
| `footprint-creep-path` | A `CommandPath` 45000 long with 7500 of drop scatter, drawn as a band along its bearing, **and** the creep walking its plan: first warning shell at +59.7 s of a 60 s enroute, second at +67.1 s, first barrage at +79.2 s — twelve seconds after it. | §9 *Asset shapes*; §9 *Artillery timeline*; §6 the creep's plan |
| `footprint-strike-line` | A `CommandLine` 6000 long along its bearing, with the aircraft moving down the run and its shot counter climbing. | §9 *Asset shapes*; §6 the strike family |
| `footprint-mortar-radius` | The mortar's fixed 7500 circle with its 4500 band, **and** its warning-free plan: counter and first barrage both reach 1 at the enroute, then eight barrages of ten every 8–9 s. | §9 *Asset shapes*; §9 *Artillery timeline* (the mortar's warning-free one); §6 the mortar's values |
| `footprint-aim-line` | A `CommandLineRadius`: two aim points, at 0 and 4475 along the bearing, the line between them, and the bomb pair at each — the config's 45 m and 100 m, both dashed. At 4475 apart the two pairs overlap, which is what that separation looks like on the ground. | §9 *Asset shapes* (`CommandLineRadius`); §9 *Precision bombs* (radii unmeasured — tracker T9 (f), W20's B2) |
| `vote-in-progress` | A vote open on team 1 with three nominees and live tallies, the timer counting the 60 s window down, and the entries persisting into the frames after it resolves — the game's own state, recorded as read. | §9 *Commander seat and votes*; §3 the vote block |
| `seat-cooling-and-ready` | The cooldown panel doing its arithmetic: the UAV ready, the strike held by the AIR category gate rather than by its own window, the artillery still on its own. | §9 *Ready-in arithmetic* (the later of entry and category; the category gate) |
| `commander-change-restamp` | The seat changes hands at 1200. An entry still cooling has its stamp pushed forward by the server's 300 s extension with `remainingAtChange` written; one whose cooldown had run is re-stamped to come back 300 s after the change; the category stamps do not move. | §9 *Ready-in arithmetic* (at a commander change) |
| `commander-step-down` | The commander stands down. The seat reads an explicit empty, every entry keeps what it had left, the last vote's cooldown still refuses a claim, and the panel shows what a fresh claim would leave on each entry — labelled as the projection it is. | §9 *Commander seat and votes* (a step-down); §9 *Ready-in arithmetic* (a re-claim) |
| `uav-shot-down` | The UAV is hit at +67 s of a 330 s window: `actionDestroyed` reads true while the actor lingers, the map strikes it through, the entry's `destroyedDuringActive` agrees — and the stamps do not move, so the ready-in is the unchanged arithmetic. Who shot it down is not shown. | §9 *Shoot-downs* (decision D18); §9 *Ready-in arithmetic* (destroyed mid-flight) |
| `drone-recon-life` | A recon drone deployed, flown, landed and exited (no pilot, owner unchanged), then shot down. Its info panel shows LAST HIT BY and KILLED BY side by side, and they differ: a second shooter moves the pointer 1.1 s after the kill, so the killer is the hitter of the first 4 Hz sample reading dead. | §9 *Drones* (stop at dead, team from owner, remaining flight time, the killer at 4 Hz — decision D19) |
| `drone-commander-call` | The commander's drone in the air beside the call actor that spawned it. Nothing is drawn for the call actor, whose position reads (0, 0, z) and means nothing — on this layer that is the centre of Al Basrah, 1.5 km from the pawn, so drawing it would be unmissable. The pawn's budget is the calling action's 420 s active window, because its class declares no battery. | §9 *Drones* (the call actor draws nothing; the commander drone's budget); §6 the call actor's (0, 0, z) |

Every item of the phase's minimum set is covered. Two scenarios carry two
items each, because the data is the same actor and marker either way:
`footprint-creep-path` is both the creep's shape and the creep mid-barrage,
and `footprint-mortar-radius` is both the mortar's radius and the mortar
mid-barrage.

## Where they come from

```
frontend/src/state/__fixtures__/commander/
  generate.mjs      the scenarios, written as code — the readable form
  <name>.json       its output: the recording lines, compact
  manifest.json     names, titles and rules, for the picker
  index.ts          the loader map (one line per scenario)
```

Regenerate with:

```
cd frontend && node src/state/__fixtures__/commander/generate.mjs
```

It is deterministic — no clock, no randomness, every stamp derived from one
fixed base — so a run with nothing changed leaves `git diff` empty. Adding a
scenario means adding it in `generate.mjs`, running that, and adding one line
to `LOADERS` in `index.ts`; a missing file is then a compile error rather
than a blank page.

## Which numbers are the game's

A scenario is not a recording, and it matters which of its numbers a reviewer
can hold the spec against. **Every figure the spec records is used verbatim:**

- §3 — the UAV's 30 / 300 / 600 and its category's 600 s interval; the
  display texts "MQ-9 UAV Recon" and "Heavy Mortar Barrage"; the vote
  cooldown timer 217
- §4 — the server's rules as read live on 2026-09-04: 60 / 300 / 300 / 2 / 3
- §5 — the footprint figures: a UAV coverage radius of 16608 (and 19958 in
  the shoot-down scenario), the static barrage's 15000 with a 7500 outer
  band, the mortar's fixed 7500 with 4500, a strike run of 6000, the creep's
  45000 path with 7500 of drop scatter, an aim line's 4475
- §6 — the creep's 2 warning shells and 12 s delay on a 60 s enroute, its
  counter reaching 1 at +59.7 s and 2 at +67.1 s with the first barrage at
  +79.2 s; the mortar's 0 warning shells, 0 delay, 10 shells a barrage, 8
  barrages, 1.0 drop radius, and its 30 s enroute
- §7 — the recon drone's 15 / 15 health and its 100 s battery

Everything else is a fixture value, chosen so a rule has something to bite
on, and none of it contradicts a recorded one. The ones worth naming:

- **The other action configs' durations.** The spec quotes only the UAV's, so
  the strike, creep, barrage and drone configs carry fixture durations.
- **Display names the spec does not quote.** Where the spec has no display
  text, the fixture uses the config class name's own distinctive segment,
  mechanically (`CommandAction_Artillery_Creep_USMC_C` → "Artillery Creep
  USMC"). It is never a label written for the fixture.
- **The category names.** `CommanderCategory.Name` is an FText the spec
  quotes no value for, so "Air", "Artillery" and "Support" are labels.
- **Where on the map they sit.** The layer block is a recorded one, bounds
  and texture name included, but *where* in it each shape was placed is a
  fixture choice — see "Where they are staged" above.
- **The bomb circles' 45 m and 100 m.** Both come from the bomb config, but
  that they are what the in-game map draws is an assumption the spec flags:
  the ratio matches and every observed bomb fell inside the inner one, and
  neither absolute value has been measured. What `footprint-aim-line` draws
  is two viewer constants, `BOMB_INNER_CM` and `BOMB_OUTER_CM`, exactly as
  the 50 m request circle is a viewer constant. An edge-stand during a bomb
  call settles them — tracker T9 (f), W20's B2 — and if it moves them, those
  two numbers are the whole of the change.
- **The cast.** Six players with invented names and EOS ids.

## What a scenario cannot show

Two things, both because the recording does not carry them:

- **Who shot an asset down.** No last-damager field exists on any command
  actor, the server log carries no line, and the viewer names nobody
  (decision D18). `uav-shot-down` shows a strike-through and nothing else.
- **Why a request that was already on the map when the recording started went
  away.** Its age is only a lower bound, so it is never called deleted. No
  scenario demonstrates this because it is the absence of a reading.
