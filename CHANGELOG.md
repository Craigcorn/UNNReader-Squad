# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
follows [Semantic Versioning](https://semver.org/).

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
