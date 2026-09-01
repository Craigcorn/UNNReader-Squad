# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- The 48-casualty refresh missed two, and upstream's 1.4.5 diff is what
  found them: both constants sat outside every watch table, so the doctor
  had no way to dictate their new values. The deployable placer slots
  (unnamed private fields - now SQ_DEPLOYABLE_PLACER_*_OFFSET) moved -0x18
  with the SQDeployable block; the stale slots mostly degraded attribution
  to null through the read path's name validation, but junk that resolved
  through the controller chain could fabricate a short "name" as a placer.
  And SQ_VEHCOMP_STATE_OFFSET - which the register wrongly called never
  read - has fed every component record since 1.4.0, and read a
  neighbouring byte as a component state after the update. Both re-measured
  live: the state field turned out to be the named EnumProperty
  VehicleComponentState, so it is a watched table row now; the placer pair
  are module constants under the fleet test, anchored to the named tail
  (ErrorTable +0x10/+0x18) with the derivation written down. Recordings
  between the server's update and this deploy carry junk component states
  and untrustworthy placer attribution, and are excluded from those uses.
- The first real Squad update since the doctor grew teeth, and it bit
  everything at once. The server auto-updated on a restart; within one
  cycle of re-attaching, the hardened doctor reported all 48 casualties:
  47 named constants (deployables, rally points, seats, vehicle arrays,
  FOB pool, controller fields - and LastTakeHitInfo, moved for the
  second build in a row) plus the one constant no table can watch: the
  transform translation, caught by the value check when every sampled
  vehicle disagreed with its own RelativeLocation. That one mattered
  most - the FTransform shifted 0x210 -> 0x200, so every recorded
  position was silently the Z coordinate and garbage until the fix. All
  48 values refreshed exactly as the doctor dictated, verified by a
  byte-level dump of the new transform layout. Recordings between the
  server's update and this deploy carry junk positions and are excluded
  from positional use.

### Added
- The machine doctor now judges the stats collectors too. Their counters
  live in private C++ structures reflection cannot see, so the only
  drift check possible is by value - and the value check needs players,
  which the machine path deliberately never goes and gets. The serve
  loop now hands over the player list from the snapshot it already
  holds, the same way it hands over vehicles for the position check, and
  the collector verdicts run on every check-in in pure Python - zero
  memory reads, sub-tenth-millisecond. Only a sibling desync (one field
  of a collector entry read while its array-mate did not) is drift;
  event-gated absence skips, an empty server skips, and no snapshot at
  all contributes nothing - not even a skip, so the payload's skipped
  field keeps meaning something.

### Fixed
- The machine doctor now runs the checks only a human could run. Four of
  the doctor's checks - the reflection walker's own assumptions, the lane
  graph, the marker array's element stride, and the ComponentToWorld
  transform under every position the reader reports - lived inside the
  `sqreader doctor` command and nowhere else. On a managed instance
  nobody can type that command, so on exactly the deployment that cannot
  look for itself those four could break and the health signal would
  still read green; the take-hit drift went unnoticed for months for
  this precise reason. The checks moved into health.py, both faces call
  the same functions, and the human command is now only a formatter -
  the two can no longer disagree. Every one of them can also say "I
  could not measure this": a layer with no lane graph, an AAS layer with
  no RAAS visualizer, an empty vehicle sample. Those skips are reported
  as skips, never as drift, because a check that cries wolf every five
  minutes is a check that gets ignored. The position check reads live
  vehicles handed to it by whoever called - the serve loop passes ones
  from the snapshot it just built - so the health path never walks the
  object array or builds a snapshot to check itself. And it got faster
  while gaining work: resolving every class in one batched walk instead
  of one walk per name, with the resolved addresses re-validated and
  reused between check-ins, took a check-in's doctor from 156 ms to
  12 ms measured live - 91 ms on the first run of a process, which is
  the one that still has to walk. A drift verdict is never reported off
  reused addresses - it re-resolves from scratch first, so the shortcut
  can make the doctor quicker but never wrong.
- The vehicle ammo pools are watched at last. Their five offsets - how
  much a logistics truck is carrying, how fast it drops it, which
  inventory it belongs to - were read off blueprint actors, and the
  register said there was no single class to check them against. There
  is: walking the super-chain of every AmmoWep blueprint alive on a
  server lands on SQVehicleResource, a native class, which declares all
  five at exactly the offsets the reader uses. Now watched like
  everything else, which also means a signed offset pack can repair
  them, and the last "cannot check this" entry in the register that
  had a real answer is gone.
- Doctor now checks inside the damage-event struct, and a class rename
  can no longer hide behind "not loaded". Two holes, one shape. The
  first: offsets addressed relative to a struct rather than a class -
  the take-hit internals the reader reads every damage event through,
  and the marker array's Items - had no table that could express them,
  so the struct that already moved once and cost months of wrong data
  was watched only at its base. They have a tier of their own now,
  which walks the hops by reflection, two deep where the bone name and
  hit distance live, and compares the same constants the reader uses.
  The second: a watched type that was absent counted as "this level has
  not loaded it" for every type, so renaming SQHealingEquipableItem or
  SQCommanderState away would have read as an eternal skip rather than
  a break. Native SQ classes are registered when the C++ module loads,
  not when content spawns - checked on an empty server with nothing
  built, every one of them answered - so they are required now and
  their absence is drift. Blueprints, which genuinely do load with
  content, stay optional.
- The check-in now says which checks could not measure anything. A
  report of "ok" used to cover both "everything was checked and it is
  fine" and "three checks never ran"; the second is a coverage hole
  that looks exactly like a pass. The payload carries a capped list of
  the checks that skipped, so a situational skip reads as content and a
  chronic one reads as the bug it is. Additive - the schema string does
  not move and an older central ignores the field.
- The damage-event struct moved and the reader read past it. Some Squad
  update shifted SQSoldier.LastTakeHitInfo from 0x24c8 to 0x24e8, and
  the memory-side damage enrichment - hit timestamps, damage type, the
  causer, the bone - had been reading the old address ever since:
  thirty-two bytes of the wrong memory, shipped as plausible data. The
  log-authoritative kill feed kept the feed itself correct, which is
  exactly why nobody noticed. The doctor caught it on the FIRST run
  after the struct went on its watch list - the strongest possible
  argument for that list - and the base now resolves by reflection at
  startup like everything else, with the corrected constant as the
  fallback it should always have been.
