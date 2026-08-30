# Medical stats — prospects from the verified capture package

Status: **catalog for review** · drafted 2026-08-30 from a live two-player
probe session on the test server. The recording additions that feed groups
2-4 (`reviveEvents` + per-player `medical` dict) are approved and pending
implementation; the stats themselves are later-phase work (`stats.py` /
SquidHub) and nothing here changes what the recorder writes. Details and
supersedes the two medical rows in `stats-wishlist.md` Tier 1.

## Verified semantics everything below stands on

All verified live (Squad v10.x dedicated server, memory + log, two-player
session):

- Health is the whole medical state machine: alive 0..100; downed = health
  goes negative and counts down at exactly -1.0 HP/s; the floor is -300
  (give-up slams health straight there - the log's own `Die()` line reports
  it as `KillingDamage=-300.000000`). Remaining bleed-out time on any downed
  soldier is therefore `300 + health`.
- A revive is an atomic snap to exactly +5.0 HP with `isWounded`/`isBleeding`
  clearing the same tick, and the server log fires
  `<reviver> has revived <victim>` at the completion instant carrying both
  parties' names, EOS ids and Steam ids.
- Healing channels live in memory only - the log has zero heal/bandage lines
  (proven offset-controlled). The held item's `HealedTarget` populates the
  instant a channel starts (self or other), resolves to the target's
  identity, and clears instantly on completion, cancel, or full health -
  no staleness observed across a 40-minute session.
- Bandages stop bleeding and never add health; only the medic bag heals
  (~7.3 HP/s). Dressings carry a live use counter
  (`ItemCount`/`MaxItemCount`).

## Group 1 — retroactive: existing recordings already carry the fields

These need no format change and classify the entire banked corpus.

| Stat | Derivation | Notes |
|---|---|---|
| Down outcome per incapacitation: revived vs gave-up/bled-out | wounded-exit health snap: -> +5.0 = revive, -> -300 = give-up | Resolves every historic wounded-exit with zero inference |
| Revive rate when downed / give-up rate | outcomes above, per victim | "How often does your team pick you up" |
| Time spent downed | wounded-flag window per down | Full-tick precision (~1 Hz) |
| Clutch-revive margin | health at the revive tick is the bleed-out clock (-280 = saved with 20 s left) | "Closest saves" leaderboard |
| Patience: time waited before giving up | down start -> -300 snap | Distinguishes instant give-ups from full bleed-outs |
| Bleeding time and how each bleed ended | `isBleeding` window + what closed it (bandage tick / down / death) | Self-vs-teammate attribution only from group 3 data onward |

## Group 2 — from `reviveEvents` (recordings made after the format change)

| Stat | Derivation | Notes |
|---|---|---|
| Revives given | event count per reviver | Dual-EOS attribution straight off the log line |
| Times revived | event count per victim | |
| Who-revives-whom network | reviver x victim matrix | Squad cohesion; "personal medic" pairings |
| Time-to-revive | victim's preceding `Wound()` event (already in `damageEvents`) -> revive event | Both directions: medic response time, and a player's average wait when downed |

## Group 3 — from the per-player `medical` dict (post-format-change)

| Stat | Derivation | Notes |
|---|---|---|
| Dressings consumed, split self vs teammate | `count` decrements, target at that tick | Exact consumption, not estimated |
| Bleeds stopped for others | channel on another player whose `isBleeding` drops that tick | The bandage equivalent of a revive |
| HP healed to teammates | target's health delta summed across bag channels | Medic leaderboard headline number |
| Self-care vs teammate-care ratio | channel targets self vs other | |
| Time spent actively healing | channel-active ticks | |
| Who-heals-whom network | complements the revive network | |

## Group 4 — composites

| Stat | Derivation | Notes |
|---|---|---|
| Effective revives | revive event + victim survives a threshold afterward (their vitals keep being recorded) | Separates real saves from revived-and-instantly-re-downed |
| Medic effectiveness score | blend of revives, HP healed, bleeds stopped, response time | Shape to be decided with the community stats redesign |
| ELO impact hook | revives are score events in Squad itself | Only if/when the impact model wants it |

## Attribution conventions under polling

Healing is state sampled at full-tick rate, not an event stream, so the
HP-healed join follows these conventions (they rely on one game fact: a
living soldier's health only ever rises from bag healing - there is no
passive regen, and the revive +5 snap is excluded by the wounded-exit
signature):

- **Interval rule.** For each pair of consecutive full frames, the target's
  positive health delta is credited to the medic whose channel on that
  target was observed at either endpoint. This correctly captures both
  edges of a channel the poll rate trims: healing that started just before
  first detection, and healing that finished just before the channel
  vanished.
- **Sole healer -> exact credit.** One observed healer in the interval gets
  the full delta.
- **Co-heals split evenly - by decision.** Two or more observed healers on
  the same target (including a mid-interval handoff) means the recording
  knows who participated but the server never splits heal amounts per
  source. The delta is divided evenly between the observed healers: a
  deliberate approximation, accepted because the event is rare and the
  stat does not need to be exact - not a verified split. Healing time
  stays exact per medic either way.
- **Unobserved micro-heals are an honest gap.** A heal that starts and
  ends inside one poll gap leaves a gain with no observed channel; it
  stays uncounted rather than guessed. Episodes average ~2.5 full frames
  in the measured corpus, so this is the small tail.
- **HP received needs no attribution** - it is the target's own recorded
  health curve and is always exact.

## Boundaries (no-guess)

- Attribution flows only where the log named both parties or `HealedTarget`
  resolved to an identity; anything unresolved stays uncounted - an honest
  gap, never a guess.
- All time/HP sums are full-tick (~1 Hz) accurate, same caveat as every
  time-in-state stat; don't promise finer.
- Downs themselves are log-backed (`Wound()` -> `damageEvents`), so even a
  down shorter than a tick still enters the outcome stats.
