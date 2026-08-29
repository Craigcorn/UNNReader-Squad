// Mortar / rocket / guided-missile projectile rendering with cross-tick
// tracking, directional icon orientation, steering trails, and an impact
// ring animation. Ported from the legacy vanilla-JS mortar-rounds module;
// adapted to our React canvas where MapCanvas already runs a continuous
// rAF loop (no need for the old requestDraw() pump — rings animate
// naturally).
//
// Tracking strategy in priority order:
//   1. projectile.id (actor pointer) — stable while the actor lives,
//      so the cheapest sig match for matching this-tick to last-tick.
//   2. Nearest-neighbour within MATCH_RADIUS — covers id churn across
//      respawns / bucket boundary cases. Dead tracks are excluded so a
//      fresh round can never inherit a corpse's trail.
//   3. velocity vector (when backend Phase B+ emits it) — first-tick
//      heading derived directly, no second sample required.
//
// Steering trail: guided missiles (TOW / Kornet / HJ-8) are the one
// projectile family whose PATH is the story — the gunner steers them —
// so each guided track accumulates its recorded positions and draws them
// as an age-faded polyline in the firer's team colour. Ballistic rounds
// fly a fixed arc and get no trail (deliberate).
//
// Frozen-ghost rule (viewer side, for recordings already written): a
// wire-cut or self-destructed missile's actor lingers in server memory
// up to a minute, parked mid-air with hasImpacted still false — a live
// powered round moves every tick, so an explosive, unimpacted projectile
// frozen across two TICK-ADVANCING frames is treated as dead: impact
// ring at the true death point, icon off, trail left to fade. Counting
// tick advances matters: reconstructed 4 Hz frames share the base full
// frame's projectiles at the same tick and must not count. Resting smoke
// rounds are not explosive and are exempt.
//
// Impact ring spawn priority:
//   A. projectile.hasImpacted flag (reader emits the moment
//      ASQProjectile.bHasImpacted flips). Spawned at the exact
//      replicated impact position. Deduped via `impactSpawned` so the
//      same dying actor (which lingers a few ticks with hasImpacted
//      still true) doesn't stack rings on the same point.
//   B. Frozen-ghost detection (above).
//   C. Tracker-vanish heuristic. Anything we saw last tick but not
//      this tick → assume it impacted at its last known location.
//      Skipped when (A)/(B) already fired for the same signature.
//
// Replay seeks: tracker state accumulates in playback order, so a tick
// REGRESSION (rewind, restart) clears everything — trails rebuild as the
// replay plays forward, and the reset also suppresses the spurious rings
// the vanish heuristic used to fire on every backward seek.

import type { Projectile, Snapshot, ViewState } from "../state/types";
import { teamColor } from "./draw";
import { icon } from "./icons";
import { worldToScreen } from "./worldToScreen";

const MATCH_RADIUS_UE  = 250_000;                        // 2500 m
const MATCH_RADIUS_SQ  = MATCH_RADIUS_UE * MATCH_RADIUS_UE;
const IMPACT_MS        = 1200;                           // ring lifetime
const STALE_MS         = 12_000;                         // drop trackers older than this
const TRAIL_MS         = 12_000;                         // trail point fade-out age
const TRAIL_MAX_POINTS = 128;                            // per-track backstop
const FROZEN_DEAD_TICKS = 2;   // identical position across N tick advances

const MORTAR_ICON_URL  = "./icons/deployables/mortar_round.svg";

interface CanvasSize {
  width: number; height: number; cssWidth: number; cssHeight: number; dpr: number;
}

interface TrailPoint { x: number; y: number; t: number; }

interface Track {
  x: number;
  y: number;
  heading: number | null;   // screen-space radians, +x axis baseline
  lastSeenAt: number;       // wall-clock ms
  kind: string;
  team: number | null;
  path: TrailPoint[];       // guided rounds only; empty otherwise
  lastTick: number | null;  // snap.tick when last evaluated for freeze
  frozenTicks: number;      // consecutive tick-advances with identical pos
  dead: boolean;            // impacted / frozen / vanished — icon off
}

interface Impact {
  x: number;
  y: number;
  startAt: number;
  kind: string;
}

// Module-level tracker state. Shared across renderScene calls so
// position deltas + impact dedupe survive between snapshots. (Single
// renderer instance per page — MapCanvas — so a singleton is fine.)
const tracks         = new Map<string, Track>();
const impacts: Impact[] = [];
const impactSpawned  = new Map<string, number>();
let lastSnapTick: number | null = null;

// A guided missile is the backend-stamped kind; the class-name fallback
// covers recordings written before the stamp existed.
function isGuided(p: Projectile): boolean {
  if (p.kind === "guided") return true;
  return /TOW|KORNET|HJ-?8|ATGM|MILAN/i.test(p.classShort ?? "");
}

// Signature: prefer the stable actor id, fall back to a coarse class +
// position bucket so micro-drift between ticks doesn't generate two
// signatures for the same physical round.
function signature(p: Projectile): string {
  if (p.id) return `id:${p.id}`;
  const pos = p.position;
  if (pos) {
    const bx = Math.round(pos.x / 500);
    const by = Math.round(pos.y / 500);
    return `c:${p.classShort ?? ""}:${bx}:${by}`;
  }
  return `n:${p.classShort ?? ""}`;
}

