// Standalone unit test for two-tier replay reconstruction. Bundled with
// esbuild + run under node — no framework (matches diff.test.mts).
import {
  interpolateProjectilesBetweenFulls,
  ReplayReconstructor,
  reconstructFromPosition,
  isPositionFrame,
} from "./replayReconstruct.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}
function eq(a: any, b: any, msg: string) {
  ok(a === b, `${msg} (got ${JSON.stringify(a)}, want ${JSON.stringify(b)})`);
}

function player(eosId: string, name: string, x: number, y: number, h = 100): any {
  return {
    name, eosId, teamId: 1, roleId: null, stats: {},
    soldier: { addr: "0x1", position: { x, y, z: 5 }, health: h, yaw: 0 },
  };
}
function vehicle(id: string, x: number, y: number, h = 1000): any {
  return { id, team: 1, health: h, position: { x, y, z: 0 }, yaw: 0 };
}
function full(tick: number): any {
  return {
    timestamp: `2026-01-01T00:00:0${tick}+00:00`, tick,
    players: [player("eos-a", "Alice", 10, 20), player("eos-b", "Bob", 30, 40)],
    vehicles: [vehicle("0x3000", 50, 60)],
    markers: [{ kind: "attack" }],           // discrete state to check ref-share
    damageEvents: [{ killed: true }],
    gameState: { matchState: "InProgress" },
  };
}
// `fullTick: null` omits the key entirely - a default parameter cannot be
// bypassed by passing `undefined`, which is exactly what it means.
function pos(tick: number, players: any[], vehicles: any[] = [],
             fullTick: number | null = 1): any {
  const f: any = { t: "pos", tick,
                   timestamp: `2026-01-01T00:00:0${tick}.5+00:00`,
                   players, vehicles };
  if (fullTick !== null) f.fullTick = fullTick;
  return f;
}

// 1. isPositionFrame discriminates.
eq(isPositionFrame(pos(2, [])), true, "pos frame detected");
eq(isPositionFrame(full(1)), false, "full frame not a pos frame");

// 2. Full frame passes through unchanged.
{
  const r = new ReplayReconstructor();
  const f = full(1);
  eq(r.push(f), f, "full frame returned by identity");
}

// 3. Position frame before any full frame is dropped.
{
  const r = new ReplayReconstructor();
  eq(r.push(pos(1, [{ id: "eos-a", x: 1, y: 2 }])), null, "orphan pos dropped");
}

// 4. Reconstruction splices positions/health/yaw by key; carries discrete state.
{
  const base = full(1);
  const rec = reconstructFromPosition(
    base,
    pos(2, [{ id: "eos-a", x: 11, y: 21, h: 80, yaw: 90 }]),  // only Alice moved
    );
  eq(rec.players[0].soldier!.position!.x, 11, "Alice x spliced");
  eq(rec.players[0].soldier!.position!.y, 21, "Alice y spliced");
  eq(rec.players[0].soldier!.health, 80, "Alice health spliced");
  eq(rec.players[0].soldier!.yaw, 90, "Alice yaw spliced");
  // Bob absent from pos frame → carries last full position (no blink).
  eq(rec.players[1].soldier!.position!.x, 30, "Bob carries last x");
  eq(rec.players[1].soldier!.health, 100, "Bob carries last health");
  eq(rec.timestamp, "2026-01-01T00:00:02.5+00:00", "timestamp from pos frame");
  eq(rec.tick, 1, "tick from fullTick, not the sampler's own counter");
  eq(rec.damageEvents.length, 0, "damageEvents emptied (no double-count)");
  eq(rec.markers, base.markers, "markers shared by reference (no clone)");
  eq(rec.gameState, base.gameState, "gameState shared by reference");
}

