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
