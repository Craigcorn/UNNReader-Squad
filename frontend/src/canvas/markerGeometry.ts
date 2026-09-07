// What shape a marker is, and where a dragged one ends.
//
// Kept apart from the icon lookup because these are not icon questions: a POI
// is a diamond, and a Direction marker is a stroke the SL drags across the map
// whose length and heading the game records. When the viewer had neither it
// fell back to the generic infantry glyph, so an order spanning half the map
// was drawn as one soldier standing where the drag began.
import type { Marker } from "../state/types";

export type MarkerShape = "diamond" | "arrow" | "frontline" | "point";

export function markerShape(m: Marker): MarkerShape | null {
  const t = (m.type ?? "").toLowerCase();
  if (t.includes("poi")) return "diamond";
  const dragged = t.includes("frontline") || t.includes("director")
    || t.includes("orderline");
  if (!dragged) return null;
  // Drawn from the geometry it CARRIES, not from its name. Every replay
  // recorded before the agent read that geometry has none, and no direction
  // can be recovered from those — so they get a plain point. Visible and
  // honest, rather than an arrow aimed at a guess.
  if (!((m.arrowLength ?? 0) > 1)) return "point";
  return t.includes("frontline") ? "frontline" : "arrow";
}

// ---- command-asset footprints ---------------------------------------------
//
// A commander's asset leaves a marker whose class says what SHAPE it is and
// whose `distance` / `addDistance` / `yaw` say how big and which way. The
// four shapes and the fields each reads are spec §9, "Asset shapes"; the
// figures observed for each are in §5 (a UAV's chosen coverage radius, the
// static barrage's 15000 with a 7500 outer band, the mortar's fixed 7500
// with 4500, a strike run's chosen 6000, the creep's 45000 path with 7500 of
// drop scatter, an aim line's 4475–12000 separation).
//
// Everything is world centimetres, so it projects like any other position
// and stays pinned to the terrain at every zoom.

export type CommandFootprint =
  /** A coverage or barrage circle. `band` is the outer ring beyond
   *  `radius`, and 0 where the marker's `addDistance` is. */
  | { kind: "circle"; x: number; y: number; radius: number; band: number }
  /** A strike run: a line of `distance` along `yaw`. */
  | { kind: "run"; x: number; y: number; endX: number; endY: number }
  /** A creeping barrage: the same line, with the drop scatter either side. */
  | { kind: "path"; x: number; y: number; endX: number; endY: number;
      scatter: number }
  /** A precision strike: two aim points, at 0 and `distance` along `yaw`. */
  | { kind: "aimPoints"; points: { x: number; y: number }[] };

/** The footprint a marker draws, or `null` when it draws none.
 *
 *  `null` covers every marker outside the Command family, and every one
 *  inside it whose geometry did not reach the file — a recording made before
 *  2026-09-07, a class that does not declare the name, a run with a length
 *  but no bearing. No shape is invented for those: they keep their icon. */
export function commandFootprint(m: Marker): CommandFootprint | null {
  const t = (m.type ?? "").toLowerCase();
  if (!t.includes("command")) return null;
  const dist = m.distance;
  if (dist == null || !(dist > 0) || !m.position) return null;
  const add = m.addDistance ?? 0;
  const { x, y } = m.position;
  // LineRadius before Line and Radius: its name contains both.
  if (t.includes("commandlineradius")) {
    const end = along(x, y, dist, m.yaw);
    return end ? { kind: "aimPoints", points: [{ x, y }, end] } : null;
  }
  if (t.includes("commandradius")) {
    return { kind: "circle", x, y, radius: dist, band: add > 0 ? add : 0 };
  }
  if (t.includes("commandpath")) {
    const end = along(x, y, dist, m.yaw);
    return end
      ? { kind: "path", x, y, endX: end.x, endY: end.y, scatter: add }
      : null;
  }
  if (t.includes("commandline")) {
    const end = along(x, y, dist, m.yaw);
    return end ? { kind: "run", x, y, endX: end.x, endY: end.y } : null;
  }
  return null;
}

/** `len` centimetres along `yaw` from (x, y), in world space. `null` without
 *  a usable bearing — a length with no direction is not half a shape. */
function along(
  x: number, y: number, len: number, yaw: number | null | undefined,
): { x: number; y: number } | null {
  if (yaw == null || !Number.isFinite(yaw)) return null;
  const rad = (yaw * Math.PI) / 180;
  return { x: x + Math.cos(rad) * len, y: y + Math.sin(rad) * len };
}

/** Where a dragged marker ends, in WORLD coordinates.
 *
 *  Computed in world space and projected like any other position, so the
 *  arrow stays anchored to the terrain at every zoom level. */
export function arrowEnd(m: Marker): { x: number; y: number } | null {
  const len = m.arrowLength ?? 0;
  const hdg = m.arrowHeading;
  if (!(len > 1) || hdg == null || !Number.isFinite(hdg) || !m.position) {
    return null;
  }
  const rad = (hdg * Math.PI) / 180;
  return {
    x: m.position.x + Math.cos(rad) * len,
    y: m.position.y + Math.sin(rad) * len,
  };
}
