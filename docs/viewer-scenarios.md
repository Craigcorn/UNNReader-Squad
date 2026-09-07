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

## They run at the recorder's cadence

Every scenario is written at the cadence a real recording has: **one full
frame every second**, with `tick` up one each, `timestamp` and `worldTimeSec`
moving together, and **three 4 Hz position lines between each pair** —
`{"t": "pos", …}`, `fullTick` naming the frame they follow, the sampler's own
counter in `tick`. They run 32 to 73 seconds each, opening a few seconds
before the first thing happens and closing a few after the last, so the
result is on screen before playback ends.

That is not decoration. The viewer's playhead runs in real time and draws by
interpolating between the two recorded frames bracketing it, exactly as it
does for a `.sqrx`. These scenarios were first built as six or seven frames
three to twenty seconds apart, which left the playhead nothing to move
toward: unpaused, the picture sat still and then jumped at each frame. At the
recorder's cadence they play like a recording, and everything a recording
advances every frame advances here — the vote counting its window down, the
cooldown arithmetic against `worldTimeSec`, the artillery counters on their
observed stamps, a drone's death arriving between two full frames.

Only what **moves** is sampled at 4 Hz. These scenarios move drones and
nothing else, so a position line's `players` and `vehicles` come out empty —
the shape a real line has when nothing passed the sampler's freshness gates —
and `drones` rides the recorder's own rule, written only while there is a
pawn to write. A drone flies at its cruise, 2.5 m a sample; a command actor
has no 4 Hz line at all, so an aircraft on its run steps one full frame at a
time, which is what a recording of one shows.

Rounds are full-frame only, for the same reason: the sampler does not emit
projectiles (shipped and reverted 2026-08-29), so a shell exists once a
second and its flight between frames is the viewer's own interpolation —
which is exactly what §9's *Asset impacts* says, and why an impact is exact
to the second and to the rest point. Each round here is therefore in the air
for three full frames, at rest on its impact point with `hasImpacted` true on
the next, and lingering there for two more — a couple of ticks, which is what
a dying actor really does and what the viewer's impact de-duplication was
written against.

## What each one shows

