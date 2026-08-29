// What counts as a guided missile, and the shared geometry constants for
// its steering trail — used by both the load-time timeline builder
// (state/replayReconstruct) and the live renderer (canvas/projectiles),
// kept here so the two can never drift apart.

import type { Projectile } from "./types";

// Backend stamps kind "guided" from the class hierarchy; the class-name
// fallback covers recordings written before the stamp existed.
export function isGuidedProjectile(p: Projectile): boolean {
  if (p.kind === "guided") return true;
  return /TOW|KORNET|HJ-?8|ATGM|MILAN/i.test(p.classShort ?? "");
}

// Minimum spacing between trail points — keeps a whole flight inside a
// bounded buffer at any frame rate.
export const TRAIL_SPACING_UE = 2_500;                   // 25 m
export const TRAIL_SPACING_SQ = TRAIL_SPACING_UE * TRAIL_SPACING_UE;

// The launcher a trail is anchored to must be plausibly at the launch
// site — a seat-name match alone could pick up stale data.
export const LAUNCHER_MAX_UE = 50_000;                   // 500 m
export const LAUNCHER_MAX_SQ = LAUNCHER_MAX_UE * LAUNCHER_MAX_UE;
