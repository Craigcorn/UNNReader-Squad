// The Hz readout's source of truth: how often a NEW snapshot arrives.
//
// This used to be the interval between arrivals, and that meant the same thing
// right up until two-tier recording existed. A two-tier replay delivers about
// four frames a second — one full snapshot and three position frames spliced
// onto it — so an arrival-to-arrival average announced 4 Hz for a reader that
// was building one snapshot a second, and jittered between 3 and 4.5 as the two
// cadences beat against each other.
//
// A position frame carries no new tick: it is the same world, moved. So the
// tick advancing is exactly the event worth timing, and timing that gives the
// same answer on a single-tier recording — where every frame advances it — as
// on a two-tier one.

export interface TickRateState {
  avgTickMs: number;
  /** Arrival time of the frame that last advanced the tick; 0 = not anchored. */
  lastTickMs: number;
  /** The tick it advanced to. */
  lastTick: number | null;
}

// Under 50 ms is a double delivery, over 5 s is a gap, a seek, or a tab that
// was in the background — none of them is a rate. The EMA is deliberately slow
// to move, because this is a readout a human stares at.
const MIN_DT_MS = 50;
const MAX_DT_MS = 5000;
const EMA_WEIGHT = 0.3;

export function advanceTickRate(
  prev: TickRateState,
  tick: number | null | undefined,
  nowMs: number,
): TickRateState {
  const t = typeof tick === "number" ? tick : null;
  // An untickable frame — an older capture, or one whose tick could not be
  // read — falls back to timing every arrival. Freezing the readout at its
  // seed value forever would be the worse failure of the two.
  const advanced = t === null || prev.lastTick === null || t !== prev.lastTick;
  if (!advanced) return prev;
  const dt = nowMs - prev.lastTickMs;
  const usable = prev.lastTickMs > 0 && dt > MIN_DT_MS && dt < MAX_DT_MS;
  return {
    avgTickMs: usable
      ? prev.avgTickMs * (1 - EMA_WEIGHT) + dt * EMA_WEIGHT
      : prev.avgTickMs,
    lastTickMs: nowMs,
    lastTick: t,
  };
}