| Scenario | Runs | Shows | The rule it demonstrates |
|---|---|---|---|
| `request-pending` | 40 s | One squad leader's request still waiting: the 50 m circle in outline, the panel row reading pending with the age it has run. | §9 *Request circle*; §9 *Pending versus approved, and deletes* |
| `request-approved-with-twin` | 35 s | The commander approves. The approved marker appears at once and the pending twin stays until the sweep — both on the map for the eight frames from 1005 to 1012 — and the viewer draws ONE request with an inner tick saying the twin is still there. | §9 *Pending versus approved, and deletes* (T15, 2026-09-07) |
| `request-deleted` | 67 s | A pending request that vanishes 55 s after it was placed, inside the 61 s fuse, with no approved twin: its squad leader took it back, and the panel says so once the playhead passes the removal. | §9 *Pending versus approved, and deletes* (decision D20) |
| `footprint-uav-coverage` | 40 s | A `CommandRadius_Friendly` marker at the 16608 radius the commander chose, drawn at true map scale, with the MQ-9 on station inside it under its own display name. | §9 *Asset shapes*; §5 `distance` |
| `footprint-static-barrage` | 45 s | A `CommandRadius` marker at 15000 with a 7500 outer band — circle filled, band dashed outside it — the guns at their origin through the plan, and **32 shells landing inside it**: two ranging rounds while the barrage counter still reads 0, then ten a barrage for three barrages. | §9 *Asset shapes*; §5 `addDistance`; §9 *Asset impacts* |
| `footprint-creep-path` | 53 s | A `CommandPath` 45000 long with 7500 of drop scatter, drawn as a band along its bearing, **and** the creep walking its plan: first warning shell at +59.7 s of a 60 s enroute, second at +67.1 s, first barrage at +79.2 s — twelve seconds after it. **42 shells land with it** — the two warning rounds near the start, then each barrage's ten further up the path than the last, all inside the scatter band. | §9 *Asset shapes*; §9 *Artillery timeline*; §6 the creep's plan; §9 *Asset impacts* |
| `footprint-strike-line` | 35 s | A `CommandLine` 6000 long along its bearing, with the aircraft moving down the run, its shot counter climbing, and **eight rockets leaving it and landing along the run** ahead of it. The strike family declares no `projectile`, so these join the call by firer alone — the whole join a recording supports for a strike. | §9 *Asset shapes*; §6 the strike family; §9 *Asset impacts* (a call with no class to join on) |
| `footprint-mortar-radius` | 71 s | The mortar's fixed 7500 circle with its 4500 band, **and** its warning-free plan: counter and first barrage both reach 1 at the enroute, then eight barrages of ten every 8–9 s. **All eighty rounds land inside the circle**, ten to a barrage, and nothing lands before the first one opens. | §9 *Asset shapes*; §9 *Artillery timeline* (the mortar's warning-free one); §6 the mortar's values; §9 *Asset impacts* |
| `footprint-aim-line` | 32 s | A `CommandLineRadius`: two aim points, at 0 and 4475 along the bearing, the line between them, and the bomb pair at each — the config's 45 m and 100 m, both dashed. At 4475 apart the two pairs overlap, which is what that separation looks like on the ground. **A bomb falls on each aim point** and rests 4 m and 12 m off them — well inside the inner circle, where every observed bomb fell; no command actor is staged here, so the bombs join no call and ring as any other round does. | §9 *Asset shapes* (`CommandLineRadius`); §9 *Precision bombs* (radii unmeasured — tracker T9 (f), W20's B2) |
| `vote-in-progress` | 73 s | A vote open on team 1 with three nominees and live tallies, the timer counting the 60 s window down, and the entries persisting into the frames after it resolves — the game's own state, recorded as read. | §9 *Commander seat and votes*; §3 the vote block |
| `seat-cooling-and-ready` | 35 s | The cooldown panel doing its arithmetic: the UAV ready, the strike held by the AIR category gate rather than by its own window, the artillery still on its own. | §9 *Ready-in arithmetic* (the later of entry and category; the category gate) |
| `commander-change-restamp` | 45 s | The seat changes hands at 1200. An entry still cooling has its stamp pushed forward by the server's 300 s extension with `remainingAtChange` written; one whose cooldown had run is re-stamped to come back 300 s after the change; the category stamps do not move. | §9 *Ready-in arithmetic* (at a commander change) |
| `commander-step-down` | 45 s | The commander stands down. The seat reads an explicit empty, every entry keeps what it had left, the last vote's cooldown still refuses a claim, and the panel shows what a fresh claim would leave on each entry — labelled as the projection it is. | §9 *Commander seat and votes* (a step-down); §9 *Ready-in arithmetic* (a re-claim) |
| `uav-shot-down` | 45 s | The UAV is hit at +67 s of a 330 s window: `actionDestroyed` reads true while the actor lingers, the map strikes it through, the entry's `destroyedDuringActive` agrees — and the stamps do not move, so the ready-in is the unchanged arithmetic. Who shot it down is not shown. | §9 *Shoot-downs* (decision D18); §9 *Ready-in arithmetic* (destroyed mid-flight) |
| `drone-recon-life` | 59 s | A recon drone deployed, flown at its 10 m/s cruise, **turning right fifteen seconds in** so the heading cone turns with it, landed and exited (no pilot, owner unchanged), then shot down where it sat. Its info panel shows LAST HIT BY and KILLED BY side by side, and they differ: a second shooter moves the pointer half a second after the kill, inside the same one-second gap, so the full frame after the kill names the wrong man and the first 4 Hz sample reading dead names the right one. | §9 *Drones* (stop at dead, team from owner, remaining flight time, the killer at 4 Hz — decision D19; the heading as the view direction — tracker T16) |
| `drone-commander-call` | 45 s | The commander's drone in the air beside the call actor that spawned it, flying two legs so its **heading cone turns at the corner**. Nothing is drawn for the call actor, whose position reads (0, 0, z) and means nothing — on this layer that is the centre of Al Basrah, 1.5 km from the pawn, so drawing it would be unmissable. The pawn's budget is the calling action's 420 s active window, because its class declares no battery. | §9 *Drones* (the call actor draws nothing; the commander drone's budget; the heading as the view direction — tracker T16); §6 the call actor's (0, 0, z) |

Every item of the phase's minimum set is covered. Several scenarios carry
more than one item, because the data is the same actor and marker either
way: `footprint-creep-path` is the creep's shape, the creep mid-barrage and
its shells landing; `footprint-mortar-radius` the same three for the mortar.
The impacts rule is deliberately shown five times over, because the join it
makes is a different one each time — an artillery plan that names its shell's
class (the two barrages and the creep), a strike that names none and joins by
firer alone (`footprint-strike-line`), and rounds with no call on the map at
all (`footprint-aim-line`), which is what a bomb's impacts look like on their
own.

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

