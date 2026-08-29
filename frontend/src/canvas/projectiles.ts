// Mortar / rocket / guided-missile projectile rendering with cross-tick
// tracking, steering trails, and an impact ring animation. Ported from
// the legacy vanilla-JS mortar-rounds module; adapted to our React
// canvas where MapCanvas already runs a continuous rAF loop (no need
// for the old requestDraw() pump — rings animate naturally).
//
// MOTION comes in already smooth: the replay path lerps projectile
// positions between the frames bracketing the continuous playhead
// (interpolation.ts), so this module draws `position` as given. Two
// attempts to own motion here — a forward dead-reckoner, then a
// render-behind glide — both lost to the same truth: the canvas graft
// pairs frame a's discrete fields with the interpolated entities, and
// any raw-position scheme de-synchronised from that pairing ping-ponged
// the tracker at every pair boundary (hitching, false freeze-kills,
// flooded trails). Motion belongs to the interpolator; this module owns
// IDENTITY (tracking), HISTORY (trails) and DEATH (rings, ghosts).
//
// Tracking strategy in priority order:
//   1. projectile.id (actor pointer) — stable while the actor lives,
//      so the cheapest sig match for matching this-tick to last-tick.
//   2. Nearest-neighbour within MATCH_RADIUS — ONLY for id-less rounds
//      whose position-bucket sig drifted, only against id-less tracks
//      of the same class, and never a dead track. An id-bearing
//      projectile is a distinct actor and must never steal another
//      round's tracker (six mortar rounds once took turns hijacking an
//      airborne TOW's tracker through this fallback).
//   3. velocity vector (when backend Phase B+ emits it) — first-tick
//      heading derived directly, no second sample required.
//
// Steering trail: guided missiles (TOW / Kornet / HJ-8) are the one
// projectile family whose PATH is the story — the gunner steers them —
// so each guided track accumulates displayed positions at least
// TRAIL_SPACING apart and draws the WHOLE flight, launch to warhead,
// in the firer's team colour for as long as the missile lives; when it
// dies the complete trail fades out as one. The spacing gate matters:
// positions arrive at 60 fps now, and appending them all held only the
// last few seconds of flight once the buffer capped. Ballistic rounds
// fly a fixed arc and get no trail (deliberate).
//
// Frozen-ghost rule (viewer side, for recordings already written): a
// wire-cut or self-destructed missile's actor lingers in server memory
// up to a minute, parked mid-air with hasImpacted still false — a live
// powered round moves every tick, so an explosive, unimpacted projectile
// frozen across two TICK-ADVANCING frames is treated as dead: impact
// ring at the true death point, icon off, trail fades. Resting smoke
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
// seek shows up as an impossible between-frame jump and resets that
// track's trail and heading instead of drawing a line across the map.

import {
  isGuidedProjectile,
  LAUNCHER_MAX_SQ,
  TRAIL_SPACING_SQ,
} from "../state/guided";
import type { ProjectileTimelinePoint } from "../state/replayReconstruct";
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
// Heading is measured from an ANCHOR that only moves when the round has
// travelled this far from it — not from per-frame deltas. At 60 fps a
// TOW moves ~1 m per frame (jittery heading) and a near-vertical mortar
// moves centimetres in XY (heading never updated at all, so the shell
// icon pointed wherever it spawned). Accumulated displacement gives
// both a stable, correct direction of travel.
const HEADING_ANCHOR_UE = 300;                           // 3 m
const HEADING_ANCHOR_SQ = HEADING_ANCHOR_UE * HEADING_ANCHOR_UE;
const FROZEN_DEAD_TICKS = 2;   // identical position across N tick advances
const VANISH_DEAD_TICKS = 2;   // absent across N tick advances
// A between-frame jump beyond this is a seek/teleport, not flight.
const SAMPLE_JUMP_SQ   = 60_000 * 60_000;                // 600 m

const MORTAR_ICON_URL  = "./icons/deployables/mortar_round.svg";

interface CanvasSize {
  width: number; height: number; cssWidth: number; cssHeight: number; dpr: number;
}

interface TrailPoint { x: number; y: number; }

interface Track {
  cls: string | null;       // classShort — NN may only pair like classes
  x: number;                // last displayed position (world units)
  y: number;
  z: number | null;         // for the freeze test — see below
  hx: number;               // heading anchor — see HEADING_ANCHOR_UE
  hy: number;
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
// _tracksForTest exposes it to the headless diagnostic harness, which
// replays a real recording through this exact module and watches
// identity/trail/death transitions frame by frame.
const tracks         = new Map<string, Track>();
export const _tracksForTest = tracks;
const impacts: Impact[] = [];
const impactSpawned  = new Map<string, number>();
let lastSnapTick: number | null = null;

// Replay-mode steering trails, precomputed at load and keyed by
// projectile id + tick (see buildProjectileTimelines). When a round has
// one, its trail is drawn as "the recorded path up to the playhead" —
// immune to seeks in either direction, which is exactly where the
// incremental per-track path (kept as the LIVE fallback) tears: a
// backward seek resets the tracker, a forward skip trips the jump
// guard, and both were reported from the field as trails restarting
// mid-flight.
let timelines: Map<string, ProjectileTimelinePoint[]> | null = null;
export function setProjectileTimelines(
  m: Map<string, ProjectileTimelinePoint[]> | null,
): void {
  timelines = m;
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
  // The displayed frame's capture time — the playhead the precomputed
  // trails are filtered against (see pass 3).
  const playMs = snap.timestamp
    ? (Date.parse(snap.timestamp) || null) : null;
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
    // fallback runs ONLY for id-less rounds (see header).
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
      x: r.position.x, y: r.position.y, z: r.position.z ?? null,
      hx: r.position.x, hy: r.position.y,
      heading: null, lastSeenAt: now, lastSeenTick: tick, kind,
      team: r.team ?? null, path: [], lastTick: tick, frozenTicks: 0,
      dead: false, diedAt: 0,
    };
    tracks.set(sig, track);
    // A newborn guided track gets its trail anchored to the LAUNCHER:
    // the firer's name is on the round, and whichever vehicle (an
    // emplacement gun or an ATGM truck) has that player in a seat within
    // sight of the first sample is where the wire physically starts.
    // Recorded data joined at display time — nothing invented; when no
    // seat matches, the trail honestly starts at the first sample.
    if (!prev && !timelines && isGuidedProjectile(r) && r.firer) {
      for (const v of snap.vehicles ?? []) {
        if (!v.position || !v.seats) continue;
        if (!v.seats.some((st) => st.occupantName === r.firer)) continue;
        const dx = v.position.x - r.position.x;
        const dy = v.position.y - r.position.y;
        if (dx * dx + dy * dy > LAUNCHER_MAX_SQ) continue;
        track.path.push({ x: v.position.x, y: v.position.y });
        break;
      }
    }