- Doctor now watches the offsets it was quietly missing. A Squad update
  that moves a struct is silent - the reader keeps reading the old
  address, gets neighbouring bytes and ships plausible garbage - and
  doctor is what makes that loud, so an offset it does not know about is
  a hole in the alarm. Fourteen constants sat in that hole, every one of
  them readable: the emplacement gun's joins and aim, the seat's role
  socket, the vehicle entity arrays, the FOB resource pool, the
  player-controller fields, the marker manager's array, the soldier's
  last-hit struct, the actor owner used to join ammo pools to vehicles,
  and the projectile's firer - which had already drifted once. They are
  checked now, which also means a signed offset pack can repair them.
  Types that may legitimately not be loaded in a level - no emplacement
  built, no FOB placed - are marked optional and skipped when absent
  rather than reported as drift, so an empty layer cannot cry wolf.
  Reads that resolve purely by reflection cannot drift, but a rename
  makes them vanish from recordings without a word; the fields the
  medical capture and the commander identity depend on are now declared,
  so a rename surfaces as drift instead of data quietly going dark. What
  still cannot be checked is written down with the reason it cannot,
  and a test fails on any offset that is neither watched nor written
  down - the rule that keeps the next addition honest is in
  CONTRIBUTING.md.

### Added
- A locked squad finally says so. The read branch for the squad's
  bIsLocked flag sat fully written in the reader - comment, name-resolved
  offset, padlock intent and all - and the viewer side was equally
  finished: the scoreboard padlock, its tooltip, even the rule that a
  recording from before the field existed shows nothing rather than
  guessing "open". The one thing missing was the field's name in the
  reflection grab list, so the read never ran and no recording ever
  carried the flag. One word added; the whole feature lit up. First
  entry of the format-lock pass: recordings now carry squad lock state
  on every full frame, and it costs bytes.

- A recording now knows when each player last switched sides. Without
  it, a side-swapper's whole match credits to their final team;
  with it, time-on-team is honest and swaps are countable. The int is
  recorded verbatim - the clock it speaks gets pinned against a live
  player before anything interprets it - and reflection-resolved, so a
  Squad rename blanks the field instead of reading junk. Second entry
  of the format-lock pass.

- A recording now names each team's commander. The CommanderState offset
  had been grabbed since the team reader existed - the read was simply
  never written. The pointer resolves to the commander's own PlayerState
  and the record carries their verified name and account id, exactly as
  a vehicle seat names its occupant; a team with no commander records
  nothing rather than a guess. Enables commander identity and uptime
  per match. Third entry of the format-lock pass.

- Every vehicle seat now records the game's own name for itself. The
  viewer's Driver/Gunner labels came from a hand-maintained catalog
  keyed by vehicle class and seat index - right for vehicles someone
  entered, a guess for everything else, and empty for every vehicle a
  future Squad patch adds. The seat's config struct carries an
  attach-socket name (socket_seat_driver, socket_seat_gunner,
  socket_seat_passenger3 - verified on live vehicles) which IS the
  game's own role label; it is now recorded verbatim per seat, read once
  per seat component and cached, offset resolved by reflection with the
  live-verified constant as fallback. The viewer prettifies the socket
  and prefers it over the catalog for seats the catalog does not know;
  old recordings fall back to the catalog exactly as before. Measured
  against a real 100-player recording through the recorder's own
  compression: 304,186 stamped seat entries cost 197 KB - 0.46% of the
  file. Fourth entry of the format-lock pass.

- `scripts/stats_parity.py` measures the claim the whole recording pipeline
  rests on: that a `.sqrx` file alone reproduces the statistics the agent
  computed while the match was being played. It replays an entire archive, in
  order, through the real `stats-backfill` path into a fresh database, and
  diffs that against a consistent snapshot of the live one - table by table,
  row by the key that identifies the thing rather than the write, column by
  column. The columns come from the live schema itself, so a stat added next
  month is compared the day it lands and this file does not change; a column
  that turns up on only one side is a loud failure rather than a quiet gap.
  It compares whole archives and not single matches, because a rating earned
  against earlier opponents cannot be reproduced by replaying one game into an
  empty database. Rows the archive cannot speak for - matches older than the
  recorder, matches retention swept, the one still being played - are counted
  and listed rather than failed, and the reasons are printed. Everything else
  is either identical or a finding. The two differences that are facts about
  the archive rather than bugs in the engine are written down in the tool,
  each with its reason and each narrow enough to expire on its own: a match
  recorded before the recorder kept its closing frames cannot show its own
  ending, and a shutdown's `end_reason` describes what happened to the agent
  rather than to the match. The report counts how many values each one
  forgave, so an exclusion cannot quietly grow into a blind spot.

- Four new cheat detectors, all shipped switched OFF. `stamina_hack` catches
  sprint-speed movement on a bar that never falls - sprinting drains stamina
  in Squad without exception, so neither half of that is suspicious alone and
  the pair is. `no_reload` reads the magazine pool: firing drains the summed
  total and reloading does not, so a drop is verified fire volume, and the
  ceiling is derived from the mandatory dump-then-reload cycle rather than
  being a flat multiple of the magazine - a flat one would have accused a
  good machine-gunner. Launchers get two rules of their own: flagrant spam
  is two rounds inside one interval, and the paced cheater who never shows
  two together - a rocket every couple of seconds, invisible to any
  single-interval test at 1 Hz - is caught on rate, the legal ceiling being
  one round per reload with the deliberately-fast reload floor serving as
  the margin. Pistols, bolt-actions and small drums get the one thing that
  is impossible for them: two full magazines gone in less time than a
  reload - classified by a class-wide capacity census, so one rifle read
  low on ammunition can never be mistaken for a pistol.
  `remote_mine` watches for a mine appearing somewhere its placer's body is
  not; placement is arm's reach, so 50 m of budget covers the drift between
  the placement and the tick that sampled it. `fire_no_ammo` joins them:
  where `infinite_ammo` assumes a cheater's ammo stops falling, this one
  counts verified shots first and asks about the ledger second, taking no
  position on a premise nobody has confirmed - and a launched round is
  charged only while the launcher is still the weapon in hand, because
  spawn sampling lags the throw and the rifle someone swaps back to must
  not answer for a pool that rightly never moved. Off is deliberate and not
  temporary politeness. All four were replayed over nine real recordings -
  four 100-player matches and a test server's archive - and three of them had
  to be corrected before they stopped accusing innocent people; the fourth
  never fired. Zero false accusations is what that measurement earns, and it
  is not the same thing as catching anybody, which remains unproven until the
  first real incident. Run `scripts/plugin_replay.py` over your own
  recordings before switching any of them on, because your server is not that
  archive.
