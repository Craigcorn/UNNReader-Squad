// Mortar / rocket / guided-missile projectile rendering with cross-tick
// tracking, render-behind motion smoothing, steering trails, and an
// impact ring animation. Ported from the legacy vanilla-JS mortar-rounds
// module; adapted to our React canvas where MapCanvas already runs a
// continuous rAF loop (no need for the old requestDraw() pump — rings
// animate naturally).
//
// Tracking strategy in priority order:
//   1. projectile.id (actor pointer) — stable while the actor lives,
//      so the cheapest sig match for matching this-tick to last-tick.
//   2. Nearest-neighbour within MATCH_RADIUS — ONLY for id-less rounds
//      whose position-bucket sig drifted, only against id-less tracks
//      of the same class, and never a dead track. An id-bearing
//      projectile is a distinct actor and must never steal another
//      round's tracker (it did once: see the guard at the match site).
//   3. velocity vector (when backend Phase B+ emits it) — first-tick
//      heading derived directly, no second sample required.
//
// Motion smoothing renders ONE SAMPLE BEHIND, gliding from the previous
// raw sample to the newest over a smoothed arrival interval — the same
// render-behind philosophy the live path uses. It replaced two designs
// that failed in turn: the frame-pair lerp in lerpSnap (two-tier
// reconstructed frames repeat the base frame's projectiles by
// reference, so it glided one span in four and froze the rest) and a
// forward dead-reckoner (its velocity came from wall-clock arrival
// gaps, and replay frames arrive with jitter — a near-zero gap made the
// velocity astronomical and flung the icon offscreen, which read as the
// missile vanishing). Interpolating between two REAL samples can never
// overshoot recorded data, is immune to arrival jitter, and costs only
// ~one sample (~300 ms) of display lag a replay cannot feel.
//
// Steering trail: guided missiles (TOW / Kornet / HJ-8) are the one
// projectile family whose PATH is the story — the gunner steers them —
// so each guided track accumulates its recorded positions and draws the
// WHOLE flight, launch to warhead, as a polyline in the firer's team
// colour for as long as the missile lives; when it dies the complete
// trail fades out as one. Ballistic rounds fly a fixed arc and get no
// trail (deliberate).
//
// Frozen-ghost rule (viewer side, for recordings already written): a
// wire-cut or self-destructed missile's actor lingers in server memory
// up to a minute, parked mid-air with hasImpacted still false — a live
// powered round moves every tick, so an explosive, unimpacted projectile
// frozen across two TICK-ADVANCING frames is treated as dead: impact
// ring at the true death point, icon off, trail fades. Counting tick
// advances matters: reconstructed 4 Hz frames share the base full
// frame's projectiles at the same tick and must not count. Resting smoke
// rounds are not explosive and are exempt. A dead track is PINNED while
// its actor is still in the snapshot — old recordings carry the ghost
// records, and GC'ing the dead track while they stream would resurrect
// the icon and ring in a cycle.
//
// Impact ring spawn priority:
//   A. projectile.hasImpacted flag (reader emits the moment
//      ASQProjectile.bHasImpacted flips). Spawned at the exact
//      replicated impact position. Deduped via `impactSpawned` so the
//      same dying actor (which lingers a few ticks with hasImpacted
//      still true) doesn't stack rings on the same point.
//   B. Frozen-ghost detection (above).
//   C. Tracker-vanish, DEBOUNCED: absent across two tick-advancing
//      frames → impacted at the last known location. One absent frame
//      is not death — a dropped or omitted record must not ring.
//
// Replay seeks: tracker state accumulates in playback order, so a tick
// REGRESSION (rewind, restart) clears everything — trails rebuild as the
// replay plays forward, and the reset also suppresses the spurious rings
// the vanish heuristic used to fire on every backward seek. A forward
// seek shows up as an impossible between-sample jump and resets that
// track's trail and motion instead of drawing a line across the map.

import type { Projectile, Snapshot, ViewState } from "../state/types";
import { teamColor } from "./draw";
import { icon } from "./icons";
import { worldToScreen } from "./worldToScreen";

// Nearest-neighbour rebinding radius — for ID-LESS rounds only, whose
// position-bucket signature drifts as they fly. 150 m covers a bucket
// hop plus one tick of the fastest round; the original 2500 m spanned
// half the map and let a freshly spawned mortar round STEAL an airborne
// TOW's tracker (see the guard at the match site).
const MATCH_RADIUS_UE  = 15_000;                         // 150 m
const MATCH_RADIUS_SQ  = MATCH_RADIUS_UE * MATCH_RADIUS_UE;
const IMPACT_MS        = 1200;                           // ring lifetime
const STALE_MS         = 12_000;                         // silently drop unseen live tracks
const DEATH_FADE_MS    = 2_500;                          // whole-trail fade after death
const TRAIL_MAX_POINTS = 256;                            // per-track backstop
const FROZEN_DEAD_TICKS = 2;   // identical position across N tick advances
const VANISH_DEAD_TICKS = 2;   // absent across N tick advances
// Sample arrival pacing: EMA'd, clamped to sane bounds so one weird
// gap (rAF hiccup, playback catch-up burst) cannot warp the glide.
const GAP_MIN_MS = 60;
const GAP_MAX_MS = 1_500;
// A between-sample jump beyond this is a seek/teleport, not flight
// (fastest tracked round ~300 m/s × ~1.5 s of gap, with headroom).
const SAMPLE_JUMP_SQ   = 60_000 * 60_000;                // 600 m