A scenario is written as `record(from, to, frame, sample)`: `frame(t)` is the
state at that second and `sample(t, fullT)` what has moved since the last
full frame, and the helper lays out the cadence — one full frame a second,
three position lines between each pair. That is where the 129 to 293 lines
of each `<name>.json` come from.

## Which numbers are the game's

A scenario is not a recording, and it matters which of its numbers a reviewer
can hold the spec against. **Every figure the spec records is used verbatim:**

- §3 — the UAV's 30 / 300 / 600 and its category's 600 s interval; the
  display texts "MQ-9 UAV Recon" and "Heavy Mortar Barrage"; the vote
  cooldown timer 217, as the reading at one named instant, counting down a
  second a frame from there the way the game's own timer does
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
- §9 — that the rounds an asset fires are tracked projectiles rather than
  part of the actor record, so every round is shaped exactly as
  `read_projectile` in `sqreader/squad/snapshot.py` writes one (the class key
  is `classShort`, and `firer` is the firing player's *name*, which is why
  the join to a call's `callerEosId` goes through the roster); and that a
  bomb call drops two `BP_Projectile_500lb_Bomb_C`, the journal's own class
  and count from the 09-02/03 calls

Everything else is a fixture value, chosen so a rule has something to bite
on, and none of it contradicts a recorded one. The ones worth naming:

- **The other action configs' durations.** The spec quotes only the UAV's, so
  the strike, creep, barrage and drone configs carry fixture durations.
- **The static barrage's fire plan.** §6 records the creep's stamps and the
  mortar's interval and nothing for the static barrage, so that one walks
  §9's family rule — guns at the enroute, the main barrage `preWarningDelaySec`
  after the last warning shell — on the creep's observed spacing. The creep's
  and the mortar's own stamps are the recorded ones, put on the one-second
  grid a recording writes them to: a counter that moves at +59.7 s first
  shows on the frame at +60.
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
- **The rounds themselves.** Which shells land where, and when, is a fixture
  plan built on the recorded stamps: a barrage's ten land over the three
  frames after its counter moves, so the actor's own `currentBarrage` in the
  frame each round comes to rest is the barrage §9 counts it against. The
  scatter is a golden-angle spread that fills the ground the marker says the
  call covers, and says nothing about a real barrage's dispersion, which the
  game does not record. Three class names are fixture values —
  `BP_Projectile_155mm_C` (matching the class the artillery actors here
  already name, so the join has something to join on), `BP_Projectile_Mortar_C`
  and `BP_Projectile_Rocket_C` — as are every round's `explosiveBaseDamage`
  and `explosiveKillZoneRadius`, which no reading quotes. The journal's own
  155 mm shell class is `BP_Projectile_155mm_Artillery_C` and nothing has
  ever read the artillery actor's `Projectile` *value*, so whether the two
  strings agree in a real recording is unanswered; §9's class join assumes
  they do, and these scenarios are written that way.
- **A strike's shot counter and its rockets.** `shotsMade` / `maxShots` are
  the scenario's existing fixture values and the eight rockets are another;
  the recording joins the two no more than this does. A gun run's rounds are
  plain bullets, which the recorder deliberately excludes from `projectiles`,
  so a gun strafe would land no tracked rounds at all.
- **The cast.** Six players with invented names and EOS ids.

## What a scenario cannot show

Three things, all because the recording does not carry them:

- **Who shot an asset down.** No last-damager field exists on any command
  actor, the server log carries no line, and the viewer names nobody
  (decision D18). `uav-shot-down` shows a strike-through and nothing else.
- **Why a request that was already on the map when the recording started went
  away.** Its age is only a lower bound, so it is never called deleted. No
  scenario demonstrates this because it is the absence of a reading.
- **Where a drone's camera is pointed.** The two drone scenarios draw a
  heading cone off the pawn's `yaw`, and that is the *airframe's* rotation —
  the camera facing it is the player's word (2026-09-07), not a read. Both
  scenarios say so in the info panel, and neither can show the difference:
  the camera's own rotation is not in any recording. Tracker T16 is the
  reading test, D22 the question of recording it.