- Seeding matches are no longer recorded at all - no replay, no stats row,
  nothing queued for upload. A seed session is two people shooting a wall for
  six hours, and it arrived in the archive dressed as a match: a 40 MB file
  nobody will ever watch, a row that drags every average it touches, and an
  upload nobody asked for. The game names the mode itself, so the decision is
  read rather than guessed - `seeding_game_modes` defaults to `["Seed"]`, and
  `seeding_layer_patterns` is the override hatch for scrims and events whose
  mode is a perfectly normal one. Anything unreadable records: a torn tick is
  not evidence of a seed session, and a recording cannot be got back
  afterwards. The decision is taken once, where the file would have been
  created, so a mode field that flaps during a map load can neither start a
  recording halfway through nor cut a real match in half. Cheat detection is
  deliberately left running through seeding - that is when a bored cheater
  tries things.

- A projectile now carries its firer's team, read off the same verified
  player state that already names the firer. The viewer was built for it all
  along - the Projectile type, the info panel's TEAM row, and the
  firer-colour tint all existed and sat empty because the reader never sent
  the field. Gated to the two real teams; anything else stays null.
  Recordings made before this carry no team and never will.

- A guided missile now draws the path it flew. A TOW is the one projectile
  whose path IS the story - the gunner steers it - and the viewer showed
  only a teleporting dot, so a steered flight played back as sparse jumps
  and an investigation was needed to learn what one recording already
  knew. Guided rounds (stamped by the backend from the class hierarchy,
  with a class-name fallback so older recordings benefit too) leave an
  age-fading trail in the firer's team colour that outlives the missile
  by a few seconds. Ballistic rounds fly a fixed arc and deliberately get
  no trail. Rewinding a replay now also resets the projectile tracker -
  trails rebuild on the way forward, and the phantom impact rings and
  inverted icon headings that every backward seek used to produce go
  with it. The path drawn is the server's replicated missile - Squad
  gives the firer's client authority over guided-missile damage, so a
  steered shot can land somewhere the recorded path never went; the
  recording shows what spectators and victims saw, which is why a victim
  can watch a recorded TOW miss them and still take its damage
  (docs/architecture-notes.md, "Known game-side behaviors").
- An emplacement gun now records what it is aimed at and what it has left
  to fire. The ammo was one pointer walk away the whole time: the gun is
  its own seat pawn, so the exact chain the tank-turret reader follows -
  inventory, current weapon, the magazines array - enters directly at the
  actor, and reads back a mortar's 3 shells, an M2's 100-round belt and a
  TOW's single missile, draining live on fire and readable with nobody in
  the seat. The aim is NOT where tank turrets keep it - the pawn and
  weapon roots that carry a Challenger's traverse sit frozen at zero on an
  emplacement, and the gun's own root never moves after the build - it
  lives on two scene components instead: the swivel mesh's yaw is the
  traverse (relative to the root, the same hull-plus-turret addition the
  viewer already does) and the gun mount's pitch is the elevation, which
  mortars mirror into their own cached tube pitch - same number, so one
  read path covers every emplacement type. All of it verified live by
  aim-diffing and firing a manned TOW, L16 mortar and M2. Both land in
  the turret record the viewer already speaks, plus a new optional
  `pitch` no tank emits.

- An emplacement is now ONE thing on the map instead of two. Recording the
  gun as a vehicle had given every emplacement a second icon - the
  baseplate's proper badge plus an artless triangle for the gun - and the
  newly captured aim rendered nowhere. The gun no longer draws at all: the
  deployable badge is the one element, it grows a barrel needle showing
  the gun's live traverse (root yaw plus swivel, the same addition tank
  turrets use), and hovering or clicking the badge surfaces the GUN - the
  crew, the magazines, the elevation in degrees, the structure's health -
  because an armed deployable's hit is resolved to its gun at the
  hit-test, so every panel downstream worked without knowing anything
  changed. TOW, HMG and AA emplacements also stopped being blank badges:
  the icon set never shipped art for crewed emplacements, so the closest
  role silhouettes stand in (ATGM gets the HAT rocket, machine guns the
  MG, auto-launchers the grenadier) until real icons are extracted from
  the game files, and the known gun classes carry friendly display names
  instead of raw blueprint strings.
- A mortar's elevation read as a few degrees on a tube pointing halfway to
  vertical. The recorded pitch is the gun mount's rotation relative to its
  REST pose, not an absolute angle - direct-fire emplacements rest level,
  so their delta is the true elevation, but a mortar rests at Squad's
  800-mil minimum, 45.0 degrees, so the display showed the offset and
  called it the elevation. The proof doubled as the calibration: a tube
  cranked to its stop recorded +43.8, and 45 plus 43.875 is 88.875 degrees
  - exactly the in-game 1580-mil maximum. Mortars now display the readout
  a mortar player actually dials, mils with degrees alongside, and every
  other emplacement keeps raw degrees, which for them were always true.
  The recording is untouched - it was telling the truth all along; the
  display just had to learn which truth.
- A dead missile no longer hangs in the sky for a minute. A wire-cut or
  self-destructed TOW's actor lingers in server memory long after it
  stops flying - parked mid-air with bHasImpacted still false, because a
  wire cut is not an impact - and the reader faithfully recorded the
  corpse: one real shot produced 32 flight samples and then 148 copies
  of the same frozen position, drawn as a missile standing in the air
  for 53 seconds, with the impact ring firing only when the actor was
  finally garbage-collected. A live powered round moves every build, so
  a guided projectile repeating last build's exact position is dead and
  stops being emitted - the death-point record itself is kept, so the
  file still shows where it died. Measured against every recording with
  a guided shot in it: all 92 flight samples kept across three missiles,
  only bit-identical repeats dropped. Resting smoke rounds are not
  guided and are untouched. Recordings already written keep their
  ghosts, so the viewer applies the same idea from the recorded data -
  an explosive, unimpacted projectile frozen across two tick-advancing
  frames is treated as dead: ring at the true death point, icon off,
  trail left to fade.
- A killed missile came back from the dead every twelve seconds. On a
  recording that still carries the ghost records, the viewer's freeze
  rule rightly killed the icon at the true death point - and then
  garbage-collected the dead tracker once its trail faded, while the
  ghost records were still streaming. The next frame met an unknown
  projectile, drew it standing mid-air, killed it again two ticks later,
  rang again - a resurrection loop dressed up as a mid-flight explosion.
  A dead tracker is now pinned for as long as its actor is still in the
  snapshot; per the first viewer's report, the trail also stopped aging
  out mid-flight - a guided missile now draws its WHOLE path, launch to
  warhead, for as long as it flies, and the complete trail fades out as
  one when it dies. And the stutter got its real fix: projectile motion
  is dead-reckoned in the tracker from raw samples (velocity from the
  last two, capped, halted the moment the freeze rule starts counting),
  replacing the frame-pair lerp that two-tier reconstruction quietly
  defeated - reconstructed frames repeat the base frame's projectiles by
  reference, so the old lerp glided one span in four and froze the rest.
  A fixed-trajectory round cannot change course between samples, so
  extrapolation is exactly its truth; for a steered missile the
  between-sample estimate is the accepted price of motion that moves.
