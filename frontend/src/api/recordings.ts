// Thin fetch helpers for /api/recordings + /api/recording/<id>.

import type { RecordingMeta, Snapshot } from "../state/types";
import {
  buildProjectileTimelines,
  interpolateProjectilesBetweenFulls,
  ReplayReconstructor,
  type RecordingLine,
} from "../state/replayReconstruct";
import { setProjectileTimelines } from "../canvas/projectiles";
import {
  isPackedHeader,
  REPLAY_FORMAT_VERSION,
  ReplayUnpacker,
} from "../state/replayUnpack";

export async function listRecordings(): Promise<RecordingMeta[]> {
  const r = await fetch("./api/recordings", { cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as RecordingMeta[];
}

export async function fetchRecordingMeta(id: string): Promise<RecordingMeta> {
  const r = await fetch(`./api/recording/${encodeURIComponent(id)}/meta`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as RecordingMeta;
}

// Fetches the full NDJSON stream and parses to Snapshot[] in memory.
// For a typical 30 min match this is ~14 MB raw → ~50 MB parsed JS objects.
//
// Streamed + parsed line-by-line as chunks arrive, so (a) `onProgress` can
// report a live frame count for the loading UI, and (b) the parse cost is
// spread across the download instead of one main-thread-freezing pass at the
// end. `onProgress` is called with the running parsed-frame count.
export async function fetchRecordingFrames(
  id: string,
  onProgress?: (framesLoaded: number) => void,
): Promise<Snapshot[]> {
  // Ask for the compact format. A server that does not know it ignores the
  // parameter and sends the original, so this is safe to request always.
  const r = await fetch(
    `./api/recording/${encodeURIComponent(id)}?v=${REPLAY_FORMAT_VERSION}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const out: Snapshot[] = [];
  // Two-tier recordings interleave full frames with compact "t":"pos" position
  // frames; the reconstructor folds them into one increasing Snapshot[]. A
  // full-only .sqrx (no "t") passes straight through unchanged.
  const recon = new ReplayReconstructor();
  // The compact format announces itself on its first line. Until then this
  // reads exactly as it always did, so an older server — or a recording whose
  // compact form has not been built yet — is not a special case anywhere.
  const unpacker = new ReplayUnpacker();
  let packed: boolean | null = null;
  const pushLine = (line: string) => {
    if (!line) return;
    let parsed: RecordingLine;
    try {
      parsed = JSON.parse(line) as RecordingLine;
    } catch {
      // Bad line; skip — partial-write tail is rare but possible.
      return;
    }
    if (packed === null) packed = isPackedHeader(parsed);
    // Two layers, and they compose: the unpacker undoes the WIRE format, the
    // reconstructor undoes two-tier recording. Pushing an unpacked frame
    // straight out skipped the second one, so a `"t":"pos"` line — present in
    // most recent recordings — reached the viewer as if it were a snapshot.
    const frame = packed
      ? (unpacker.push(parsed as unknown as Record<string, unknown>) as
          RecordingLine | null)
      : parsed;
    if (!frame) return;
    const snap = recon.push(frame);
    if (snap) out.push(snap);
  };

  const reader = r.body?.getReader();
  if (!reader) {
    // No streaming support — fall back to a single blocking read.
    for (const line of (await r.text()).split("\n")) pushLine(line);
    interpolateProjectilesBetweenFulls(out);
    setProjectileTimelines(buildProjectileTimelines(out));
    onProgress?.(out.length);
    return out;
  }

  const decoder = new TextDecoder();
  let buf = "";
  let lastReport = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl = buf.indexOf("\n");
    while (nl >= 0) {
      pushLine(buf.slice(0, nl));
      buf = buf.slice(nl + 1);
      nl = buf.indexOf("\n");
    }
    if (out.length - lastReport >= 10) {
      onProgress?.(out.length);
      lastReport = out.length;
    }
  }
  buf += decoder.decode();
  pushLine(buf.trim());
  // With the whole timeline loaded, the bracketing full for every
  // reconstructed frame is known — fill in projectile motion so the
  // pair stream never stalls at position-frame boundaries, and build
  // the seek-proof steering trails for guided rounds.
  interpolateProjectilesBetweenFulls(out);
  setProjectileTimelines(buildProjectileTimelines(out));
  onProgress?.(out.length);
  return out;
}