// 5. Reconstruction never mutates the base full frame.
{
  const base = full(1);
  reconstructFromPosition(base, pos(2, [{ id: "eos-a", x: 99, y: 99, h: 1 }]));
  eq(base.players[0].soldier!.position!.x, 10, "base Alice x untouched");
  eq(base.players[0].soldier!.health, 100, "base Alice health untouched");
  eq(base.damageEvents.length, 1, "base damageEvents untouched");
}

// 6. Vehicle spliced by id; z preserved from base.
{
  const rec = reconstructFromPosition(
    full(1), pos(2, [], [{ id: "0x3000", x: 55, y: 65, h: 500, team: 2 }]));
  eq(rec.vehicles[0].position!.x, 55, "vehicle x spliced");
  eq(rec.vehicles[0].health, 500, "vehicle health spliced");
  eq(rec.vehicles[0].team, 2, "vehicle team spliced");
  eq(rec.vehicles[0].position!.z, 0, "vehicle z carried from base");
}

// 7. End-to-end stream: full, pos, pos, full → 4 increasing snapshots.
{
  const r = new ReplayReconstructor();
  const out: any[] = [];
  for (const line of [full(1),
                      pos(2, [{ id: "eos-a", x: 12, y: 22 }]),
                      pos(3, [{ id: "eos-a", x: 13, y: 23 }]),
                      full(4)]) {
    const s = r.push(line);
    if (s) out.push(s);
  }
  eq(out.length, 4, "4 snapshots reconstructed");
  eq(out[1].players[0].soldier!.position!.x, 12, "2nd frame Alice at 12");
  eq(out[2].players[0].soldier!.position!.x, 13, "3rd frame Alice at 13");
  eq(out[3].tick, 4, "4th frame is the new full frame");
}

// 8. A position frame carries TWO counters and only one of them is the world's.
// `tick` is the 4 Hz sampler's loop counter, `fullTick` the build the positions
// were spliced onto. They drift apart for as long as the service has been up -
// about a thousand apart on the first real two-tier recording - so reading the
// wrong one made the viewer's tick seesaw between the two on alternate frames.
{
  const rec = reconstructFromPosition(
    full(1041), pos(2317, [{ id: "eos-a", x: 1, y: 2 }], [], 1041));
  eq(rec.tick, 1041, "fullTick wins over the sampler tick");
}

// 9. No fullTick - a frame from before the encoder shipped it - falls back to
// the base frame's tick, which is the same world anyway since a position frame
// never advances it. Never the sampler's counter.
{
  const rec = reconstructFromPosition(
    full(1041), pos(2317, [{ id: "eos-a", x: 1, y: 2 }], [], null));
  eq(rec.tick, 1041, "falls back to the base tick, not the sampler tick");
}

// 10. Across a whole two-tier stream the tick never jumps about.
{
  const r = new ReplayReconstructor();
  const lines = [full(100),
                 pos(9001, [{ id: "eos-a", x: 1, y: 1 }], [], 100),
                 pos(9002, [{ id: "eos-a", x: 2, y: 2 }], [], 100),
                 pos(9003, [{ id: "eos-a", x: 3, y: 3 }], [], 100),
                 full(101),
                 pos(9004, [{ id: "eos-a", x: 4, y: 4 }], [], 101)];
  const ticks: number[] = [];
  for (const line of lines) {
    const out = r.push(line);
    if (out) ticks.push(out.tick!);
  }
  eq(JSON.stringify(ticks), JSON.stringify([100, 100, 100, 100, 101, 101]),
     "tick is monotonic across a two-tier stream");
}

// A reconstructed frame carries the POSITION frame's own timestamp —
// playback paces by timestamp, so inheriting the base full's stamp
// would collapse the whole full+positions group into one instant.
// This pins behavior that already existed; it was nearly regressed.
{
  const r = new ReplayReconstructor();
  const base = full(1);
  r.push(base);
  const rec: any = r.push(pos(2, [{ id: "eos-a", x: 99, y: 99 }]));
  eq(rec.timestamp, "2026-01-01T00:00:02.5+00:00",
     "reconstructed frame keeps the pos frame's own timestamp");
  ok(rec.timestamp !== base.timestamp,
     "and does not inherit the base full's timestamp");
}

