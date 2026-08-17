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

/** A measurement is a path, not a pair. One leg is the common case; a route
 *  walked around a hill is several, and the total is what you actually want
 *  to know about it. */
export interface Ruler { points: WorldPoint[]; }

/** UE world units per metre. Squad is centimetres. */
export const CM_PER_METRE = 100;

/** Flat map distance in metres. */
export function metresBetween(a: WorldPoint, b: WorldPoint): number {
  return Math.hypot(b.x - a.x, b.y - a.y) / CM_PER_METRE;
}

/** Total length of a path, in metres. */
export function pathMetres(points: readonly WorldPoint[]): number {
  let m = 0;
  for (let i = 1; i < points.length; i++) m += metresBetween(points[i - 1]!, points[i]!);
  return m;
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

/** Compass label for a leg: "847 m · 112°". */
export function legLabel(a: WorldPoint, b: WorldPoint): string {
  return `${formatMetres(metresBetween(a, b))} · `
    + `${Math.round(bearingDegrees(a, b)) % 360}°`;
}


/** The path, its end caps, and the readings — drawn over everything else. */
export function drawRuler(ctx: CanvasRenderingContext2D, view: ViewState,
                          cs: Size, r: Ruler): void {
  const pts = r.points;
  if (pts.length < 2) return;
  const dpr = cs.dpr;
  const scr = pts.map((p) => worldToScreen(view, cs as never, p.x, p.y));

  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  // A dark halo under a bright line: the map underneath is sand in one place
  // and forest in another, and a single-colour line disappears into one of
  // them. Same treatment the lane graph and the direction arrows use.
  const trace = () => {
    ctx.beginPath();
    ctx.moveTo(scr[0]![0], scr[0]![1]);
    for (let i = 1; i < scr.length; i++) ctx.lineTo(scr[i]![0], scr[i]![1]);
  };
  trace();
  ctx.strokeStyle = "rgba(0,0,0,0.55)";
  ctx.lineWidth = 4 * dpr;
  ctx.stroke();
  trace();
  ctx.strokeStyle = "#ffd166";
  ctx.lineWidth = 1.6 * dpr;
  ctx.stroke();

  for (const [x, y] of scr) {
    ctx.beginPath();
    ctx.arc(x, y, 3.2 * dpr, 0, Math.PI * 2);
    ctx.fillStyle = "#ffd166";
    ctx.fill();
    ctx.lineWidth = 1.4 * dpr;
    ctx.strokeStyle = "rgba(0,0,0,0.7)";
    ctx.stroke();
  }

  const fontPx = Math.max(11, Math.round(12 * dpr));
  ctx.font = `bold ${fontPx}px system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  const chip = (text: string, x: number, y: number, bright: boolean) => {
    const w = ctx.measureText(text).width + 10 * dpr;
    const h = fontPx + 8 * dpr;
    ctx.fillStyle = bright ? "rgba(0,0,0,0.78)" : "rgba(0,0,0,0.6)";
    ctx.beginPath();
    if ((ctx as CanvasRenderingContext2D & { roundRect?: unknown }).roundRect) {
      ctx.roundRect(x - w / 2, y - h / 2, w, h, 4 * dpr);
    } else {
      ctx.rect(x - w / 2, y - h / 2, w, h);
    }
    ctx.fill();
    ctx.fillStyle = bright ? "#ffd166" : "rgba(255,209,102,0.85)";
    ctx.fillText(text, x, y);
  };

  // Every leg gets its own reading, offset off the line so the number never
  // sits on top of what it measures — unreadable exactly while you drag.
  const multi = pts.length > 2;
  for (let i = 1; i < pts.length; i++) {
    const [ax, ay] = scr[i - 1]!;
    const [bx, by] = scr[i]!;
    const h = fontPx + 8 * dpr;
    chip(legLabel(pts[i - 1]!, pts[i]!), (ax + bx) / 2, (ay + by) / 2 - h,
         !multi);
  }
  // With more than one leg the total is the answer being looked for, so it
  // sits at the end of the path in full brightness.
  if (multi) {
    const [ex, ey] = scr[scr.length - 1]!;
    chip(`∑ ${formatMetres(pathMetres(pts))}`, ex, ey - (fontPx + 20 * dpr),
         true);
  }
  ctx.restore();
}