const MORTAR_ICON_URL  = "./icons/deployables/mortar_round.svg";

interface CanvasSize {
  width: number; height: number; cssWidth: number; cssHeight: number; dpr: number;
}

interface TrailPoint { x: number; y: number; }

interface Track {
  cls: string | null;       // classShort — NN may only pair like classes
  x: number;                // newest RAW sample (world units)
  y: number;
  px: number;               // previous RAW sample — the glide's start
  py: number;
  sampleAt: number;         // wall-clock ms the newest sample arrived
  gapMs: number;            // EMA of sample arrival intervals
  heading: number | null;   // screen-space radians, +x axis baseline
  lastSeenAt: number;       // wall-clock ms
  lastSeenTick: number | null;  // snap.tick when last present
  kind: string;
  team: number | null;
  path: TrailPoint[];       // guided rounds only; empty otherwise
  lastTick: number | null;  // snap.tick when last evaluated for freeze
  frozenTicks: number;      // consecutive tick-advances with identical pos
  dead: boolean;            // impacted / frozen / vanished — icon off
  diedAt: number;           // wall-clock ms dead was set (0 while alive)
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

function markDead(sig: string, t: Track, x: number, y: number, now: number) {
  spawnRing(sig, x, y, t.kind, now);
  if (!t.dead) {
    t.dead = true;
    t.diedAt = now;
  }
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
  const drawable: { r: Projectile; track: Track }[] = [];
  for (const r of rounds) {
    if (!r.position) continue;
    const sig = signature(r);
    seen.add(sig);

    // Resolve previous track: sig hit first. The nearest-neighbour
    // fallback runs ONLY for id-less rounds whose position-bucket sig
    // drifted, and only against tracks that are themselves id-less and
    // of the same class. An id-bearing projectile is a DISTINCT actor:
    // when this fallback could cross that line, six mortar rounds
    // spawning beside one airborne TOW each hijacked its tracker in
    // turn — deleting it, resetting trail, pacing and heading — which
    // reached the user as flicker, jitter, and a trail that restarted
    // mid-flight. Never a dead one either.
    let prev: Track | null = tracks.get(sig) ?? null;
    if (!prev && !r.id) {
      let bestD2 = MATCH_RADIUS_SQ;
      let bestSig: string | null = null;
      for (const [psig, pt] of tracks) {
        if (seen.has(psig) || pt.dead) continue;
        if (psig.startsWith("id:")) continue;
        if (pt.cls !== (r.classShort ?? null)) continue;
        const dx = r.position.x - pt.x, dy = r.position.y - pt.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < bestD2) { bestD2 = d2; bestSig = psig; prev = pt; }
      }
      if (bestSig) {
        tracks.delete(bestSig);
      }
    }

    const kind = r.kind ?? "mortar";
    const track: Track = prev ?? {
      cls: r.classShort ?? null,
      x: r.position.x, y: r.position.y,
      px: r.position.x, py: r.position.y,
      sampleAt: now, gapMs: 0,
      heading: null, lastSeenAt: now, lastSeenTick: tick, kind,
      team: r.team ?? null, path: [], lastTick: tick, frozenTicks: 0,
      dead: false, diedAt: 0,
    };
    tracks.set(sig, track);

    const dxU = r.position.x - track.x;
    const dyU = r.position.y - track.y;
    const movedSq = prev ? dxU * dxU + dyU * dyU : Infinity;

    // A fresh RAW sample (ignore <1 m jitter). The previous sample
    // becomes the glide's start; the arrival interval feeds a clamped
    // EMA so one weird gap cannot warp the pacing. An impossible jump
    // is a seek, not flight — restart the track there.
    if (prev && movedSq > 10_000) {
      if (movedSq > SAMPLE_JUMP_SQ) {
        track.px = r.position.x;
        track.py = r.position.y;
        track.gapMs = 0;
        track.path.length = 0;
        track.heading = null;
      } else {
        track.px = track.x;
        track.py = track.y;
        const gap = Math.min(GAP_MAX_MS,
          Math.max(GAP_MIN_MS, now - track.sampleAt));
        track.gapMs = track.gapMs > 0
          ? track.gapMs * 0.6 + gap * 0.4 : gap;
        const [psx, psy] = worldToScreen(view, cs, track.x, track.y);
        const [csx, csy] = worldToScreen(view, cs,
          r.position.x, r.position.y);
        track.heading = Math.atan2(csy - psy, csx - psx);
      }
      track.x = r.position.x;
      track.y = r.position.y;
      track.sampleAt = now;
    }
    // First-tick fallback when backend emits velocity (Phase B+).
    if (track.heading == null && r.velocity
        && (r.velocity.x || r.velocity.y)) {
      const [ox, oy] = worldToScreen(view, cs, r.position.x, r.position.y);
      const [tx, ty] = worldToScreen(view, cs,
        r.position.x + r.velocity.x,
        r.position.y + r.velocity.y);
      track.heading = Math.atan2(ty - oy, tx - ox);
    }

    // Frozen-ghost detection — only when the tick actually advanced
    // (reconstructed 4 Hz frames repeat the base frame's projectiles at
    // the same tick and must not count).
    const advanced = tick != null
      && (track.lastTick == null || tick > track.lastTick);
    if (prev && advanced) {
      if (movedSq <= 10_000 && r.isExplosive && !r.hasImpacted) {
        track.frozenTicks += 1;
        if (track.frozenTicks >= FROZEN_DEAD_TICKS) {
          markDead(sig, track, r.position.x, r.position.y, now);
        }
      } else if (movedSq > 10_000) {
        track.frozenTicks = 0;
      }
    }
    if (advanced) track.lastTick = tick;
    if (tick != null) track.lastSeenTick = tick;

    // Trail — guided rounds only: their path is the story, drawn whole
    // from launch until the missile dies.
    if (!track.dead && isGuided(r)
        && (track.path.length === 0 || movedSq > 10_000)) {
      track.path.push({ x: r.position.x, y: r.position.y });
      if (track.path.length > TRAIL_MAX_POINTS) track.path.shift();
    }

    track.lastSeenAt = now;
    if (r.team != null) track.team = r.team;

    // Path A: reader said this projectile has impacted. Spawn the
    // burst now at the replicated impact position; the actor lingers a
    // couple of ticks with hasImpacted=true, impactSpawned dedupes.
    if (r.hasImpacted) {
      markDead(sig, track, r.position.x, r.position.y, now);
      continue;  // exploding, not flying — no icon
    }
    if (track.dead) continue;  // frozen ghost — trail fades, no icon
    drawable.push({ r, track });
  }

