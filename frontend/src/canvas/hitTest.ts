// Cursor → entity hit testing, in world coordinates so it scales with zoom.

import type { CaptureZone, CommandAction, Deployable, Drone, Marker, Player,
  Projectile, RallyPoint, Snapshot, Vec3, Vehicle,
  VehicleSpawner } from "../state/types";
import { dedupeMarkers } from "../state/commander/markers";
import { visibleCaps } from "./capVisibility";
import { isAdminCam } from "./draw";

export type HitType =
  | "player" | "vehicle" | "deployable" | "spawner"
  | "marker" | "projectile" | "capzone" | "rally"
  | "commandAction" | "drone";

export type HitEntity =
  | { type: "player"; e: Player }
  | { type: "vehicle"; e: Vehicle }
  | { type: "deployable"; e: Deployable }
  | { type: "spawner"; e: VehicleSpawner }
  | { type: "marker"; e: Marker }
  | { type: "projectile"; e: Projectile }
  | { type: "capzone"; e: CaptureZone }
  | { type: "rally"; e: RallyPoint }
  | { type: "commandAction"; e: CommandAction }
  | { type: "drone"; e: Drone };

export interface Hit { d2: number; hit: HitEntity; }

export function hitTest(snap: Snapshot | null, wx: number, wy: number,
                        worldRadius: number): Hit | null {
  if (!snap) return null;
  const r2 = worldRadius * worldRadius;
  const czR2 = (worldRadius * 3) * (worldRadius * 3);
  let best: Hit | null = null;

  const consider = (hit: HitEntity, pos: Vec3 | null | undefined, hitR2: number) => {
    if (!pos) return;
    const dx = pos.x - wx, dy = pos.y - wy;
    const d2 = dx * dx + dy * dy;
    if (d2 > hitR2) return;
    if (best === null || d2 < best.d2) best = { d2, hit };
  };

  for (const p of snap.players ?? []) {
    // Skip mounted soldiers — the vehicle they're inside should win the hover.
    if (!p.soldier || p.soldier.stale || p.soldier.attached) continue;
    // The admin free-cam is not drawn, so it must not be clickable either —
    // an invisible hover target is worse than a visible one.
    if (isAdminCam(p.soldier)) continue;
    consider({ type: "player", e: p }, p.soldier.position, r2);
  }
  for (const pr of snap.projectiles ?? []) consider({ type: "projectile", e: pr }, pr.position, r2);
  // The de-duped list, not the raw one: a marker the map merged away is not
  // drawn, and an invisible hover target is worse than a visible one.
  for (const m of dedupeMarkers(snap.markers)) consider({ type: "marker", e: m }, m.position, r2);
  for (const a of snap.commandActions ?? []) {
    // The drone's call actor is not on the map — its position reads (0, 0, z)
    // and means nothing — so it must not be clickable either.
    if (a.position && (a.position.x !== 0 || a.position.y !== 0))
      consider({ type: "commandAction", e: a }, a.position, r2);
  }
  for (const d of snap.drones ?? []) consider({ type: "drone", e: d }, d.position, r2);
  // Emplacement guns are not drawn (the deployable badge is the ONE map
  // element for an emplacement), so they must not capture hovers either.
  // Instead an armed deployable's hit IS its gun: tooltip and click then
  // surface crew, ammo and the joined health with no routing downstream.
  let gunByDep: Map<string, Vehicle> | null = null;
  for (const v of snap.vehicles ?? []) {
    if (v.owningDeployable) (gunByDep ??= new Map()).set(v.owningDeployable, v);
  }
  for (const v of snap.vehicles ?? []) {
    if (v.owningDeployable) continue;
    consider({ type: "vehicle", e: v }, v.position, r2 * 1.5);
  }
  for (const d of snap.deployables ?? []) {
    const gun = gunByDep?.get(d.id);
    if (gun) consider({ type: "vehicle", e: gun }, d.position, r2);
    else consider({ type: "deployable", e: d }, d.position, r2);
  }
  for (const sp of snap.vehicleSpawners ?? []) consider({ type: "spawner", e: sp }, sp.position, r2);
  for (const rp of snap.rallyPoints ?? [])  consider({ type: "rally", e: rp }, rp.position, r2);
  // Only hover caps that are actually drawn (hidden pre-roll cloud excluded).
  for (const cz of visibleCaps(snap.captureZones ?? [])) consider({ type: "capzone", e: cz }, cz.position, czR2);
  return best;
}
