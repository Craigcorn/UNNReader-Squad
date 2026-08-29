// Squad pre-spawns one instance of each emplacement gun class the match's
// factions could build (ZiS-3, emplaced ZU-23, M2 tripod, …) and parks it
// at the exact world origin until used — team 0, full health, no seats,
// never visible to a player. The backend skips these at capture time now,
// but recordings are immutable: every replay written before that fix
// carries the ghosts in every frame, and world origin sits INSIDE the play
// area on most maps, so they rendered as a stack of phantom icons mid-map.
// Stripping them at the store's front door cleans every source — old
// replays and live streams from older backends alike.
//
// A physics-settled vehicle never rests at exactly (0,0,0); an occupied
// actor is kept regardless, so nothing a player is actually inside of can
// ever vanish.

import type { Snapshot, Vehicle } from "./types";

export function isStagingGhost(v: Vehicle): boolean {
  const p = v.position;
  if (!p || p.x !== 0 || p.y !== 0 || p.z !== 0) return false;
  return !(v.seats ?? []).some((s) => s.occupantName);
}

// Returns the snapshot UNCHANGED (same reference) when nothing matches, so
// the common case costs one scan and no allocation.
export function stripStagingGhosts(snap: Snapshot): Snapshot {
  const vs = snap.vehicles;
  if (!vs || !vs.some(isStagingGhost)) return snap;
  return { ...snap, vehicles: vs.filter((v) => !isStagingGhost(v)) };
}