    const dxU = r.position.x - track.x;
    const dyU = r.position.y - track.y;
    const movedSq = prev ? dxU * dxU + dyU * dyU : Infinity;
    // Death is BIT-IDENTICAL position in all three axes — a stopped
    // actor repeats its transform exactly. Anything looser misreads a
    // live round: a mortar fired near max elevation climbs almost
    // vertically, moving well under a metre in XY between frames while
    // Z screams upward — a 2-D metre threshold declared those dead
    // seconds after launch and rang the impact at the mortar pit.
    const identical = prev
      && r.position.x === track.x
      && r.position.y === track.y
      && (r.position.z ?? null) === track.z;

    if (prev && movedSq > SAMPLE_JUMP_SQ) {
      // A seek, not flight — restart history at the new spot.
      track.path.length = 0;
      track.heading = null;
      track.hx = r.position.x;
      track.hy = r.position.y;
    } else {
      // Heading from accumulated displacement since the anchor, in
      // screen space (handles the map projection correctly).
      const ax = r.position.x - track.hx;
      const ay = r.position.y - track.hy;
      if (ax * ax + ay * ay >= HEADING_ANCHOR_SQ) {
        const [psx, psy] = worldToScreen(view, cs, track.hx, track.hy);
        const [csx, csy] = worldToScreen(view, cs,
          r.position.x, r.position.y);
        track.heading = Math.atan2(csy - psy, csx - psx);
        track.hx = r.position.x;
        track.hy = r.position.y;
      }
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

    // Frozen-ghost detection — only when the tick actually advanced.
    const advanced = tick != null
      && (track.lastTick == null || tick > track.lastTick);
    if (prev && advanced) {
      if (identical && r.isExplosive && !r.hasImpacted) {
        track.frozenTicks += 1;
        if (track.frozenTicks >= FROZEN_DEAD_TICKS) {
          markDead(sig, track, r.position.x, r.position.y, now);
        }
      } else if (!identical) {
        track.frozenTicks = 0;
      }
    }
    if (advanced) track.lastTick = tick;
    if (tick != null) track.lastSeenTick = tick;

    // Trail — guided rounds only, spaced so the whole flight fits: the
    // displayed stream is 60 fps, and appending every step held only
    // the last few seconds once the buffer capped.
    if (!track.dead && !timelines && isGuidedProjectile(r)) {
      const lastP = track.path[track.path.length - 1];
      const farEnough = !lastP
        || ((r.position.x - lastP.x) ** 2
            + (r.position.y - lastP.y) ** 2) >= TRAIL_SPACING_SQ;
      if (farEnough) {
        track.path.push({ x: r.position.x, y: r.position.y });
        if (track.path.length > TRAIL_MAX_POINTS) track.path.shift();
      }
    }

    track.x = r.position.x;
    track.y = r.position.y;
    track.z = r.position.z ?? null;
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
  for (const [sig, pt] of tracks) {
    // Alive: the whole flight, launch to warhead, tail slightly softer
    // than the head, closed off with a segment to the live position.
    // Dead: the complete trail fades out as one.
    const deadFade = pt.dead
      ? Math.max(0, 1 - (now - pt.diedAt) / DEATH_FADE_MS) : 1;
    if (deadFade <= 0) continue;
    let pts: TrailPoint[];
    const tl = timelines && sig.startsWith("id:")
      ? timelines.get(sig.slice(3)) : undefined;
    if (tl) {
      // Replay: the recorded path up to the playhead — a pure function
      // of data + current frame, so no seek can tear it. Filtered by
      // the displayed frame's TIMESTAMP (its real capture time); a
      // tick filter let reconstructed-frame points — which carry their
      // base full's tick but positions ahead of it — through one frame
      // early, and the trail tip ran ahead of the missile.
      const upTo = playMs == null
        ? tl : tl.filter((q) => q.t <= playMs);
      pts = pt.dead ? upTo : [...upTo, { x: pt.x, y: pt.y }];
    } else {
      pts = pt.dead ? pt.path : [...pt.path, { x: pt.x, y: pt.y }];
    }
    const col = teamColor(pt.team);
    const n = pts.length;
    if (n < 2) continue;
    ctx.save();
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.lineWidth = 2 * cs.dpr;
    ctx.strokeStyle = col;
    for (let i = 1; i < n; i++) {
      const a = pts[i - 1]!, b = pts[i]!;
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

  // ---- pass 4: icons ----------------------------------------------------
  for (const { r, track } of drawable) {
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
