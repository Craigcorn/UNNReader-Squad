# sqreader snapshot schema

The contract for what the reader emits. Two parts: the **frame-key
register** below — every top-level key of the full frame and of the
4 Hz position line, when it arrived, and how the packed replay stream
carries it — and one section per capture added in this fork, describing
the shape of the addition. The pre-fork keys are described field by
field by upstream's `docs/findings.md` and by the viewer's
`frontend/src/state/types.ts`; this file grows one section per addition.

Rules (CLAUDE.md, "Recordings are immutable"): frames are additive and
unversioned — a new key is documented here, entered in the register in
the same commit that adds it, emitted only when present, and never
changes what an existing key means. The `.sqrx` container version moves
only with the file's byte layout; the packed replay stream's version
moves only with the set of index-tracked lists.

## Frame-key register

### Full frame (one JSON object per tick)

| Key | What it holds | Since | In the packed stream |
|---|---|---|---|
| `timestamp` | wall-clock time of the tick | upstream | sent whole |
| `server` | server label | upstream | sent whole |
| `schemaVersion` | constant label `phase3-draft` from upstream; descriptive, no reader checks it | upstream | sent whole |
| `tick` | the reader's tick counter | upstream | sent whole |
| `perf` | reader diagnostics (build time, cache counters) | upstream | sent whole |
| `counts` | entity counts | upstream | sent whole |
| `gameState` | match and layer state, incl. `worldTimeSec`; `commanderRules` (the server's commander settings) rides inside it | upstream; `commanderRules` 2026-09-07 | sent whole |
| `teams` | per-team record; `commander` (the seat's state, the vote, the cooldowns) rides inside each entry | upstream; `commander` 2026-09-07 | sent whole |
| `squads` | per-squad record | upstream | sent whole |
| `players` | per-player record with `soldier` (`soldier.medical` since 2026-08-30) | upstream | **index-tracked** |
| `vehicles` | per-vehicle record with `turrets` (`turrets[].weapons` and the driver record since 2026-09-04) | upstream | **index-tracked** |
| `captureZones` | capture-zone state | upstream | sent whole |
| `markers` | map markers; an actor marker carries `distance`, `addDistance`, `action` and `yaw` where its own class declares them | upstream; the geometry fields 2026-09-07 | sent whole |
| `deployables` | deployables with placer | upstream | sent whole |
| `vehicleSpawners` | spawner state | upstream | sent whole |
| `rallyPoints` | rally points | upstream | sent whole |
| `projectiles` | tracked projectiles with `firer` and `team` (since 2026-08-28) | upstream | sent whole |
| `damageEvents` | damage and kill events; log-derived in serve mode today (the memory detail is lost — tracker W21) | upstream | sent whole |
| `reviveEvents` | revives from the server log; present only on ticks with revives | 2026-08-30 | sent whole |
| `commandActions` | one entry per live command actor; present only while one exists | 2026-09-07 | sent whole |
| `drones` | one entry per live drone pawn | planned, decision 7 (2026-09-04) | sent whole |

### Position line (`{"t": "pos", ...}`, 4 Hz, between full frames)

The packed stream wraps the whole line as `{"p": line}` and passes it
through untouched, so every key here rides the packed stream by
construction.

| Key | What it holds | Since |
|---|---|---|
| `t` | the literal `"pos"` | 2026-08-28 (two-tier recording) |
| `tick`, `timestamp`, `fullTick` | the sample's tick and time, and the full frame it follows | 2026-08-28 |
| `players[]` | `{id, x, y, z, h, yaw}`; `id` is the player's eosId, else name | 2026-08-28 |
| `vehicles[]` | `{id, x, y, h, yaw, team}`; `id` is the vehicle id | 2026-08-28 |
| `projectiles[]` | `{id, x, y, z}`; optional — the sampler does not emit it today (shipped and reverted 2026-08-29); the viewer reads it when present | 2026-08-29 |
| `drones[]` | `{id, x, y, z, yaw}`; `id` is the pawn address, the same id as the full-frame entry | planned, decision 7 (2026-09-04) |

---

## Medical capture

Two optional keys. Both are absent from recordings made before them, so
a consumer reads them where they exist and shows nothing where they do
not — never a default.

### `players[].soldier.medical`

Present whenever that player is **holding** a healing-family item (field
dressing, medic bag), whether or not they are currently using it:

```json
"medical": {
  "item": "BP_Generic_FieldDressing_Medic_C",
  "count": 9,
  "target": "eos-0000000000000000000000000000ruby"
}
```

- `item` — the held item's class name, verbatim, exactly as
  `weapon.className` and the vehicle/projectile classes are recorded;
  prettifying it is display work. `null` only when the name read failed
  after the class check had already identified the item as a healing one.
- `count` — uses remaining in that item. Omitted when the read fails or
  the value is outside a sane range.
- `target` — the `eosId` of the player being healed. Present **only
  while a heal/bandage channel is actually running**: the game fills the
  pointer the instant one starts and clears it the instant it ends, so
  the key's presence is the channel. A self-heal carries the holder's own
  `eosId` — that is the explicit self signal, not a mistake. A pointer
  that does not resolve to a player read on the same tick is omitted
  rather than guessed.

The whole block is absent when no healing item is held, when the game's
healing base class is not loaded, or when reflection could not resolve
the fields — a Squad rename blanks the feature instead of reading
whatever now sits at a remembered offset.

### `reviveEvents`

Top level, one entry per revive the server log reported during that tick.
The key is written **only on ticks that produced one**, which is nearly
none of them:

```json
"reviveEvents": [
  {
    "reviver": "Doc",
    "reviverEosId": "eos-00000000000000000000000000000doc",
    "victim": "Ruby",
    "victimEosId": "eos-0000000000000000000000000000ruby",
    "ts": 1786615206.0
  }
]
```

- Names are resolved to the roster's base name (clan tag stripped) by the
  same rule the kill feed uses.
- `reviver` and `reviverEosId` are `null` when the log line did not carry
  the reviver's id-bearing prefix. The revive is still recorded; the
  reviver is never inferred from who happened to be nearby.
- `ts` is epoch seconds, parsed from the log line's own timestamp.
- The log is the only place a revive is evented at all: memory shows the
  medic's item and target while a channel runs, never the completion.

## Vehicle seat inventory

Two optional additions to the existing `vehicles[].turrets[]` records.
Both are absent from recordings made before them and from any seat the
reader could not resolve, so a consumer reads them where they exist and
shows nothing where they do not. Turret records without them mean
exactly what they always did.

### `turrets[].weapons`

Present when the seat's inventory resolved: one record per weapon the
seat can switch to, group by group — a tank gunner's HE, coax, smoke and
ATGM all at once, or the three pilot weapons on a Loach, not only the
gun selected this tick:

```json
"weapons": [
  { "weaponClass": "BP_M134_Minigun_C", "group": 0, "active": true,
    "magazines": [3000], "magazinesMax": [3000] },
  { "weaponClass": "BP_M260_RocketPod_C", "group": 1,
    "magazines": [7], "magazinesMax": [7] }
]
```

- `weaponClass` — the weapon's class name, verbatim, the same rule as
  `weapon.className` everywhere else in the schema.
- `group` — the group's weapon-switch slot index, read from the game's
  own group data; it is the order the switch key walks.
- `active` — written (as `true`) only on the currently selected weapon.
  Absence means "not selected", never "unknown": the selected group is
  identified by pointer equality with the seat's current weapon, and a
  tick where that comparison cannot be made emits no `active` at all.
- `magazines` / `magazinesMax` — the same shape the turret record
  already carries for its current weapon; omitted when the magazine read
  fails, never zero-filled.

The whole list is absent when the seat inventory pointer or its group
array cannot be resolved — a Squad rename blanks the feature instead of
stepping through whatever now sits at a remembered offset.

### the `seat: "driver"` record

The driver / pilot seat is the vehicle actor itself, which
`VehicleTurrets` never lists — a Loach's minigun, rockets and smoke, or
a BTR driver's smoke launcher, belonged to no record at all. When that
seat holds readable weapons, they ride along as the **last** entry in
`turrets`, stamped `"seat": "driver"`, carrying the vehicle's own
`className` and the same `weapons` list as any turret. A consumer
routes it by the stamp — it is a loadout record, not a turret: it has
no aim, no barrel and no seat pawn, and must never be drawn as one.
The record is absent entirely when the driver seat has nothing
readable.

## Commander block

Two additions and one repair, all on existing records: `teams[].commander`,
`gameState.commanderRules`, and the two commander identity fields that have
shipped empty since 2026-08-30. The contract is
`docs/command-assets-spec.md` §3 and §4 — this section is the wire shape; the
spec says what each value means and what evidence it rests on.

Absence has two meanings here, and they are kept apart deliberately (spec §2):

- **`null`** — the game's own value is empty and was read successfully: no
  commander in the seat, no element at that index yet.
- **the key absent** — the recorder could not read it: the property is not in
  the class (a Squad rename), a pointer led nowhere, the read failed.

So a consumer that sees `null` knows "none" and one that sees nothing knows
"unknown". Nothing is defaulted, and nothing is carried over from the previous
frame — every value is that frame's own read.

### `teams[].commanderName` / `commanderEosId`

Present since 2026-08-30 and never populated: the read treated
`SQTeamState.CommanderState` as a player state, and it points at the team's
commander-state actor. The seat is `CurrentCommander` on that actor, and the
identity comes off the player state it points at. Both fields are `null` when
that pointer reads null — an unclaimed seat — and absent when the read could
not be made. `commanderStateAddr` is unchanged.

### `teams[].commander`

Present on every full frame whose team record reaches a commander state:

```json
"commander": {
  "enabled": true,
  "actionsEnabled": true,
  "vote": {
    "inProgress": false, "timer": 0, "endsGameTime": 1043,
    "nominees": [{"eosId": "eos-…", "name": "Ruby", "votes": 3}],
    "cooldownActive": true, "cooldownTimer": 217,
    "cooldownEndsGameTime": 1343
  },
  "cooldowns": {
    "categories": [
      {"id": 0, "name": "…", "intervalSec": 600.0,
       "lastUseGameTime": null}
    ],
    "actions": [
      {"action": "CommandAction_UAV_MQ9_USMC_C", "displayName": "MQ-9 UAV Recon",
       "createdGameTime": 843.6, "remainingAtChange": 0.0,
       "destroyedDuringActive": false, "categoryId": 0,
       "enrouteSec": 30.0, "activeSec": 300.0, "cooldownSec": 600.0}
    ]
  }
}
```

- `enabled` is "the commander system exists on this layer", not "claimed" — it
  reads true on both teams while one of them has no commander.
  `actionsEnabled` is live state: whether the team may issue commands this
  frame.
- The `vote` block rides every frame, open or not, so a seek into the middle
  of a replay is self-describing. `timer` counts the vote window down and
  reads 0 when none is open; `endsGameTime` and `cooldownEndsGameTime` are end
  times on the same game clock as `gameState.worldTimeSec`. `nominees` keeps
  its entries after a vote resolves — that is the game's own state, recorded
  as read.
- `nominees[]` is `[]` while the array is empty (read, and none). A nominee
  whose player-state pointer reads null carries `eosId` and `name` `null`.
- `categories[]` is the per-category gate. `id` is the array index — the index
  the last-use stamps are keyed by and the value an action's `categoryId`
  carries — and it is the one key in the whole block the recorder produces
  rather than reads. `lastUseGameTime` is `null` until something in that
  category has been called.
- `actions[]` is `[]` before the first claim, then one entry per action the
  team can call. The four live values come from the entry; the five config
  values (`displayName`, `categoryId`, `enrouteSec`, `activeSec`,
  `cooldownSec`) are the action class's own defaults and ride every frame, so
  a seek is self-describing there too. An entry whose action class reads null
  carries `action` `null` and no config values — there is no class to read
  them from.
- Every "ready in", every "vote resolved", every rule about how the timers
  combine is the consumer's to derive from this per-frame state; the recorder
  computes none of it (spec §9 carries the arithmetic).

### `gameState.commanderRules`

The server's own commander settings, read off one live `SQCommanderManager`
(several exist at once and read alike). Six scalars every frame rather than
once, for the same seek-safety reason:

```json
"commanderRules": {"enabled": true, "votingTimeSec": 60,
                   "voteCooldownSec": 300, "newCommanderExtensionSec": 300.0,
                   "minSquadSize": 2, "minSquads": 3}
```

`commanderRules.enabled` is the server setting; `commander.enabled` is a
team's state. Two flags with the same word, deliberately distinct. The whole
block is absent when no manager is live or none of the six could be read.

---

## Marker geometry

Four optional keys on the actor-marker entries of `markers[]`. The contract is
`docs/command-assets-spec.md` §5 — this section is the wire shape. The
player-placed squad-data markers, which reach the same list through the marker
manager's FastArray, are a different surface and keep their own `arrowLength`
/ `arrowHeading`; neither set is reused for the other.

```json
{
  "id": "0x707db0c584a0", "type": "BP_MapMarker_CommandRadius_Friendly_C",
  "team": 1, "squad": 3, "fireTeamId": 0,
  "ownerPlayerStateAddr": "0x707d…",
  "distance": 16608.0, "addDistance": 7500.0,
  "action": "CommandAction_UAV_MQ9_USMC_C",
  "position": {"x": 12345.5, "y": -6789.25, "z": 42.0}, "yaw": 45.0
}
```

- `distance` is the marker's own length figure in raw game units (cm) — the
  commander's choice wherever the UI offers one: a coverage or barrage
  circle's radius, a strike run's length, a creeping barrage's path length, an
  aim line's separation. `addDistance` is the secondary figure beside it, the
  drop scatter or the outer band. Both read 0 on a request marker, which has
  no shape of its own.
- `yaw` is degrees, world, from the same `ComponentToWorld` transform
  `position` is read from — the marker's facing. It rides wherever `distance`
  does and nowhere else.
- `action` is the `CommandAction_*` config the marker belongs to, verbatim: a
  join from a footprint to the `teams[].commander.cooldowns.actions[]` entry
  that produced it. It is `null` on a squad leader's request marker, which
  belongs to no config.

Which keys a marker carries is decided by its own class and nothing else.
Every name is looked for on that class's reflected layout, so today the
Command family (`BP_MapMarker_CommandMaster_C` and its subclasses) emits all
four, the Director family (`BP_MapMarker_DirectorMaster_C`) emits `distance`
and `yaw`, and every other actor marker emits none — but a class that gains or
loses one of the names is followed without a code change, because no class
name is ever matched against.

Absence keeps the two meanings the commander block gave it (spec §2):

- **`null`** — the game's own value is empty and was read successfully: an
  `Action` pointer that reads null.
- **the key absent** — the class does not declare the name (most markers, and
  what a Squad rename looks like), or the read failed.

A consumer reads these where they exist and draws nothing where they do not.
Recordings made before 2026-09-07 carry none of them, and no value is
defaulted or carried from the previous frame.

---

## `commandActions`

A new top-level list: one entry per live command actor — the strike aircraft
flying its run, the artillery fire plan and its progress, the UAV on station,
the drone's call actor. The contract is `docs/command-assets-spec.md` §6 — this
section is the wire shape.

**The key is present only while such an actor exists**, which is a handful of
windows per match, 30 s to 10 min each. An absent key means "no command asset
in the air this frame", exactly what every recording made before 2026-09-07
says by carrying no key at all.

```json
"commandActions": [
  {
    "id": "0x707db0c584a0",
    "class": "BP_CommandActor_Artillery_Creep_C",
    "team": 1,
    "action": "CommandAction_Artillery_Creep_USMC_C",
    "callerEosId": "eos-…",
    "position": {"x": 12345.5, "y": -6789.25, "z": 42.0}, "yaw": 45.0,
    "actionDestroyed": false,
    "distance": 45000.0,
    "originLocation": {"x": 1000.0, "y": 2000.0, "z": 30.0},
    "targetLocation": {"x": 4000.0, "y": 5000.0, "z": 60.0},
    "maxDropRadius": 1.0,
    "preWarningShells": 2, "preWarningDelaySec": 12.0,
    "shellsPerBarrage": 10, "barrageCount": 8,
    "currentPrewarningShells": 1, "currentBarrage": 3,
    "projectile": "BP_Projectile_155mm_C"
  }
]
```

Every entry carries the common half:

- `id` is the actor's address as a lowercase hex string, the same form vehicles
  and markers use — new on every call, never a join across matches.
- `class` is the actor's class name verbatim; `action` is the `CommandAction_*`
  config it belongs to, a join to the `teams[].commander.cooldowns.actions[]`
  entry that produced it. `action` is `null` on a null pointer, which is what
  the level's template actors read at a layer load.
- `team` is the owning team. `callerEosId` is the commander who called it — the
  attribution pointer the game itself uses for the asset's kills, resolved
  through the object array with its serial check, so a recycled slot omits the
  key rather than naming the wrong player.
- `position` and `yaw` are where the actor is this frame, off the same
  `ComponentToWorld` transform everything else uses: aircraft move along their
  run, artillery sits at its origin, and the drone's call actor reads
  `(0, 0, z)` and means nothing by it.
- `actionDestroyed` says the call was cut short — it reads true while a
  shot-down actor lingers, and a natural end removes the actor without any
  frame reading it. `distance` is the actor's own length figure.

The family half rides wherever the actor's own class declares the name: the
strike aircraft's `shotsMade`, `maxShots`, `splineDistance` and
`originLocation`; the artillery's ten fire-plan fields above; the drone call
actor's `ownerEosId`. A UAV declares none of them and carries the common half
alone. No class name is ever matched against — every name is looked for on the
actor's own reflected layout — so a class that gains or loses one is followed
without a code change.

Three things the list deliberately never holds. A health figure on any command
actor: none is recorded whichever class declares one, the drone's health being
its pawn's on the `drones` list (decision D18). Who shot an actor down: no
last-damager field exists on any command actor, the server log carries no line
for it, and it is not inferred from anything nearby. And the shells, rockets
and bombs an asset fires: those are already tracked projectiles with `firer`
set to the commander, so the actor record carries the plan and its progress and
never the impacts.

Absence keeps the two meanings the sections above gave it (spec §2): `null`
where the game's own value is empty — a pointer that reads null — and the key
absent where the class does not declare the name (what a Squad rename looks
like) or the read failed. An actor whose root position reads exactly
`(0, 0, 0)` is dropped from the list entirely, the junk-actor rule vehicles
already use; a class default object is never an entry.