function spawnRing(sig: string, x: number, y: number,
                   kind: string, now: number) {
  if (impactSpawned.has(sig)) return;
  impacts.push({ x, y, startAt: now, kind });
  impactSpawned.set(sig, now);
}

export function drawProjectilesAndImpacts(
  ctx: CanvasRenderingContext2D,
  snap: Snapshot,
  view: ViewState,
  cs: CanvasSize,
) {
  const now = Date.now();
  const tick = snap.tick ?? null;
  if (tick != null && lastSnapTick != null && tick < lastSnapTick) {
    // Rewind / restart: playback-order state is now from the future.
    tracks.clear();
    impacts.length = 0;
    impactSpawned.clear();
  }
  if (tick != null) lastSnapTick = tick;

  const seen = new Set<string>();
  const rounds = snap.projectiles ?? [];

  const img = icon(MORTAR_ICON_URL);
  const imgReady = img.complete && img.naturalWidth > 0;

  // ---- pass 1: update trackers (freeze detection, trails, rings) --------
  const drawable: { r: Projectile; heading: number | null }[] = [];
  for (const r of rounds) {
    if (!r.position) continue;
    const sig = signature(r);
    seen.add(sig);

    // Resolve previous track: sig hit first, then nearest-neighbour
    // within radius (covers id-changing edge cases). Never a dead one.
    let prev: Track | null = tracks.get(sig) ?? null;
    if (!prev) {
      let bestD2 = MATCH_RADIUS_SQ;
      let bestSig: string | null = null;
      for (const [psig, pt] of tracks) {
        if (seen.has(psig) || pt.dead) continue;
        const dx = r.position.x - pt.x, dy = r.position.y - pt.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < bestD2) { bestD2 = d2; bestSig = psig; prev = pt; }
      }
      if (bestSig) {
        tracks.delete(bestSig);
      }
    }

    // Heading: prefer screen-space delta from the previous sample
    // (handles map Y-flip correctly). Ignore micro-jitter (<1m).
    let heading = prev ? prev.heading : null;
    const movedSq = prev
      ? (r.position.x - prev.x) ** 2 + (r.position.y - prev.y) ** 2
      : Infinity;
    if (prev && movedSq > 10_000) {
      const [psx, psy] = worldToScreen(view, cs, prev.x, prev.y);
      const [csx, csy] = worldToScreen(view, cs, r.position.x, r.position.y);
      heading = Math.atan2(csy - psy, csx - psx);
    }
    // First-tick fallback when backend emits velocity (Phase B+).
    if (heading == null && r.velocity && (r.velocity.x || r.velocity.y)) {
      const [ox, oy] = worldToScreen(view, cs, r.position.x, r.position.y);
      const [tx, ty] = worldToScreen(view, cs,
        r.position.x + r.velocity.x,
        r.position.y + r.velocity.y);
      heading = Math.atan2(ty - oy, tx - ox);
    }

    const kind = r.kind ?? "mortar";
    const track: Track = prev ?? {
      x: r.position.x, y: r.position.y, heading, lastSeenAt: now, kind,
      team: r.team ?? null, path: [], lastTick: tick, frozenTicks: 0,
      dead: false,
    };
    tracks.set(sig, track);

    // Frozen-ghost detection — only when the tick actually advanced
    // (reconstructed 4 Hz frames repeat the base frame's projectiles at
    // the same tick and must not count).
    const advanced = tick != null
      && (track.lastTick == null || tick > track.lastTick);
    if (prev && advanced) {
      if (movedSq <= 10_000 && r.isExplosive && !r.hasImpacted) {
        track.frozenTicks += 1;
        if (track.frozenTicks >= FROZEN_DEAD_TICKS && !track.dead) {
          spawnRing(sig, r.position.x, r.position.y, kind, now);
          track.dead = true;
        }
      } else if (movedSq > 10_000) {
        track.frozenTicks = 0;
      }
    }
    if (advanced) track.lastTick = tick;

    // Trail — guided rounds only: their path is the story.
    if (!track.dead && isGuided(r)
        && (track.path.length === 0 || movedSq > 10_000)) {
      track.path.push({ x: r.position.x, y: r.position.y, t: now });
      if (track.path.length > TRAIL_MAX_POINTS) track.path.shift();
    }

    track.x = r.position.x;
    track.y = r.position.y;
    track.heading = heading;
    track.lastSeenAt = now;
    if (r.team != null) track.team = r.team;

    // Path A: reader said this projectile has impacted. Spawn the
    // burst now at the replicated impact position; the actor lingers a
    // couple of ticks with hasImpacted=true, impactSpawned dedupes.
    if (r.hasImpacted) {
      spawnRing(sig, r.position.x, r.position.y, kind, now);
      track.dead = true;
      continue;  // exploding, not flying — no icon
    }
    if (track.dead) continue;  // frozen ghost — trail fades, no icon
    drawable.push({ r, heading });
  }

  // ---- pass 2: vanish + GC ---------------------------------------------
  for (const [sig, pt] of Array.from(tracks)) {
    if (!seen.has(sig) && !pt.dead) {
      // Path C: gone from the snapshot → impacted at last known spot.
      spawnRing(sig, pt.x, pt.y, pt.kind, now);
      pt.dead = true;
    }
    // A dead track lives on until its trail has faded; a live-but-stale
    // one (recording gap) is dropped on the old schedule.
    const lastTrailT = pt.path.length
      ? pt.path[pt.path.length - 1]!.t : pt.lastSeenAt;
    if (pt.dead
        ? (now - lastTrailT > TRAIL_MS)
        : (!seen.has(sig) && now - pt.lastSeenAt > STALE_MS)) {
      tracks.delete(sig);
      impactSpawned.delete(sig);
    }
  }
  // Belt-and-suspenders GC of the dedupe map.
  for (const [sig, t] of Array.from(impactSpawned)) {
    if (now - t > Math.max(STALE_MS, TRAIL_MS)) impactSpawned.delete(sig);
  }

  // ---- pass 3: steering trails (under the icons) ------------------------
  for (const pt of tracks.values()) {
    if (pt.path.length < 2) continue;
    const col = teamColor(pt.team);
    ctx.save();
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.lineWidth = 2 * cs.dpr;
    ctx.strokeStyle = col;
    for (let i = 1; i < pt.path.length; i++) {
      const a = pt.path[i - 1]!, b = pt.path[i]!;
      const age = now - b.t;
      if (age > TRAIL_MS) continue;
      const [ax, ay] = worldToScreen(view, cs, a.x, a.y);
      const [bx, by] = worldToScreen(view, cs, b.x, b.y);
      ctx.globalAlpha = 0.75 * (1 - age / TRAIL_MS);
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.stroke();
    }
    ctx.restore();
  }

  // ---- pass 4: icons ----------------------------------------------------
  for (const { r, heading } of drawable) {
    const [sx, sy] = worldToScreen(view, cs, r.position!.x, r.position!.y);
    if (sx < -80 || sx > cs.width + 80 || sy < -80 || sy > cs.height + 80) {
      continue;  // offscreen; tracker keeps updating for heading carry
    }
    const kind = r.kind ?? "mortar";
    if (imgReady) {
      const isRocket = kind === "grad" || kind === "s5";
      const w = (isRocket ? 14 : 12) * cs.dpr;
      const h = (isRocket ? 30 : 26) * cs.dpr;
      ctx.save();
      ctx.globalAlpha = 0.9;
      ctx.translate(sx, sy);
      // Icon's native nose points -Y; rotate by heading + π/2 so the
      // tip aims along the travel direction in screen space.
      if (heading != null) ctx.rotate(heading + Math.PI / 2);
      ctx.drawImage(img, -w / 2, -h / 2, w, h);
      ctx.restore();
    } else {
      // Fallback dot while the SVG warms up.
      ctx.beginPath();
      ctx.arc(sx, sy, 3 * cs.dpr, 0, 2 * Math.PI);
      ctx.fillStyle = "#ffd166";
      ctx.fill();
    }
  }

  // ---- impact rings -----------------------------------------------------
  if (impacts.length) drawImpacts(ctx, view, cs, now);
}

