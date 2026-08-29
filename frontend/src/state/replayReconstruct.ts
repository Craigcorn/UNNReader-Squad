// Two-tier replay reconstruction (load-time).
//
// A two-tier .sqrx interleaves ~1 Hz full rich Snapshots with 4 Hz compact
// `{"t":"pos",...}` position frames. This module folds them back into a single
// increasing `Snapshot[]` at load time: each position frame becomes a full
// Snapshot by carrying the *last full frame's discrete state forward by
// reference* and splicing in the fresh positions/health. The existing render
// path (MapCanvas frame interpolation) then plays it as smooth 4 Hz movement
// with zero changes — all the work is here.
//
// Heap: only `players` / `vehicles` / `projectiles` get new arrays (with
// shallow-cloned entries for the ones that moved); every other array
// (markers, deployables, zones, …) is shared by reference with the base
// full frame, so a position frame costs ~its moved entities, not a
// full-snapshot clone.

import {
  isGuidedProjectile,
  LAUNCHER_MAX_SQ,
  TRAIL_SPACING_SQ,
} from "./guided";
import type {
  PositionFrame,
  PositionPlayer,
  PositionProjectile,
  PositionVehicle,
  Snapshot,
} from "./types";

// A parsed .sqrx line is either a full Snapshot or a compact position frame.
export type RecordingLine = Snapshot | PositionFrame;

export function isPositionFrame(line: RecordingLine): line is PositionFrame {
  return (line as PositionFrame).t === "pos";
}

// The identity a position frame's `id` matches against — must mirror the
// agent-side key (possample.py: eosId else name) and interpolation.ts.
function playerKey(p: { eosId: string | null; name: string | null }): string | null {
  return p.eosId ?? p.name;
}

// Build one full Snapshot from a position frame + the last full frame.
export function reconstructFromPosition(
  base: Snapshot,
  pos: PositionFrame,
): Snapshot {
  const pByKey = new Map<string, PositionPlayer>();
  for (const p of pos.players) pByKey.set(p.id, p);
  const vById = new Map<string, PositionVehicle>();
  for (const v of pos.vehicles) vById.set(v.id, v);

  // Splice fresh positions onto the base roster. A player/vehicle absent from
  // the position frame (dead / not sampled this slot) carries its last full
  // position — no blink; the next full frame (<=1 s) corrects the roster.
  const players = base.players.map((p) => {
    const key = playerKey(p);
    const u = key != null ? pByKey.get(key) : undefined;
    if (!u || !p.soldier) return p;
    return {
      ...p,
      soldier: {
        ...p.soldier,
        position: { x: u.x, y: u.y, z: u.z ?? p.soldier.position?.z ?? null },
        health: u.h ?? p.soldier.health,
        yaw: u.yaw ?? p.soldier.yaw,
      },
    };
  });

  const vehicles = base.vehicles.map((v) => {
    const u = vById.get(v.id);
    if (!u) return v;
    return {
      ...v,
      position: { x: u.x, y: u.y, z: v.position?.z ?? null },
      health: u.h ?? v.health,
      yaw: u.yaw ?? v.yaw,
      team: u.team ?? v.team,
    };
  });

  // Projectiles joined the sampler later, so the key is optional: an old
  // recording without it shares the base array by reference exactly as
  // before, and its missiles move at full-frame cadence only.
  let projectiles = base.projectiles;
  if (pos.projectiles && base.projectiles?.length) {
    const prById = new Map<string, PositionProjectile>();
    for (const pr of pos.projectiles) prById.set(pr.id, pr);
    projectiles = base.projectiles.map((p) => {
      const u = prById.get(p.id);
      if (!u) return p;
      return {
        ...p,
        position: { x: u.x, y: u.y, z: u.z ?? p.position?.z ?? null },
      };
    });
  }

  return {
    ...base, // shares gameState/teams/squads/zones/markers/deployables/… by ref
    reconstructed: true,
    timestamp: pos.timestamp,
    // `fullTick`, never `tick`. A position frame carries TWO counters and they
    // are not the same number: `tick` is the 4 Hz sampler's own loop counter
    // and `fullTick` is the build the positions were spliced onto. The two
    // namespaces drift apart for as long as the service has been up — about a
    // thousand apart on the first real two-tier recording — so taking `tick`
    // here made the viewer's tick display seesaw between the sampler's count
    // on reconstructed frames and the builder's on full ones. The encoder was
    // always shipping the right value; this is which one to read.
    tick: pos.fullTick ?? base.tick,
    players,
    vehicles,
    projectiles,
    // Kills are per-tick deltas already delivered on the full frame that
    // precedes these position frames; re-emitting them here would double-count
    // in the kill feed. Empty is correct — no new kills in a position frame.
    damageEvents: [],
  };
}

// Stateful reducer that turns the raw line stream into the reconstructed
// Snapshot[]. Feed lines in order; a position frame before the first full
// frame is dropped (nothing to carry). Returns the produced snapshot (or null
// when the line was dropped) so callers can count progress.
export class ReplayReconstructor {
  private lastFull: Snapshot | null = null;

