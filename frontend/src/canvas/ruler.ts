// Measuring a distance on the map.
//
// Squad's world unit is the centimetre, so a distance is only ever a division
// away — the reason this is its own module is not the arithmetic, it is that
// the number has to be honest about what it measures. This is the flat, map
// distance between two points: what a player reads off the map, and what a
// keypad grid is drawn in. It deliberately ignores height, because two points
// either side of a valley are not "closer" for being at the same altitude, and
// nobody paces a hill in three dimensions.
import type { ViewState } from "../state/types";
import { worldToScreen } from "./worldToScreen";

export interface WorldPoint { x: number; y: number; }
export interface Ruler { a: WorldPoint; b: WorldPoint; }

/** UE world units per metre. Squad is centimetres. */
export const CM_PER_METRE = 100;

/** Flat map distance in metres. */
export function metresBetween(a: WorldPoint, b: WorldPoint): number {
  return Math.hypot(b.x - a.x, b.y - a.y) / CM_PER_METRE;
}

/**
 * The label for a measurement.
 *
 * Always metres, never kilometres: Squad's grid is 300 m keypads and players
 * call ranges out in metres, so "1 240 m" is read instantly where "1.24 km"
 * has to be converted in the head first. A plain space groups the thousands:
 * a comma or a dot would mean different things to a Turkish and an English
 * reader, and a typographic thin space is not in every canvas font.
 */
export function formatMetres(m: number): string {
  const n = Math.round(m);
  return `${n.toLocaleString("en-US").replace(/,/g, " ")} m`;
}

/** Bearing in degrees, 0 = north, clockwise — the compass a player reads. */
export function bearingDegrees(a: WorldPoint, b: WorldPoint): number {
  // Screen/world +Y runs south on this map, so north is -Y. atan2(dx, -dy)
  // puts 0 at north and increases clockwise, which is what a compass does.
  const d = (Math.atan2(b.x - a.x, -(b.y - a.y)) * 180) / Math.PI;
  return (d + 360) % 360;
}

interface Size { width: number; height: number; dpr: number; }

/** The line, its end caps, and the reading — drawn over everything else. */
export function drawRuler(ctx: CanvasRenderingContext2D, view: ViewState,
                          cs: Size, r: Ruler): void {
  const [ax, ay] = worldToScreen(view, cs as never, r.a.x, r.a.y);
  const [bx, by] = worldToScreen(view, cs as never, r.b.x, r.b.y);
  const dpr = cs.dpr;

  ctx.save();
  // A dark halo under a bright line: the map underneath is sand in one place
  // and forest in another, and a single-colour line disappears into one of
  // them. Same treatment the lane graph and the direction arrows use.
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(ax, ay);
  ctx.lineTo(bx, by);
  ctx.strokeStyle = "rgba(0,0,0,0.55)";
  ctx.lineWidth = 4 * dpr;
  ctx.stroke();
  ctx.strokeStyle = "#ffd166";
  ctx.lineWidth = 1.6 * dpr;
  ctx.stroke();

  for (const [x, y] of [[ax, ay], [bx, by]] as const) {
    ctx.beginPath();
    ctx.arc(x, y, 3.2 * dpr, 0, Math.PI * 2);
    ctx.fillStyle = "#ffd166";
    ctx.fill();
    ctx.lineWidth = 1.4 * dpr;
    ctx.strokeStyle = "rgba(0,0,0,0.7)";
    ctx.stroke();
  }

  const label = formatMetres(metresBetween(r.a, r.b));
  const fontPx = Math.max(11, Math.round(12 * dpr));
  ctx.font = `bold ${fontPx}px system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const mx = (ax + bx) / 2;
  const my = (ay + by) / 2;
  const w = ctx.measureText(label).width + 10 * dpr;
  const h = fontPx + 8 * dpr;
  ctx.fillStyle = "rgba(0,0,0,0.72)";
  ctx.beginPath();
  // Kept off the line itself so the number never sits on top of what it
  // measures, which is unreadable at the moment you are dragging.
  const ly = my - h;
  if ((ctx as CanvasRenderingContext2D & { roundRect?: unknown }).roundRect) {
    ctx.roundRect(mx - w / 2, ly - h / 2, w, h, 4 * dpr);
  } else {
    ctx.rect(mx - w / 2, ly - h / 2, w, h);
  }
  ctx.fill();
  ctx.fillStyle = "#ffd166";
  ctx.fillText(label, mx, ly);
  ctx.restore();
}
