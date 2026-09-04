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
| `gameState` | match and layer state, incl. `worldTimeSec`; the commander rules block joins it (planned, `docs/command-assets.md` decision 2) | upstream; rules block planned 2026-09-04 | sent whole |
| `teams` | per-team record; the commander block joins it (planned, decision 2) | upstream; commander block planned 2026-09-04 | sent whole |
| `squads` | per-squad record | upstream | sent whole |
| `players` | per-player record with `soldier` (`soldier.medical` since 2026-08-30) | upstream | **index-tracked** |
| `vehicles` | per-vehicle record with `turrets` (`turrets[].weapons` and the driver record since 2026-09-04) | upstream | **index-tracked** |
| `captureZones` | capture-zone state | upstream | sent whole |
| `markers` | map markers (`distance`, `addDistance`, `yaw` planned, decision 5) | upstream | sent whole |
| `deployables` | deployables with placer | upstream | sent whole |
| `vehicleSpawners` | spawner state | upstream | sent whole |
| `rallyPoints` | rally points | upstream | sent whole |
| `projectiles` | tracked projectiles with `firer` and `team` (since 2026-08-28) | upstream | sent whole |
| `damageEvents` | damage and kill events; log-derived in serve mode today (the memory detail is lost — tracker W21) | upstream | sent whole |
| `reviveEvents` | revives from the server log; present only on ticks with revives | 2026-08-30 | sent whole |
| `commandActions` | one entry per live command actor | planned, decision 6 (2026-09-04) | sent whole |
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