  push(line: RecordingLine): Snapshot | null {
    if (isPositionFrame(line)) {
      if (!this.lastFull) return null;
      return reconstructFromPosition(this.lastFull, line);
    }
    this.lastFull = line;
    return line;
  }
}

// Load-time projectile smoothing. Position frames carry players and
// vehicles but not projectiles (deliberately — on real 100-player
// matches 90-180 rounds fly at barrage peaks, and sampling them cost
// 2-7% of file size and out-read the whole player roster), so every
// reconstructed frame repeats its base full's projectile positions and
// the render lerp sees a zero-motion span at each one: a visible hitch
// at the position-frame cadence. This pass runs once after the whole
// timeline is loaded, when the NEXT full is finally known, and fills
// each reconstructed frame's projectiles by interpolating between the
// bracketing fulls on the frame's own timestamp — the same straight
// segment the renderer already draws between fulls, precomputed so the
// pair stream never stalls. Works identically on single-tier prod
// recordings (no reconstructed frames → no-op) and leaves untouched:
// entries a future format really sampled (they differ from the base
// full's by reference), and rounds absent from the next full (their
// last known position holds until the vanish debounce rules).
export function interpolateProjectilesBetweenFulls(
  frames: Snapshot[],
): void {
  let runStart = -1;   // index of the first reconstructed frame of a run
  let baseFull: Snapshot | null = null;
  for (let i = 0; i < frames.length; i++) {
    const f = frames[i]!;
    if (f.reconstructed) {
      if (baseFull && runStart < 0) runStart = i;
      continue;
    }
    if (runStart >= 0 && baseFull) {
      fillRun(frames, runStart, i - 1, baseFull, f);
    }
    runStart = -1;
    baseFull = f;
  }
}

// A guided missile's complete steering trail, precomputed per round at
// load time and keyed by tick — so the renderer draws "the recorded
// path up to the playhead" and a seek in either direction can never
// tear it: the trail is a pure function of recorded data plus the
// current frame, not of the order the viewer happened to visit frames
// in. The first point is the LAUNCHER when one can be joined honestly
// (the round names its firer; the launcher has that player in a seat
// within sight of the first sample). Points stop by themselves where
// the round dies — a frozen position never clears the spacing gate.
export interface ProjectileTimelinePoint { x: number; y: number; tick: number; }

export function buildProjectileTimelines(
  frames: Snapshot[],
): Map<string, ProjectileTimelinePoint[]> {
  const out = new Map<string, ProjectileTimelinePoint[]>();
  for (const f of frames) {
    const tick = f.tick ?? 0;
    for (const p of f.projectiles ?? []) {
      if (!p.position || !isGuidedProjectile(p)) continue;
      let tl = out.get(p.id);
      if (!tl) {
        tl = [];
        out.set(p.id, tl);
        if (p.firer) {
          for (const v of f.vehicles ?? []) {
            if (!v.position || !v.seats) continue;
            if (!v.seats.some((s) => s.occupantName === p.firer)) continue;
            const dx = v.position.x - p.position.x;
            const dy = v.position.y - p.position.y;
            if (dx * dx + dy * dy > LAUNCHER_MAX_SQ) continue;
            tl.push({ x: v.position.x, y: v.position.y, tick });
            break;
          }
        }
      }
      const last = tl[tl.length - 1];
      if (last) {
        const dx = p.position.x - last.x, dy = p.position.y - last.y;
        if (dx * dx + dy * dy < TRAIL_SPACING_SQ) continue;
      }
      tl.push({ x: p.position.x, y: p.position.y, tick });
    }
  }
  return out;
}

function fillRun(frames: Snapshot[], from: number, to: number,
                 a: Snapshot, b: Snapshot): void {
  const aPr = a.projectiles;
  if (!aPr || aPr.length === 0) return;
  const bById = new Map(
    (b.projectiles ?? []).map((p) => [p.id, p] as const));
  const ta = Date.parse(a.timestamp) || 0;
  const tb = Date.parse(b.timestamp) || 0;
  const span = tb - ta;
  if (!(span > 0)) return;
  for (let i = from; i <= to; i++) {
    const r = frames[i]!;
    const alpha = Math.max(0, Math.min(1,
      ((Date.parse(r.timestamp) || 0) - ta) / span));
    const own = r.projectiles;
    r.projectiles = aPr.map((p, k) => {
      // A genuinely sampled entry (spliced from a position frame that
      // carried projectiles) is a different object than the base's —
      // real data always wins over interpolation.
      const existing = own && own[k];
      if (existing && existing !== p) return existing;
      const bp = bById.get(p.id);
      if (!bp || !p.position || !bp.position) return p;
      const t = alpha;
      return {
        ...p,
        position: {
          x: p.position.x + (bp.position.x - p.position.x) * t,
          y: p.position.y + (bp.position.y - p.position.y) * t,
          z: p.position.z != null && bp.position.z != null
            ? p.position.z + (bp.position.z - p.position.z) * t
            : p.position.z ?? null,
        },
      };
    });
  }
}
