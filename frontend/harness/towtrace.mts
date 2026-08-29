// Headless replay trace: pump a real recording slice through the ACTUAL
// viewer modules (reconstruction, projectile smoothing, the canvas's
// lerp+graft, the projectile tracker) and log the TOW track's state at
// every simulated rAF, so a visual glitch can be diagnosed from state
// transitions instead of eyeballs. Not a test — a diagnostic tool; run:
//   node <bundled> <slice.ndjson>
/* eslint-disable no-console */

(globalThis as any).Image = class {
  complete = false;
  naturalWidth = 0;
  addEventListener() {}
  set src(_v: string) {}
};

import { readFileSync } from "node:fs";
import {
  buildProjectileTimelines,
  interpolateProjectilesBetweenFulls,
  ReplayReconstructor,
  type RecordingLine,
} from "../src/state/replayReconstruct.ts";
import { lerpSnap } from "../src/canvas/interpolation.ts";
import {
  drawProjectilesAndImpacts,
  setProjectileTimelines,
  _tracksForTest,
} from "../src/canvas/projectiles.ts";
import type { Snapshot, ViewState } from "../src/state/types.ts";

const slicePath = process.argv[2]!;
const lines = readFileSync(slicePath, "utf-8").split("\n").filter(Boolean);
const recon = new ReplayReconstructor();
const frames: Snapshot[] = [];
for (const l of lines) {
  const s = recon.push(JSON.parse(l) as RecordingLine);
  if (s) frames.push(s);
}
interpolateProjectilesBetweenFulls(frames);
const timelines = buildProjectileTimelines(frames);
setProjectileTimelines(timelines);
console.log(`frames: ${frames.length}, timelines: ${timelines.size}`);
for (const [id, tl] of timelines) {
  console.log(`  timeline ${id}: ${tl.length} points, `
    + `first (${Math.round(tl[0]!.x)},${Math.round(tl[0]!.y)}) `
    + `tick ${tl[0]!.tick}..${tl[tl.length - 1]!.tick}`);
}

const view: ViewState = {
  minX: -200000, minY: -200000, maxX: 200000, maxY: 200000,
  zoom: 1, panX: 0, panY: 0, userInteracted: false,
} as ViewState;
const cs = { width: 1000, height: 1000, cssWidth: 1000, cssHeight: 1000,
             dpr: 1 };
const ctx: any = new Proxy({}, {
  get: (_t, _p) => () => {},
  set: () => true,
});

const ts = (f: Snapshot) => Date.parse(f.timestamp) || 0;
const t0 = ts(frames[0]!);
const tEnd = ts(frames[frames.length - 1]!);
const wall0 = 1_000_000_000_000;
const realNow = Date.now.bind(Date);
let simWall = wall0;
Date.now = () => simWall;

const TOW = "id:0x72f3cf687c60";
const tow = timelines.get("0x72f3cf687c60");
let last = "";

function play(tFrom: number, tTo: number, label: string) {
  let idx = 0;
  for (let t = tFrom; t <= tTo; t += 40) {
    simWall += 40;
    while (idx + 1 < frames.length && ts(frames[idx + 1]!) <= t) idx++;
    const a = frames[idx]!;
    const b = frames[idx + 1];
    let display: Snapshot = a;
    if (b) {
      const ta = ts(a), tb = ts(b);
      const span = Math.max(1, tb - ta);
      const alpha = Math.max(0, Math.min(1, (t - ta) / span));
      if (alpha > 0) {
        const interp = lerpSnap(a, b, alpha, span);
        display = interp ? {
          ...a,
          players: interp.players,
          vehicles: interp.vehicles,
          projectiles: interp.projectiles,
          captureZones: interp.captureZones,
        } : a;
      }
    }
    drawProjectilesAndImpacts(ctx, display, view, cs);
    const tr: any = _tracksForTest.get(TOW);
    const dt = display.tick ?? 0;
    const tlLen = tow ? tow.filter((q) => q.tick <= dt).length : 0;
    const state = tr
      ? `trail=${tlLen} dead=${tr.dead} frozen=${tr.frozenTicks}`
      : "NO TRACK";
    if (state !== last) {
      console.log(
        `${label} t=+${((t - t0) / 1000).toFixed(2)}s ` +
        `tick=${display.tick} | ${state}`);
      last = state;
    }
  }
}

play(t0, t0 + 18_000, "linear ");
console.log("--- SEEK BACK to +14s ---");
last = "";
play(t0 + 14_000, t0 + 21_000, "reseek ");
Date.now = realNow;
console.log("done");
