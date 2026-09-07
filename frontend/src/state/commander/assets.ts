// What a command actor is doing this frame: was it shot down, and where in
// its fire plan is it.
//
// Both are spec §9 ("Shoot-downs", "Artillery timeline"). The barrage
// interval is NOT here, because the game does not record it: barrages
// advance at the game's own rhythm — roughly six to seven seconds on the
// 08-30 creep, eight to nine on the 2026-09-07 mortar — and the viewer reads
// `currentBarrage` rather than predicting the next one.

import type { CommandAction, CommanderActionCooldown } from "../types";

/** Was this call cut short?
 *
 *  `actionDestroyed` reads true only while a SHOT-DOWN actor lingers: a
 *  natural end removes the actor without any frame reading the flag (two
 *  natural ends and two kills, 2026-09-07). Seeing the actor at all is
 *  therefore the second half of the test, and a frame that carries the entry
 *  has already satisfied it. Who did it is not recorded — no last-damager
 *  field exists on any command actor, and the server log carries no line
 *  (decision D18) — so nothing here names a shooter. */
export function isShotDown(a: CommandAction | null | undefined): boolean {
  return a?.actionDestroyed === true;
}

/** The same fact from the team's side: the cooldown entry's own flag. It
 *  says the call was destroyed during its active window and, notably, the
 *  stamps beside it do NOT move when it flips. */
export function entryDestroyed(
  e: CommanderActionCooldown | null | undefined,
): boolean {
  return e?.destroyedDuringActive === true;
}

/** Does this actor carry an artillery fire plan? Asked of the fields, never
 *  of the class name — a class that gains or loses one is followed without a
 *  code change, exactly as the recorder follows it. */
export function isArtillery(a: CommandAction | null | undefined): boolean {
  return !!a && (a.barrageCount != null || a.shellsPerBarrage != null
                 || a.preWarningShells != null);
}

export type ArtilleryPhase = "enroute" | "warning" | "barrage";

export interface ArtilleryTimeline {
  /** `createdGameTime` + `enrouteSec`: when the guns open. The first warning
   *  shell landed at +59.7 s on a 60 s enroute (08-30 creep). */
  gunsOpenGameTime: number | null;
  /** Whether this plan has a warning phase at all. The mortar has none —
   *  `preWarningShells` and `preWarningDelaySec` both read 0 — and its
   *  `currentPrewarningShells` and `currentBarrage` both reach 1 at
   *  `enrouteSec`. `null` when `preWarningShells` was not read. */
  hasWarningPhase: boolean | null;
  warningShellsFired: number | null;
  warningShellsTotal: number | null;
  /** True once the counter has reached the total. `null` when either side
   *  of that comparison is unknown. */
  warningComplete: boolean | null;
  /** The main barrage opens `preWarningDelaySec` after the last warning
   *  shell (observed 12.1 s after the second, 08-30). Only computable once
   *  the caller can say WHEN the counter finished — the game records the
   *  count, never the moment — so it is `null` without that stamp. */
  mainBarrageOpensGameTime: number | null;
  currentBarrage: number | null;
  barrageCount: number | null;
}

export function artilleryTimeline(
  a: CommandAction,
  entry: CommanderActionCooldown | null | undefined,
  warningCompleteGameTime?: number | null,
): ArtilleryTimeline {
  const created = entry?.createdGameTime ?? null;
  const enroute = entry?.enrouteSec ?? null;
  const total = a.preWarningShells ?? null;
  const fired = a.currentPrewarningShells ?? null;
  const delay = a.preWarningDelaySec ?? null;
  const hasWarningPhase = total == null ? null : total > 0;
  return {
    gunsOpenGameTime: created != null && enroute != null
      ? created + enroute : null,
    hasWarningPhase,
    warningShellsFired: fired,
    warningShellsTotal: total,
    warningComplete: total == null || fired == null
      ? null : fired >= total,
    mainBarrageOpensGameTime:
      warningCompleteGameTime != null && delay != null
        ? warningCompleteGameTime + delay : null,
    currentBarrage: a.currentBarrage ?? null,
    barrageCount: a.barrageCount ?? null,
  };
}

/** Where the plan stands at `worldTimeSec`.
 *
 *  `null` is a real answer: past the enroute window with no warning phase
 *  and no barrage yet recorded, nothing in the frame says which of the two
 *  the guns are in, and the viewer says nothing rather than picking. */
export function artilleryPhase(
  t: ArtilleryTimeline,
  worldTimeSec: number | null | undefined,
): ArtilleryPhase | null {
  if (t.gunsOpenGameTime != null && worldTimeSec != null
      && worldTimeSec < t.gunsOpenGameTime) return "enroute";
  if ((t.currentBarrage ?? 0) >= 1) return "barrage";
  // A warning phase that has yet to finish, and the pre-warning delay that
  // follows it, are both "warning": shells are landing to be seen and the
  // main barrage has not started.
  if (t.hasWarningPhase === true) return "warning";
  return null;
}