function drawImpacts(
  ctx: CanvasRenderingContext2D,
  view: ViewState,
  cs: CanvasSize,
  now: number,
) {
  // Reap finished effects first.
  for (let i = impacts.length - 1; i >= 0; i--) {
    if (now - impacts[i]!.startAt > IMPACT_MS) impacts.splice(i, 1);
  }
  for (const e of impacts) {
    const t = Math.min(1, (now - e.startAt) / IMPACT_MS);  // 0..1
    const [sx, sy] = worldToScreen(view, cs, e.x, e.y);
    if (sx < -120 || sx > cs.width + 120
        || sy < -120 || sy > cs.height + 120) continue;
    const isRocket = e.kind === "grad" || e.kind === "s5";
    const maxR = (isRocket ? 32 : 22) * cs.dpr;
    const ringR = (4 * cs.dpr) + (maxR - 4 * cs.dpr) * t;
    // Fast fade-in (0..0.2) → slow fade-out (0.2..1).
    const fade = t < 0.2 ? (t / 0.2) : (1 - (t - 0.2) / 0.8);

    ctx.save();
    ctx.globalAlpha = fade * 0.85;
    ctx.strokeStyle = "#ff2418";
    ctx.lineWidth = 2.5 * cs.dpr;
    ctx.beginPath();
    ctx.arc(sx, sy, ringR, 0, 2 * Math.PI);
    ctx.stroke();
    if (t < 0.5) {
      ctx.globalAlpha = (1 - t * 2) * 0.7;
      ctx.fillStyle = "#ff5040";
      ctx.beginPath();
      ctx.arc(sx, sy, 3 * cs.dpr, 0, 2 * Math.PI);
      ctx.fill();
    }
    ctx.restore();
  }
}