- The dead-reckoner lasted one viewing session before its own author's
  tester shot it down. Its velocity came from wall-clock gaps between
  frame arrivals, and replay frames arrive with jitter - two frames
  landing nearly together made the gap a few milliseconds, the velocity
  astronomical, and the extrapolation flung the icon hundreds of metres
  offscreen, which read as the missile vanishing; every new sample then
  snapped it back, which read as worse stutter. Both reports were the
  same bug. Motion now renders ONE SAMPLE BEHIND instead - gliding from
  the previous raw sample to the newest over a clamped, smoothed arrival
  interval, the same render-behind philosophy the live path uses. It can
  never overshoot recorded data, cannot be flung by a jittery arrival,
  and costs one sample (~300 ms) of display lag a replay cannot feel.
  Two hardenings landed with it: the tracker-vanish heuristic is
  debounced (a projectile absent for ONE frame is a dropped record, not
  an impact - death needs absence across two tick advances), and the
  recorder tolerates a single repeated guided-missile position (a build
  racing engine replication) so it can never punch a one-frame hole
  mid-flight for that heuristic to misread.
- Two attempts to smooth projectile motion lost to the interpolation that
  was right all along. Removing the projectile lerp made the canvas
  graft pair one frame's tick with the NEXT frame's raw positions, and
  at every frame boundary the playhead's zero-alpha branch flipped the
  position back a step - a ping-pong that was, at once, the reported
  hitching, the false freeze-kills that made the missile vanish
  mid-flight, and the flooded trail that forgot its launch. The lerp is
  restored; the dead-reckoner and the render-behind glide are deleted;
  the tracker owns identity, trails and death, and motion belongs to the
  interpolator. The trail now spaces its points at least 25 m apart so a
  whole flight fits its buffer at any frame rate. What remained of the
  original stutter had a data cause: position frames carry players and
  vehicles but not projectiles, so every reconstructed frame repeated
  its base full's projectile positions and the render lerp stalled at
  each one - a hitch at the position-frame cadence. Sampling projectiles
  at 4 Hz was tried and REVERTED after measuring it against real
  100-player recordings: 90-180 rounds airborne at barrage peaks cost
  2-7% of file size and out-read the entire player roster at exactly the
  busiest moments, for a round (the wire-guided TOW) whose server-side
  path diverges from its firer anyway. The fix lives in the viewer
  instead: once a replay is fully loaded, the bracketing full for every
  reconstructed frame is known, so a load-time pass fills their
  projectile positions by interpolating between the fulls on each
  frame's own timestamp - the same straight segment the renderer already
  draws, precomputed so the pair stream never stalls. Works on every
  recording ever made, single-tier production files included, and costs
  the recording nothing.
- The recorder writes the dead missile's corpse again, on purpose. The
  frozen-ghost emission stop was reverted by the owner's call, and the
  reasoning is worth keeping: the lingering actor is genuinely what the
  server captured, the viewer already recognises the signature and
  cleans up the DISPLAY on any recording old or new, and keeping the
  raw record in the file means the behaviour can be reinterpreted later
  without wishing data back into files that no longer hold it. The
  recorder's job is memory truth; interpretation lives in the viewer.
  The kind "guided" stamp stays - it reads the engine's own class
  hierarchy, so a guided weapon Squad ships next year classifies
  correctly the day it lands, with no name list to grow.
- A steering trail could no longer survive the scrub bar. The trail was
  accumulated incrementally in playback order, so a backward seek reset
  the tracker (trail restarting mid-air at the seek point, heading
  re-settling - reported as the missile "changing course" with no impact
  ring) and a forward skip tripped the teleport guard and wiped it
  without re-anchoring. In a replay the whole flight is already on disk,
  so the trail is now PRECOMPUTED at load - every guided round's
  complete path, launcher anchor included, each point keyed by the tick
  that produced it - and the renderer draws "the recorded path up to
  the playhead": a pure function of data plus current frame that no
  seek in either direction can tear. Verified with a headless harness
  (kept in frontend/harness) that replays the real recording through
  the actual viewer modules and diffs a straight playthrough against a
  mid-flight backward seek: identical trail at every tick. Live mode
  keeps the incremental trail - there is no future to precompute from.
  One refinement followed the first viewing: trail points were keyed by
  TICK, and a reconstructed frame carries its base full's tick while
  holding a position interpolated toward the NEXT full - so those points
  passed the playhead filter one frame early and the trail tip rendered
  ahead of the missile. Points are keyed by their frame's TIMESTAMP now,
  which every frame owns individually, and the tip can no longer lead
  the round it belongs to.
- A guided missile's trail now starts at the launcher, and every shell
  points where it is going. The trail's first point was the round's
  first recorded sample, ~70-90 m downrange (the distance covered before
  the first build catches it) - the firer's name is on the round and the
  launcher has that player in a seat, so the trail anchors to that
  recorded position when one sits within 500 m, an honest join of two
  recorded facts. And the projectile icon derived its heading from
  per-frame deltas: at 60 fps a TOW moves about a metre per frame
  (jittery heading) and a near-vertical mortar shell moves centimetres
  on the map (heading never computed at all, so the shell icon pointed
  wherever it spawned). Heading now comes from accumulated displacement
  - an anchor that re-plants every 3 m of travel - which gives every
  round a stable, correct direction of travel however slowly it crosses
  the map.
- A mortar round detonated at the mortar pit seconds after launch - in
  the viewer only, and only since the frozen-ghost rule landed. The rule
  tested movement in X and Y with a metre of tolerance, but a round
  fired near maximum elevation climbs almost vertically: under a metre
  of drift per frame on the map while Z screams upward, so two frames in
  a row read as "frozen" and the round was declared dead at the top of
  its first second. Death is now what the recorder's own rule always
  said: a BIT-IDENTICAL position in all three axes - a stopped actor
  repeats its transform exactly, and a live round always differs
  somewhere, if only in height.
- A mortar round could steal a flying TOW's tracker - and with six rounds
  in the air beside one missile, they took turns. The tracker's
  nearest-neighbour fallback, inherited from the original module, matched
  any unknown projectile to any track within 2500 metres - half the map -
  and REBOUND it: the TOW's tracker deleted, its identity handed to a
  freshly spawned mortar round, and the missile's next record forced to
  start a new tracker from nothing. The theft predates every recent
  change, but a tracker used to hold almost nothing, so being robbed cost
  one heading blip; now it holds the steering trail, the motion pacing
  and the death state, so each theft restarted the trail mid-flight,
  snapped the motion, and flickered the icon - measured against the
  recording that surfaced it: six thefts in one flight, one per symptom
  report. The fallback now runs only for id-less rounds whose position
  bucket drifted, only against id-less tracks of the same class, within
  150 metres - an id-bearing projectile is a distinct actor, and a track
  keyed by an id can no longer be stolen at all.

