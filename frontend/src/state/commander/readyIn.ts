// "Ready in" — the arithmetic the recorder deliberately does not do.
//
// Every stamp here is a raw read off the frame; every combination of them is
// spec §9, "Ready-in arithmetic". The recorder writes no countdown and no
// event, so a rule corrected here is corrected for every recording at once —
// which is the whole reason the numbers ride the file and the arithmetic
// does not.
//
// Nothing below carries state between frames: an entry's `createdGameTime`
// IS the answer at a commander change, because the game moves that stamp
// itself. The one projection that cannot be read off a single frame — where
// a re-claim after a step-down would put an entry — is `reclaimReadyGameTime`
// below, and it is the formula §9 gives, not one this file invents.

import type {
  CommanderActionCooldown, CommanderCategoryCooldown, TeamState,
} from "../types";

/** enroute + active + cooldown: the whole window from the call to the next
 *  one. `null` when any of the three was not read — a config value can only
 *  be absent because the action's class pointer read null, and two thirds of
 *  a duration is not a duration. */
export function effectiveDurationSec(
  e: CommanderActionCooldown | null | undefined,
): number | null {
  if (!e) return null;
  const { enrouteSec: a, activeSec: b, cooldownSec: c } = e;
  if (a == null || b == null || c == null) return null;
  return a + b + c;
}

/** When this action's own cooldown ends: `createdGameTime` + the effective
 *  duration. The stamp does NOT move when the asset is destroyed mid-flight
 *  — a UAV and a drone shot down on 2026-09-07 kept theirs, and the game's
 *  own UI matched this arithmetic rather than destruction + cooldown. */
export function actionReadyGameTime(
  e: CommanderActionCooldown | null | undefined,
): number | null {
  const eff = effectiveDurationSec(e);
  if (eff == null || e?.createdGameTime == null) return null;
  return e.createdGameTime + eff;
}

/** When this category's gate opens: `lastUseGameTime` + `intervalSec`.
 *
 *  `null` means there is no gate to wait for — either nothing in the
 *  category has been called (`lastUseGameTime` reads null, the array being
 *  empty until the first call) or the stamp could not be read at all. */
export function categoryReadyGameTime(
  c: CommanderCategoryCooldown | null | undefined,
): number | null {
  if (!c || c.lastUseGameTime == null || c.intervalSec == null) return null;
  return c.lastUseGameTime + c.intervalSec;
}

export interface ActionReadiness {
  /** The later of the two gates, on the game clock. `null` when the entry's
   *  own arithmetic could not be done. */
  readyGameTime: number | null;
  /** That, less the frame's `worldTimeSec`. Negative once it is callable;
   *  `null` when either half is unknown. */
  readyInSec: number | null;
  /** Whether the frame's clock says it can be called now. `null` when
   *  unknown — never a hopeful false. */
  ready: boolean | null;
  actionReadyGameTime: number | null;
  categoryReadyGameTime: number | null;
  /** Which gate is the binding one, for a UI that wants to say so. The
   *  category gate holds across a commander change, and a strike really was
   *  refused with 15:00 on it after an artillery call (2026-09-07). */
  gatedBy: "action" | "category" | null;
}

/** An asset is callable at the LATER of its own cooldown and its category's
 *  gate, less the frame's `worldTimeSec` (spec §9). */
export function actionReadiness(
  e: CommanderActionCooldown | null | undefined,
  categories: CommanderCategoryCooldown[] | null | undefined,
  worldTimeSec: number | null | undefined,
): ActionReadiness {
  const own = actionReadyGameTime(e);
  const cat = categoryReadyGameTime(categoryFor(e, categories));
  let readyGameTime: number | null = own;
  let gatedBy: "action" | "category" | null = own == null ? null : "action";
  if (cat != null && (own == null || cat > own)) {
    readyGameTime = own == null ? null : cat;
    // A category gate the entry's own arithmetic cannot be compared against
    // is not a reading — say nothing rather than half of it.
    gatedBy = own == null ? null : "category";
  }
  const readyInSec = readyGameTime != null && worldTimeSec != null
    ? readyGameTime - worldTimeSec : null;
  return {
    readyGameTime,
    readyInSec,
    ready: readyInSec == null ? null : readyInSec <= 0,
    actionReadyGameTime: own,
    categoryReadyGameTime: cat,
    gatedBy,
  };
}

/** The category an action belongs to, by the `categoryId` it carries — the
 *  index into the category array, which is also the index the last-use
 *  stamps are keyed by. */
export function categoryFor(
  e: CommanderActionCooldown | null | undefined,
  categories: CommanderCategoryCooldown[] | null | undefined,
): CommanderCategoryCooldown | null {
  if (!e || e.categoryId == null) return null;
  return (categories ?? []).find((c) => c.id === e.categoryId) ?? null;
}

/** Where a re-claim after a step-down puts an entry (spec §9).
 *
 *  A step-down writes every entry's `remainingAtChange` with the time it had
 *  left and moves nothing else; at the next claim the entry becomes ready at
 *  claim + min(remainingAtChange + newCommanderExtensionSec, cooldownSec).
 *  The match's FIRST claim is a different case — every entry starts on its
 *  full cooldown, which is what the back-dated `createdGameTime` says — and
 *  this returns that when nothing was left over.
 *
 *  A projection, not a reading: once the claim has happened the entry's own
 *  stamp says it, and `actionReadyGameTime` is the answer. */
export function reclaimReadyGameTime(
  e: CommanderActionCooldown | null | undefined,
  claimGameTime: number | null | undefined,
  newCommanderExtensionSec: number | null | undefined,
): number | null {
  if (!e || claimGameTime == null) return null;
  const cooldown = e.cooldownSec;
  if (cooldown == null) return null;
  const left = e.remainingAtChange ?? 0;
  if (left <= 0) return claimGameTime + cooldown;
  if (newCommanderExtensionSec == null) return null;
  return claimGameTime + Math.min(left + newCommanderExtensionSec, cooldown);
}

// ---- joining a footprint, an actor or a drone back to its action ----------

/** The cooldown entry a `CommandAction_*` class name belongs to.
 *
 *  `team` narrows the search to that team's list, which is what a marker or
 *  a command actor carries; without it every team is searched, which is what
 *  a drone needs (the pawn carries no team at all). */
export function findActionEntry(
  action: string | null | undefined,
  teams: TeamState[] | null | undefined,
  team?: number | null,
): CommanderActionCooldown | null {
  if (!action) return null;
  for (const t of teams ?? []) {
    if (team != null && t.id !== team) continue;
    const hit = (t.commander?.cooldowns?.actions ?? [])
      .find((a) => a.action === action);
    if (hit) return hit;
  }
  return null;
}

/** The config's own display text — "MQ-9 UAV Recon", "Heavy Mortar Barrage"
 *  — carried on every action entry, every frame (decision D15). `null` when
 *  no entry names this class or the entry carries no display name, in which
 *  case a caller shows the class name rather than a guess at a label. */
export function actionDisplayName(
  action: string | null | undefined,
  teams: TeamState[] | null | undefined,
  team?: number | null,
): string | null {
  return findActionEntry(action, teams, team)?.displayName ?? null;
}