  // ---- pass 2: vanish + GC ---------------------------------------------
  for (const [sig, pt] of Array.from(tracks)) {
    if (!seen.has(sig) && !pt.dead) {
      // Path C, debounced: only absence across VANISH_DEAD_TICKS tick
      // advances is death — one absent frame (a dropped or omitted
      // record) must not ring.
      const advancesUnseen = (tick != null && pt.lastSeenTick != null)
        ? tick - pt.lastSeenTick : 0;
      if (advancesUnseen >= VANISH_DEAD_TICKS) {
        markDead(sig, pt, pt.x, pt.y, now);
      } else if (now - pt.lastSeenAt > STALE_MS) {
        // Stale without ever dying visibly (recording gap) — no ring.
        tracks.delete(sig);
        impactSpawned.delete(sig);
        continue;
      }
    }
    // A dead track is PINNED while its actor is still in the snapshot
    // (ghost records in old recordings would otherwise resurrect it);
    // once unseen, it lives on only until its trail has faded.
    if (pt.dead && !seen.has(sig) && now - pt.diedAt > DEATH_FADE_MS) {
      tracks.delete(sig);
      impactSpawned.delete(sig);
    }
  }
  // Belt-and-suspenders GC of the dedupe map — never while its track
  // still exists (deleting it early re-arms the ring).
  for (const [sig, t] of Array.from(impactSpawned)) {
    if (!tracks.has(sig) && now - t > Math.max(STALE_MS, DEATH_FADE_MS)) {
      impactSpawned.delete(sig);
    }
  }

  // ---- pass 3: steering trails (under the icons) ------------------------
  for (const pt of tracks.values()) {
    if (pt.path.length < 2) continue;
    // Alive: the whole flight, launch to warhead, tail slightly softer
    // than the head. Dead: the complete trail fades out as one.
    const deadFade = pt.dead
      ? Math.max(0, 1 - (now - pt.diedAt) / DEATH_FADE_MS) : 1;
    if (deadFade <= 0) continue;
    const col = teamColor(pt.team);
    const n = pt.path.length;
    ctx.save();
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.lineWidth = 2 * cs.dpr;
    ctx.strokeStyle = col;
    for (let i = 1; i < n; i++) {
      const a = pt.path[i - 1]!, b = pt.path[i]!;
      const [ax, ay] = worldToScreen(view, cs, a.x, a.y);
      const [bx, by] = worldToScreen(view, cs, b.x, b.y);
      ctx.globalAlpha = deadFade * (0.35 + 0.45 * (i / n));
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.stroke();
    }
    ctx.restore();
  }

  // ---- pass 4: icons (gliding between the two newest samples) -----------
  for (const { r, track } of drawable) {
    // Ease from the previous sample to the newest over the smoothed
    // arrival interval. Bounded by real data on both ends: no
    // extrapolation, no overshoot, nothing a jittery arrival can fling.
    const t = track.gapMs > 0
      ? Math.min(1, (now - track.sampleAt) / track.gapMs) : 1;
    const wx = track.px + (track.x - track.px) * t;
    const wy = track.py + (track.y - track.py) * t;
    const [sx, sy] = worldToScreen(view, cs, wx, wy);
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
      if (track.heading != null) ctx.rotate(track.heading + Math.PI / 2);
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