### Fixed
- The kill the game itself had credited was thrown away on every licensed
  server. When a downed player finally dies, Squad's own Die line names the
  credited killer by id, and for a death that outlived the wound correlation
  that id is the last thing left identifying them. The reader looked it up in
  the memory roster - where a licensed server reports an account UUID, while
  every id in the log is a 32-hex EOS ProductUserId. Two namespaces, one map,
  no possible hit: the recovery written to kill the bare "?" rows had never
  once fired on the servers this reader actually runs on. It now asks the log
  instead of the roster. Every damage line already prints an attacker's name
  and their id side by side, and every revive prints both players and both
  ids - which is where the medics come from, who can go a whole match without
  dealing damage - so the parser keeps what it has read and answers the Die
  line out of its own pages. The same pairing repairs the self-death check,
  which was asking "is the credited killer the victim themselves?" in the
  same wrong namespace and always hearing no. A player the log has never
  named resolves to nothing at all, and their id is still handed to the
  roster afterwards, because where the two namespaces do coincide that lookup
  works. Nothing is guessed on the way past: an id nobody can name is still a
  question mark.
- The kill feed printed a kill the game itself had denied. It keeps a window
  of recent wounds so a death that lands seconds later - a bleed-out, a
  give-up - can be credited to whoever put the player down, and nothing in
  that window is cleared when a medic gets there. So: downed by Bob,
  revived, then killed inside the window by something the window can never
  hold - a give-up or a world cause, both attacker-less and therefore never
  buffered - and the survived wound won. The feed said "Bob killed X" while
  the death frame was holding the right answer in its other hand. A wounded
  entry now yields to the death frame's own killed event when that event
  says self-inflicted, or names a cause with nobody behind it; a buffered
  KILLED event keeps its precedence, because there the backend and the
  window agree. And because a give-up's event runs one frame behind the
  counter it belongs to - the same lateness an earlier fix already found -
  a death whose frame carries nothing of its own now waits one frame before
  the survived wound is allowed to answer for it. Measured over five
  recorded matches: of 1539 feed rows, 13 changed - eleven from a name to
  Suicide, and two to a different name, both of those because the game's own
  killed event arrived a frame late naming somebody the wound had nothing to
  do with (a frag, and a BM-21 rocket). 128 more rows are identical and
  appear one frame later, which is what the wait costs. Clearing the wound
  at the revive was measured and not shipped: the deaths counter never moves
  on the frame a player goes from wounded to healthy, so that rule cannot
  tell a revive from a respawn - it would have fired on 1968 transitions to
  reach the 584 that were revives.
- A player who waited out most of the medic timer died as a question mark.
  The correlation that names the wounder when a downed player finally gives
  up remembered wounds for 180 seconds - but Squad allows 300 before the
  forced give-up, and players hoping for a revive routinely use most of
  them. Verified on a real match: wounded at 27:24, gave up at 31:41, and
  the feed showed "?" while the game itself credited the wounder. The
  memory now outlasts the longest possible incap. Recordings already
  written keep their nulls - a recording cannot be got back afterwards.
- A suicide showed in the kill feed as a question mark. The capture was
  perfect - the recording holds a self-inflicted killed event naming the
  victim - but the death counter increments one frame before the tracker
  emits that event, and the feed judged the death on the frame it happened:
  no evidence yet, so an honest "?", one frame before the honesty was
  earned. Suicide events also never enter the attack buffer - they have no
  attacker - so the late event had no second chance to attach. An
  evidence-free death is now held and retried until a frame with a NEW tick
  still carries nothing - not merely until the next frame, because a
  two-tier replay interleaves reconstructed position frames that share the
  base frame's tick and carry no events by design, and the first version of
  this hold conceded on one of those, one frame before the evidence could
  possibly arrive (verified against the real recording: full frame, position
  frame, then the evidence). The same hold attributes any ordinary kill
  whose event runs a frame late, and a death on a recording's final frame is
  flushed rather than lost. The feed is computed by the viewer, so this
  repairs old recordings too - the same replay that showed "?" shows
  "Suicide" on reload.
- Backfilling a two-tier recording handed the stats writer its 4 Hz position
  frames. A position frame is not a snapshot - it is a side channel to the
  recorder, and the live reader never gives one to the stats writer - so
  passing one along does not do nothing, it does damage: with no game state
  in it, the writer reads it as an unreadable tick, and an unreadable tick
  breaks a match-end confirmation in progress. Those frames sit between the
  confirming ticks, so the confirmation could never complete and every
  replayed match came out `unverified`, with no winner and no rating, however
  complete the recording was. The backfill now skips them, the same rule the
  metadata rescan and the plugin replayer already applied.

- Every recording stopped one frame before the end of its match. The frames
  that prove a match ended - three consecutive not-playing ticks, the same
  evidence the live writer requires - were counted and thrown away, so a
  finished `.sqrx` ended on the last frame of play and held nothing to show
  the game was over. The sidecar asserted it; the stream could not. Replay
  that file and it can only conclude `unverified`: no confirmed ending, so no
  winner, so no rating - while the agent, watching those same frames go past
  live, had recorded all three. The whole archive was missing the ending of
  every match in it, and nobody could have noticed without replaying one and
  comparing. Those frames are now written. They are counted separately from
  `ticks`, exactly as the 4 Hz position frames are, so `durationSec` and
  `peakPlayers` keep describing the match that was played - which is also how
  the stats row measures it. An uncertain shutdown writes however many frames
  it actually saw, because one is not three and a replay has to be able to
  tell the difference.

- Every projectile's firer read as nobody, and it took two findings to fix.
  First, the field the reader targeted had moved eight bytes in a Squad
  update, leaving two bools where it used to be - so a bool and its padding
  were being read as a pointer, 95 494 times out of 95 494 in four real
  matches, and failing safe meant nobody was told for a version cycle.
  Second, correcting that offset changed nothing, because live fire showed
  the field itself - DamageInstigatorController, despite the name - is never
  populated when a round spawns. What Squad actually stamps on a projectile
  is the engine's own Instigator, the firing pawn: a memory probe against
  freshly fired rockets and smoke hit the pawn 178 times out of 178 and the
  controller zero. The firer now follows the pawn to its player state, both
  offsets taken from reflection so they heal across Squad updates, with the
  controller chain kept only as a fallback for whatever context does fill it.
