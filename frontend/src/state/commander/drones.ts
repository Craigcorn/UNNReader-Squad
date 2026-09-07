// What the viewer knows about a drone that the pawn does not carry: whose
// side it is on, how long it has left, and who shot it down.
//
// All three are spec §9, "Drones". The pawn has no team field, no
// remaining-time field (`EndFlightTimer` is a timer handle, `BleedOutTime` a
// constant 30 that is neither the flight time nor the linger) and no killer
// beyond a `LastHitBy` pointer that keeps moving after the kill — so all
// three are derived here and none is recorded.

import type { Drone, Snapshot, TeamState } from "../types";
import { findActionEntry } from "./readyIn";

/** The team of the drone's OWNER, in the same frame. The pawn carries no
 *  team of its own; `ownerEosId` is the deployer, it persists through
 *  de-possession and death, and a drone cannot change hands, so the join
 *  never moves. `null` when the owner is not on this frame's roster. */
export function droneTeam(
  d: Drone | null | undefined,
  snap: Snapshot | null | undefined,
): number | null {
  const eos = d?.ownerEosId;
  if (!eos) return null;
  return (snap?.players ?? []).find((p) => p.eosId === eos)?.teamId ?? null;
}

/** The drone's flight budget in seconds: the recon kit's own
 *  `batteryLifetimeMax`, or — on a commander drone, whose class does not
 *  declare that field — the calling action's `activeSec` off the commander
 *  block. `null` when neither is there to read. */
export function droneBudgetSec(
  d: Drone | null | undefined,
  teams: TeamState[] | null | undefined,
): number | null {
  if (!d) return null;
  if (d.batteryLifetimeMax != null) return d.batteryLifetimeMax;
  return findActionEntry(d.commandAction, teams)?.activeSec ?? null;
}

/** One drone's life across a whole recording — everything about it that a
 *  single frame cannot say. */
export interface DroneTrack {
  id: string;
  /** The frame the pawn first appears in, and that frame's game clock. A
   *  drone's id is new on every deploy, so this IS the spawn — unless the
   *  recording started mid-flight, which `firstFrameOfRecording` says. */
  firstSeenFrameIdx: number;
  firstSeenGameTime: number | null;
  firstFrameOfRecording: boolean;
  lastSeenFrameIdx: number;
  budgetSec: number | null;
  /** The frame the pawn first reads dead, at 4 Hz resolution. */
  deadFromFrameIdx: number | null;
  deadGameTime: number | null;
  /** The `lastHitBy` of the FIRST sample carrying `dead` — exact to a
   *  quarter second, which matters: a second shooter moved the pointer 1.1 s
   *  after a kill on 2026-09-07, inside the full frame's one-second gap
   *  (decision D19). `null` when nothing had hit it — a battery that ran
   *  out kills a drone with no shooter at all. */
  killerEosId: string | null;
  ownerEosId: string | null;
}

/** Build one track per drone id over the reconstructed frames.
 *
 *  Run this over the frames the reconstructor produced, not the raw lines:
 *  the 4 Hz death is already spliced onto them, which is what makes the
 *  killer exact to a quarter second. */
export function buildDroneTracks(
  frames: Snapshot[],
  teams?: TeamState[] | null,
): Map<string, DroneTrack> {
  const out = new Map<string, DroneTrack>();
  for (let i = 0; i < frames.length; i++) {
    const f = frames[i]!;
    const now = f.gameState?.worldTimeSec ?? null;
    for (const d of f.drones ?? []) {
      let t = out.get(d.id);
      if (!t) {
        t = {
          id: d.id,
          firstSeenFrameIdx: i, firstSeenGameTime: now,
          firstFrameOfRecording: i === 0,
          lastSeenFrameIdx: i,
          budgetSec: droneBudgetSec(d, teams ?? f.teams),
          deadFromFrameIdx: null, deadGameTime: null,
          killerEosId: null,
          ownerEosId: d.ownerEosId ?? null,
        };
        out.set(d.id, t);
      }
      t.lastSeenFrameIdx = i;
      // The budget only ever arrives late on a commander drone, whose
      // calling action has to be in the commander block to be joined.
      if (t.budgetSec == null) t.budgetSec = droneBudgetSec(d, teams ?? f.teams);
      if (t.ownerEosId == null) t.ownerEosId = d.ownerEosId ?? null;
      if (d.dead === true && t.deadFromFrameIdx == null) {
        t.deadFromFrameIdx = i;
        t.deadGameTime = now;
        // The FIRST sample carrying `dead` — later hits on the falling pawn
        // move the pointer and must not be read as the killer.
        t.killerEosId = d.lastHitByEosId ?? null;
      }
    }
  }
  return out;
}

/** Seconds of flight left: the first frame's clock + the budget − now.
 *
 *  `null` when the clock, the budget or the spawn moment is unknown — which
 *  includes a drone that was already up when the recording started, since
 *  its first frame is not its spawn. */
export function droneRemainingSec(
  t: DroneTrack | null | undefined,
  worldTimeSec: number | null | undefined,
): number | null {
  if (!t || t.firstFrameOfRecording) return null;
  if (t.firstSeenGameTime == null || t.budgetSec == null
      || worldTimeSec == null) return null;
  return t.firstSeenGameTime + t.budgetSec - worldTimeSec;
}

/** The killer of a drone from its own 4 Hz samples: the `lastHitBy` of the
 *  first sample carrying `dead`. Separated from the track builder so the
 *  rule can be read and tested on its own. */
export function killerFromSamples(
  samples: { dead?: boolean; lastHitBy?: string | null }[],
): string | null {
  for (const s of samples) {
    if (s.dead === true) return s.lastHitBy ?? null;
  }
  return null;
}