// Projectile positions splice like vehicles when the frame carries them,
// and an old recording without the key shares the base array untouched.
{
  const r = new ReplayReconstructor();
  const base: any = full(1);
  base.projectiles = [
    { id: "0x9000", classShort: "BP_TOW_Proj_C",
      position: { x: 1, y: 2, z: 3 } },
    { id: "0x9001", classShort: "BP_Mortarround4_C",
      position: { x: 7, y: 8, z: 9 } },
  ];
  r.push(base);
  const withPr: any = pos(2, []);
  withPr.projectiles = [{ id: "0x9000", x: 100, y: 200, z: 300 }];
  const rec: any = r.push(withPr);
  eq(rec.projectiles[0].position.x, 100, "sampled projectile moved");
  eq(rec.projectiles[0].position.z, 300, "sampled projectile carries z");
  ok(rec.projectiles[1] === base.projectiles[1],
     "unsampled projectile shared by reference");
  const withoutPr: any = pos(3, []);
  const rec2: any = r.push(withoutPr);
  ok(rec2.projectiles === base.projectiles,
     "an old recording without the key shares the whole base array");
}

// Load-time projectile smoothing: reconstructed frames between two fulls
// get positions interpolated on their own timestamps; a genuinely
// sampled entry survives; a round absent from the next full holds; and
// a single-tier stream (no reconstructed frames) is a no-op.
{
  const r = new ReplayReconstructor();
  const a: any = full(1);   // ts ...:01
  a.projectiles = [
    { id: "0x9000", classShort: "BP_TOW_Proj_C",
      position: { x: 0, y: 0, z: 0 } },
    { id: "0x9001", classShort: "BP_Mortarround4_C",
      position: { x: 500, y: 500, z: 500 } },   // absent from B — dies
  ];
  const frames: any[] = [];
  frames.push(r.push(a));
  frames.push(r.push(pos(2, [])));              // ts ...:02.5 — recon
  const b: any = full(4);   // ts ...:04
  b.projectiles = [
    { id: "0x9000", classShort: "BP_TOW_Proj_C",
      position: { x: 3000, y: 0, z: 300 } },
  ];
  frames.push(r.push(b));
  interpolateProjectilesBetweenFulls(frames);
  const rec = frames[1];
  // (2.5 - 1) / (4 - 1) = 0.5
  eq(rec.projectiles[0].position.x, 1500,
     "recon projectile interpolated between the bracketing fulls");
  eq(rec.projectiles[0].position.z, 150, "z interpolates too");
  eq(rec.projectiles[1].position.x, 500,
     "a round absent from the next full holds its last position");
  ok(frames[0].projectiles[0].position.x === 0
     && frames[2].projectiles[0].position.x === 3000,
     "the fulls themselves are untouched");
}
{
  // Real sampled data (a spliced, non-shared entry) wins over the pass.
  const r = new ReplayReconstructor();
  const a: any = full(1);
  a.projectiles = [{ id: "0x9000", classShort: "BP_TOW_Proj_C",
                     position: { x: 0, y: 0, z: 0 } }];
  const frames: any[] = [r.push(a)];
  const withPr: any = pos(2, []);
  withPr.projectiles = [{ id: "0x9000", x: 42, y: 42, z: 42 }];
  frames.push(r.push(withPr));
  const b: any = full(4);
  b.projectiles = [{ id: "0x9000", classShort: "BP_TOW_Proj_C",
                     position: { x: 3000, y: 0, z: 300 } }];
  frames.push(r.push(b));
  interpolateProjectilesBetweenFulls(frames);
  eq(frames[1].projectiles[0].position.x, 42,
     "a genuinely sampled position is never overwritten");
}

console.log(`\nreplay reconstruct tests: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