- On a two-tier replay the viewer's tick counter seesawed by about a thousand
  and the Hz readout jittered between 3 and 4.5. Both are the same mistake
  read twice. A position frame carries two counters - the 4 Hz sampler's own
  loop count and the build its positions were spliced onto - and the viewer
  was displaying the first, so reconstructed frames showed the sampler's
  number and full frames the builder's, alternating, the two having drifted
  apart for as long as the service had been up. It now reads the build
  counter, which the encoder was shipping all along. The rate readout had the
  matching problem: it timed arrivals, and a two-tier stream arrives four
  times a second, so a reader building one snapshot a second was labelled 4
  Hz. It now times the tick advancing, which means the same thing on a
  single-tier recording and the right thing on a two-tier one.
- `doctor` reported offset drift on six stats-collector fields whenever the
  server was quiet. The rule was "some player must be carrying a value, or the
  offsets have drifted", and those counters are event-gated: Squad only
  creates a player's entry once they first score in that category, so warmup -
  or a whole round where nobody destroyed a FOB - looked exactly like a broken
  offset. It sent somebody re-deriving offsets that were fine. A value now
  passes whether or not it is zero (a verified zero says the entry exists and
  we read it), nothing to read is reported as skipped rather than failed, and
  the one thing this check can honestly call drift - two fields out of the
  same collector entry disagreeing about whether they read at all - is the one
  thing it now fails on.
- A link into the viewer stopped working the moment anyone reloaded it. Only
  four paths mapped to the app, so `/viewer/<recording-id>` - a route the
  browser resolves for itself once the page has loaded - reached the server
  as a real request and got a 404. It worked if you clicked your way there
  and failed if you shared the link, which is the worse of the two. Anything
  that is not an API path and does not look like a file now serves the app; a
  missing bundle or image still gets its 404, because an HTML body under a
  `.js` URL is a syntax error three layers from the actual problem.
- Every detector that reads the kill feed was silently doing nothing. The
  attacker on a damage event is looked up by account id, and the lookup gave
  up rather than falling back to the name when that id matched nobody - which
  is what happens whenever the log and the game's memory number players
  differently. Measured against four real matches: 0 of 789 events with a
  weapon on them resolved an attacker, against 780 by name. So
  `infinite_ammo`, `remote_melee` and `magic_bullet` were not quiet, they were
  inert, and had been since they were switched on. The id is still tried
  first, because a name is unique only by convention. The same measurement
  retired a substring comparison between the event's weapon and the gun the
  player is holding: exact equality matched every one of the 2499 comparable
  pairs the loose test did, and a substring rule can pair two unrelated class
  names by accident.
- A stale soldier reading is no longer read for ammunition. A stale block
  repeats last tick's magazines, and a frozen magazine count is exactly what
  an ammo cheat looks like - so a stale read did not weaken that signal, it
  manufactured it. Real example from the archive: a soldier stuck at
  `[30, 29, 30, 30, 30, 30]` for a dozen ticks with kills still arriving.
- Replays failed to load behind a spec-strict reverse proxy. The server spoke
  HTTP/1.0 - the stdlib default - while streaming recordings with
  Transfer-Encoding: chunked, which does not exist in HTTP/1.0. Lenient
  clients decode it anyway, which is why a direct connection never showed the
  problem; a proxy that follows the spec (traefik) forwarded the chunk
  framing as body bytes, and the viewer reported "Recording failed to load".
  The server now speaks HTTP/1.1 and closes each connection explicitly,
  keeping the one-request-per-connection lifecycle it always had.
- Three vehicles nobody could ever ride were recorded in every frame of some
  matches. Squad pre-spawns one instance of each emplacement gun its factions
  could build - a ZiS-3, an emplaced ZU-23, an M2 tripod - and parks it at
  the exact world origin until somebody builds one. The reader faithfully
  recorded these staging actors as vehicles: team 0, full health, no seats,
  sitting at (0,0,0) from the first frame to the last. World origin is inside
  the play area on most maps, so the viewer drew them as a stack of phantom
  icons mid-map. The snapshot now drops an unmanned vehicle parked at the
  exact origin - a physics-settled vehicle never rests there, and the check
  is per-tick, so the moment one is genuinely deployed it appears like any
  other. Recordings already written are immutable and keep their ghosts, so
  the viewer applies the same rule as frames enter its store - which cleans
  the old replays and streams from older backends in the same stroke.
- The crew of every built emplacement was read perfectly and thrown away,
  every tick. The gun a fully built mortar, TOW or HMG bunker spawns is a
  real vehicle in Squad's engine - ordinary seat machinery, occupant name
  and id included, plus a pointer back to the baseplate deployable it
  belongs to - and the reader was reading all of it. But these guns carry
  MaxHealth 0 by design (the baseplate owns the health), and MaxHealth 0 is
  the signature the vehicle junk filter uses to recognise a freed memory
  slot, so every gun was discarded on the way to the snapshot and no one
  who ever manned an emplacement appears at one in any recording. Verified
  live with a player on a TOW: the seat named them in full while the filter
  ate the record. The gun's baseplate link is now read (by reflection, with
  the live-verified offset as fallback) and recorded as `owningDeployable`,
  and a vehicle that carries it is exempt from the MaxHealth rule - that
  link is exactly what a freed slot cannot have. Emplacement guns now
  appear as vehicles: crew in the seat panel, joinable to the deployable
  that carries the placer and build state. The pool actors Squad parks at
  world origin carry no baseplate link, so the origin rule above still
  removes them; and because the guns flow through the ordinary vehicle
  pipeline, the stats vehicle boards begin accruing emplacement seat-time
  under the gun's class name from here on.
- The first emplacement gun ever displayed read "0% · 500/0" and claimed to
  be parked. Both are the gun's own fields telling the truth about the
  wrong actor: the gun carries MaxHealth 0 because the baseplate owns the
  health, its team follows the OCCUPANT (0 whenever nobody is on it), and
  it is permanently attached to its baseplate - so the health bar divided
  by zero, an unmanned gun drew neutral on the map, and the
  attached-vehicle cue showed on every emplacement forever. The viewer now
  follows the owningDeployable join it was already given: health, bar and
  team come from the baseplate structure, and the attached/parked note and
  map ring - which exist to flag the RARE case of a vehicle riding another
  actor - are replaced for guns by an "emplacement gun" label. Viewer-side,
  so the recording that surfaced it displays correctly on reload.

- A recording now carries the revives the log was already telling us about.
  The tailer has matched the revive line since the kill feed was built, but
  only to clear correlation state: it read "X has revived Y", forgot who had
  downed Y so their next death would not be miscredited, and dropped the
  line. Nothing else in the game reports a completed revive - memory carries
  the medic's item and target only while a channel is running - so that
  discarded line was the entire record of the act, and no recording made so
  far holds one. Revives now ride the snapshot as their own list, carrying
  both players' names and account ids and the log's own timestamp, and they
  merge at the same point the kill feed merges, so a replay recomputing from
  the file sees exactly what the live pass saw. Only the ticks that produced
  a revive carry the key, and a line that omits the reviver's id-bearing
  prefix records the revive with an empty reviver rather than a guess at who
  it was.

