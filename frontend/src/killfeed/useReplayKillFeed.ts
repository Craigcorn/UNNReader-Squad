// Replay kill feed — a pure filter over the pre-computed match timeline.
//
// The live path derives the feed incrementally (useKillFeed + diff.ts): each
// tick is diffed against the previous, and an attacker is buffered for a while
// so the death it causes can be attributed. That model is forward-only and
// cannot survive a SEEK: scrubbing back left stale future rows on screen, and
// re-crossing a death forward re-emitted it with no attacker ("?") because the
// buffered attack had already been consumed on the first pass.
//
// Replay instead pre-computes the WHOLE feed once at load (api/replay.ts →
// replayKillTimeline), each entry tagged with the frame index it fired on. Here
// we just show the entries whose frameIdx <= the playhead, newest first. Since
// the timeline is frameIdx-ascending, the cut point is a binary search, and we
// only rewrite the store's killFeed when that count actually changes (crossing
// a kill, or a seek) — not on every playback tick.

import { useEffect } from "react";
import { useViewerStore, KILL_FEED_MAX } from "../state/viewerStore";

export function useReplayKillFeed() {
  useEffect(() => {
    let lastCount = -1;
    let lastTl: unknown = null;
    const unsub = useViewerStore.subscribe((s) => {
      if (s.mode !== "replay") { lastCount = -1; lastTl = null; return; }
      const tl = s.replayKillTimeline;
      // A freshly loaded replay swaps in a new timeline array — re-seed so the
      // first playhead position repaints even if its count matches the old one.
      if (tl !== lastTl) { lastTl = tl; lastCount = -1; }
      const idx = s.replay.currentIdx;
      // Count of entries with frameIdx <= idx (timeline is ascending by frameIdx).
      let lo = 0, hi = tl.length;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (tl[mid]!.frameIdx <= idx) lo = mid + 1; else hi = mid;
      }
      if (lo === lastCount) return;  // visible set unchanged → no store write
      lastCount = lo;
      // Newest first, capped — same shape the live feed produces.
      const start = Math.max(0, lo - KILL_FEED_MAX);
      useViewerStore.getState().setKillFeed(tl.slice(start, lo).reverse());
    });
    return unsub;
  }, []);
}
