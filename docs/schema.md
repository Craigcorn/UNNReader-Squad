# sqreader snapshot schema

The contract for what the reader emits per tick. We're building up to
this incrementally — each phase adds more fields. The full target is
documented in the kickoff doc; this file tracks **what we actually
produce today**.

---

## Phase 0 — no snapshot yet

The reader can attach to the process and read raw memory at known
addresses. No structured output.

---

## Phase 1 — reflection anchors (planned)

A diagnostic dump only:

```json
{
  "binary_build_id": "96be21a...",
  "squad_version": "v10.4.1",
  "ue_version": "5.7.4-604352",
  "module_base": 2097152,
  "globals": {
    "g_world_string": 20682414,
    "g_world_pointer": null,
    "g_uobject_array": null,
    "f_name_pool": null
  },
  "first_uobjects": []
}
```

---

## Phase 3 target

See `squad-memory-reader-kickoff.md` § "Reference: Snapshot Schema
Contract (Truncated)" for the full target, and `kickoff-changes.md`
§ "Phase 4+" for the 11 additional reader paths (capture zones, FOBs,
damage events, vehicle pool, projectiles, markers, RAAS lane,
per-player extras, vehicle IDs, continuous mode, offset auto-verify).

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