- A recording now knows who a medic is healing and how many dressings they
  have left. Health numbers alone say that somebody got better, never who
  did it or with what, so every medical question there is - heals given,
  dressings spent, whether a squad's medic is actually keeping their squad
  alive - was unanswerable from a file. Each player holding a healing-family
  item now records which item it is, the uses left in it, and, while a
  bandage or a bag heal is actually running, the account id of the player it
  is pointed at, self-heals included. The item is recognised by the game's
  own healing base class rather than a list of names, so a new faction's
  dressing classifies itself the day Squad ships it; every field is resolved
  by reflection with no hardcoded fallback, so a rename blanks the block
  instead of reading whatever now sits at a remembered offset; and a heal
  target that does not resolve to a player read on the same tick is left out
  rather than guessed. Measured on real production recordings through the
  recorder's own compression: 84-104 KB a match, 0.20-0.21% of the file.

## [1.4.4] - 2026-08-19

### Added
- Plugin alerts can be sent to a Discord webhook. Detection already worked
  and every alert already landed in the database, but nothing ever read that
  table - so the only way to learn about a cheater was to open SQLite, which
  means nobody learned about anything. Set `alert_webhook` in the config.
  Alerts carry the evidence the detector based its call on and a link to the
  replay they came from.
- Plugins can be switched on from the config file (`plugins_config`), not only
  from the command line. On a deployment whose start command belongs to an
  image or a unit file somebody else manages, the detectors could not be
  enabled at all - which is why they had never run anywhere.
- `scripts/plugin_replay.py` runs the detectors over recorded matches offline.
  A detector is an accusation generator, and until now the only way to find
  out what its thresholds did on your server was to switch it on and watch a
  channel fill with names. Now it can be measured against the archive instead.
- The map has a ruler: right-drag (or shift+drag) reads the distance in
  metres with its compass bearing, and a drag continued from the last
  endpoint extends it into a route, each leg keeping its own reading with
  the total at the end. Distances are flat map metres - the ones keypads are
  drawn in - so height is ignored on purpose: two points either side of a
  valley are not closer for sharing an altitude. A plain click or Escape
  puts the measurement away; panning the map, or inspecting one of the two
  things just measured between, does not.

### Changed
- The Commons Clause now sits on top of the AGPL: use it, change it, share
  it - just do not sell it, and that includes selling hosting or support
  whose value comes substantially from this tool. Running a game server that
  happens to use sqreader is not selling it, donations and paid whitelists
  included: the value there is the server, not this. Everything the AGPL says
  still holds, and the added condition makes sqreader source-available rather
  than open source in the OSI's sense - deliberately, and said plainly in
  LICENSE, README and NOTICE rather than buried.
- Replay decoding in the browser is about twice as fast: guarding every field
  against one awkward key cost more than half the time, and only that key
  needs it.

### Fixed
- On a two-tier recording the viewer discarded every 4 Hz position update. The
  compact format wraps those lines so they are never diffed, and the browser's
  decoder had no branch for them at all, so each one came back as a copy of the
  previous full snapshot - the replay played at the full-frame rate while a
  quarter of the download was thrown away on arrival. Nothing compared the two
  decoders against each other, which is why it survived; they now are.
- Cheat detection measured "sustained for N ticks", which only meant what it
  says at one tick rate. The inherited value meant 16 seconds at 0.5 Hz and
  2.7 seconds at 3 Hz - and a parachute landing or a bail from a moving
  vehicle lasts about that long, which is a false accusation waiting for a
  config nobody thought to rescale. Durations are now measured in seconds from
  the game's own clock and mean the same thing at any rate.
- The 4 Hz position sampler could write a non-finite coordinate. Its gate was
  a magnitude test, and `abs(nan) > 5e6` is false by IEEE rules, so a torn
  read passed straight through into the recording; `z` was never checked at
  all.
- A recording whose layer the reader could not identify - a scrim layer a
  community named after itself - drew a bare grid. The layer lookup is keyed
  on the exact display name, so those frames carried no map at all; the viewer
  now falls back to the map name, which they do carry.
- The admin free-camera is no longer drawn on the map. It is not a player,
  nobody in the match can see it, and it moved wherever whoever was spectating
  happened to look.
- Direction and Frontline markers are drawn as the strokes they are, with the
  placing squad in a disc at the point the drag began.

## [1.4.3] - 2026-08-13

### Fixed
- Direction and Frontline markers were drawn as a spotted infantryman. They are
  not points: a squad leader drags them across the map, and the game records how
  far and in which direction - two numbers this reader had recorded as unused
  padding. With no geometry to draw, the viewer fell back to the generic
  infantry glyph, so an order spanning half the map appeared as one soldier
  standing where the drag began, indistinguishable from a spotted enemy. Both
  are now read and drawn as the strokes they are, and a point of interest is a
  diamond rather than a third thing wearing the same soldier icon.
- Recordings made before this carry no such geometry and never will. Those
  markers are drawn as a plain point rather than an arrow aimed at a guess.
- A locked squad is now recorded and shown as locked on the scoreboard.

## [1.4.2] - 2026-08-10

### Fixed
- Servers not installed through LinuxGSM lost most of their kill feed without
  being told. The agent looked for the game's log in one fixed place - the
  layout LinuxGSM happens to use - so a server installed anywhere else found
  nothing, quietly fell back to sampling kills from memory, and produced
  statistics that looked reasonable and undercounted. It now asks the running
  game where its own log is, which is correct wherever the server was
  installed, and says so plainly when it still cannot find one.

## [1.4.1] - 2026-08-10

### Fixed
- An agent that could not read the game gave up instead of asking for help. If
  the Squad server was not running yet, or a game update moved the things the
  reader looks for, it stopped before it had said anything to anyone - and then
  did the same on every restart, quietly, forever. That is the one fault that
  arrives on every server at the same moment, and it disabled the only channel
  that could have delivered a fix.
- It now keeps trying, and keeps reporting itself while it does. A server in
  that state is visible as down, with the reason, and can still receive a
  corrected set of memory offsets or a new version - so recovering from a Squad
  update does not involve logging into anyone's machine.

## [1.4.0] - 2026-08-10

### Changed
- The project is free software again, under the GNU Affero General Public
  License v3. Run it, change it, host it; if you host a changed version for
  other people, publish your changes. The previous release was proprietary and
  tied the right to use it to enrolment with one particular central - that is
  now gone. Releases distributed under the AGPL before the proprietary period
  were always still AGPL; this puts everything back on one licence.
