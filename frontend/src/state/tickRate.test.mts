// What the Hz readout is allowed to count. Standalone, esbuild + node, no
// framework (matches replayReconstruct.test.mts).
import { advanceTickRate, type TickRateState } from "./tickRate.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}
function eq(a: any, b: any, msg: string) {
  ok(a === b, `${msg} (got ${JSON.stringify(a)}, want ${JSON.stringify(b)})`);
}
function near(a: number, b: number, tol: number, msg: string) {
  ok(Math.abs(a - b) <= tol, `${msg} (got ${a}, want ~${b})`);
}

const seed: TickRateState = { avgTickMs: 2000, lastTickMs: 0, lastTick: null };

// Feed a stream of (tick, arrivalMs) pairs and return the settled state.
function run(frames: [number | null | undefined, number][],
             from: TickRateState = seed): TickRateState {
  let s = from;
  for (const [tick, now] of frames) s = advanceTickRate(s, tick, now);
  return s;
}

// 1. The first frame anchors and changes nothing: one arrival is not an
//    interval.
{
  const s = advanceTickRate(seed, 1, 5000);
  eq(s.avgTickMs, 2000, "first frame leaves the seed alone");
  eq(s.lastTickMs, 5000, "first frame anchors the clock");
  eq(s.lastTick, 1, "first frame remembers the tick");
}

// 2. Single-tier: every frame advances the tick, so every gap is timed. One
//    snapshot per second converges on 1000 ms.
{
  const frames: [number, number][] = [];
  for (let i = 0; i < 40; i++) frames.push([i + 1, 1000 + i * 1000]);
  near(run(frames).avgTickMs, 1000, 20, "1 Hz stream reads as 1000 ms");
}

// 3. Two-tier: three position frames per snapshot, all carrying the SAME tick
//    (that is what reconstruction gives them). The readout must report the
//    SNAPSHOT rate, not the four-frames-a-second delivery rate. This is the
//    bug: the old rule timed arrivals and announced 4 Hz for a reader building
//    one snapshot a second, jittering 3-4.5 as the two cadences beat.
{
  const frames: [number, number][] = [];
  let t = 1000;
  for (let i = 0; i < 40; i++) {
    const tick = i + 1;
    frames.push([tick, t]);                  // the full snapshot
    frames.push([tick, t + 250]);            // three position frames after it
    frames.push([tick, t + 500]);
    frames.push([tick, t + 750]);
    t += 1000;
  }
  const s = run(frames);
  near(s.avgTickMs, 1000, 20, "two-tier stream still reads as 1000 ms");
  ok(s.avgTickMs > 700, "never collapses towards the 250 ms position cadence");
}

// 4. A repeated tick returns the state UNCHANGED, object identity included, so
//    a position frame cannot even nudge the anchor forward.
{
  const anchored = advanceTickRate(seed, 7, 1000);
  const again = advanceTickRate(anchored, 7, 1250);
  ok(again === anchored, "a repeated tick is a no-op");
}

// 5. A frame with no tick at all - an older capture, or one whose tick could
//    not be read - falls back to timing arrivals. Freezing the readout on its
//    seed forever would be the worse of the two failures.
{
  const frames: [undefined, number][] = [];
  for (let i = 0; i < 40; i++) frames.push([undefined, 1000 + i * 500]);
  near(run(frames).avgTickMs, 500, 20, "untickable frames still time arrivals");
}

// 6. Gaps are not rates. A seek, a backgrounded tab or a double delivery must
//    not enter the average - but must still re-anchor, so the frame after it
//    is measured from the right place.
{
  const anchored = advanceTickRate(seed, 1, 1000);
  const huge = advanceTickRate(anchored, 2, 1000 + 60_000);
  eq(huge.avgTickMs, 2000, "a 60 s gap does not enter the average");
  eq(huge.lastTickMs, 61_000, "but it does re-anchor");
  const tiny = advanceTickRate(anchored, 2, 1010);
  eq(tiny.avgTickMs, 2000, "a 10 ms double delivery does not either");
}

// 7. A tick that goes BACKWARDS is still a change, and still timed. Seeking
//    backwards in a replay is exactly that, and the interval it produces is a
//    real one.
{
  const anchored = advanceTickRate(seed, 500, 1000);
  const back = advanceTickRate(anchored, 100, 2000);
  // 2000 * 0.7 + 1000 * 0.3
  near(back.avgTickMs, 1700, 1, "the backward jump is timed like any other");
  eq(back.lastTick, 100, "and the new tick is remembered");
}

console.log(`\ntick rate tests: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