- Internal deployment runbooks are no longer part of this repository. They
  described one specific set of servers and were of no use to anyone else.

## [1.3.3] - 2026-08-09

No functional change. Published so that a server already running 1.3.2 would be
asked to install it, unattended, and be watched doing so - the one part of
remote updating that cannot be proven anywhere except on a live server.

## [1.3.2] - 2026-08-09

### Fixed
- The self-updating added in 1.3.1 could never actually run. The agent
  recognised a genuine installation as if it were a developer's source
  checkout, decided there was nothing to replace, and threw the update away -
  quietly, on every server. Found by running the real 1.3.1 build on a Linux
  box rather than trusting the tests, which cannot reproduce the packaged form
  of the program.

## [1.3.1] - 2026-08-09

Remote updates now actually install themselves. Until this release the agent
downloaded and verified a new version and then sat on it: nothing ever applied
it, so every upgrade was a manual visit to the server.

### Added
- The agent installs a verified release on its own. It waits for a moment with
  no match in progress, exits, and comes back on the new version - the restart
  is what performs the swap, so no recording is ever cut in half by an upgrade.
- An upgrade that will not run is undone by itself. The incoming binary has to
  start and report its version before it is allowed to take over, the previous
  one is kept next to it, and a version that cannot get through startup twice
  is put back. A bad release is then remembered as bad and never offered to
  that server again.

### Fixed
- A fresh install could download the map-and-viewer archive and install it as
  though it were the agent, depending only on the order the release listing
  happened to be written in.

## [1.3.0] - 2026-08-09

Required for everyone: until now a match that simply stopped being watched was
published with a made-up result.

### Fixed
- Matches that the agent never saw end were recorded as if they had, with
  whatever the score happened to be at the moment it stopped looking, and a
  winner worked out from that score. One torn read of the game's match state was
  enough to trigger it. Verified against a recording: one match was stored as
  "273-203, team 1 won" while its own replay shows the game still being played
  at the final frame. Across the archive, 123 of 821 matches carried a result
  nobody observed, and those results had been counted into ELO.
- The agent now waits for the same confirmation the replay recorder has always
  used before accepting that a match is over, and takes the score from the
  frame that carries the ending rather than the last one before it. A match it
  did not see end keeps its last known score, states that the result is unknown,
  is left out of match lists, and is not rated. Its statistics are unaffected -
  the scoreboard really was observed; only the outcome was not.
- Ticket counts now have their own sanity bound. They were sharing one tuned for
  player counters, which let an implausible reading through as a final score.

## [1.2.0] - 2026-08-08

Required on any server without an OWI license - which is most clan servers.
Before this, such a server recorded nothing at all while the agent reported
itself perfectly healthy.

### Added
- Matches on unlicensed servers are now recorded. A server with no license key
  never opens a session with Squad's backend, so the game leaves its match id
  empty for the whole match. The agent read that correctly and, having no id to
  file anything under, wrote no replay and no stats - silently, because a match
  with no id is indistinguishable from no match at all. It now derives a stable
  id for those servers instead. Licensed servers are unaffected: an id supplied
  by the game is always used as-is.
- A line on startup naming the derived id the first time one is used, so an
  operator can tell at a glance which mode their server is in.

### Notes
- The derived id is anchored on the community central assigned at enrollment,
  so two unlicensed servers can never produce the same one.
- A match that spans an agent restart keeps its id and stays one match.
- Squad restarting mid-match starts a new one, which is the same behaviour a
  licensed server has.

## [1.1.9] - 2026-08-08

Required on any machine running more than one Squad server: before this, a
second agent could not be told which game to read.

### Fixed
- An agent on a box with several Squad servers picked one by process name,
  which is the same for all of them, so which game it read came down to start
  order - and a second agent installed alongside the first read the same game.
  A `squad_port` setting now names Squad's `-Port=` and the agent resolves its
  target from that. The installer refuses to guess when more than one server is
  running, and names every path and unit after the instance so installs add up
  instead of overwriting each other.
- Under LinuxGSM the agent could crash-loop on startup. The launcher script and
  the game binary report the same 15-byte process name and the same `-Port=`,
  so the agent attached to the shell, found no game mapped, exited, and was
  restarted into the same wall.

## [1.1.8] - 2026-08-08

Recommended for every operator: restarting the agent during a live match
corrupted that match in the archive.

### Fixed
- Restarting the agent mid-match published the match early. It was written to
  central with only the duration that had elapsed - showing up as a very short
  "draw" - and the partially written recording the reader had just closed was
  accepted as that match's replay, so the archive offered "Watch" on a game
  that was still being played. The match is now left open and published once,
  when it actually ends, with its true duration.

## [1.1.7] - 2026-07-25

Recommended for every operator: the first fix below caused finished matches to
go completely unrecorded, with no error anywhere.

### Fixed
- Matches could be played, finished, and never written to disk. The recorder
  required strictly consecutive frame ids and discarded the buffer it builds
  while waiting to open a recording whenever one was missing — but two-tier
  recording produces full frames in a separate process and the reader keeps
  only the newest, dropping the rest by design. Under load those gaps reset the
  buffer faster than it could fill, so the recording never opened at all.
- A failed build-worker spawn no longer takes the agent down, and repeated
  spawn failures back off instead of forking in a tight loop.
- Upgrades now restart the service. `systemctl enable --now` is a no-op on a
  running unit, so an upgrade previously left the OLD binary running while
  reporting success.
- The installer can be re-run with no token to upgrade an enrolled server, and
  authenticates that download with the agent's own credentials.
- Recording retention stops deleting when its free-space target cannot be met
  by pruning recordings at all, and always keeps the newest few (`--min-keep`,
  default 3). It could previously empty the archive chasing space that
  something else on the disk was using.

### Added
- Logging is configured, so the recorder's decisions reach the journal
  (`SQREADER_LOG_LEVEL` to change the level). Nothing configured it before, so
  every explanation the recorder produced was discarded unread.
- A watchdog warns when a match stays in progress with no recording open.
- `retention`, `version`, `selftest` and `download-auth` subcommands.
- One-command install via `install.sh`, and a compiled single-file build.

## [1.0.0]

First public release.

### Added
- Read-only Squad game-state reader from `/proc/<pid>/mem`: players, vehicles,
  capture zones, deployables, projectiles, markers, squads, lanes.
- Match recording and replay in the `.sqrx` format.
- Per-player stats and ELO in SQLite, with a stats API and web dashboard.
- Static SquadCalc capture-zone geometry layer (shape + position).
- Anti-cheat detectors — all memory-verified, no-guess.
- `sqreader doctor` to re-verify every memory offset against the live binary.
- Machine-specific settings extracted to `sqreader.config.json`
  (`sqreader.config.example.json` template); zero-config on standard boxes.
