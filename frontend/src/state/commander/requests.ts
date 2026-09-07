// The life of a tactical request, read out of the frames it appears in.
//
// Nothing about a request's END reaches the recording: the server holds no
// "deleted", no "expired", no "consumed" flag, and a marker simply stops
// being in the list. What the server DOES have is a rhythm — it sweeps its
// markers about every 61 s — and that rhythm is what lets an early
// disappearance be told from a fuse running out (spec §9, "Pending versus
// approved, and deletes", decision D20).
//
// Every claim below is bounded by what was measured: removals at 22, 55 and
// 57 s were deletes, and no untouched marker ever went before 61 s.

import type { Marker, Snapshot } from "../types";
import { requestPhase, SAME_PLACE_CM, type RequestPhase } from "./markers";

/** The server's marker sweep interval, and so a pending request's fuse: a
 *  pending marker that nothing touches survives ~61 s and goes at the sweep
 *  after that (T15, 2026-09-07). */
export const PENDING_FUSE_SEC = 61;

/** An approved request's own fuse — "about 60 s" (T15). Both fuses are the
 *  game's, not the recorder's, and neither is written to the file. */
export const APPROVED_FUSE_SEC = 60;

/** How a request left the map, where the recording can say.
 *
 *  - `deleted` — a PENDING request gone before its fuse could have run, with
 *    no approved twin at its spot: its squad leader took it back (D20).
 *  - `consumed` — an APPROVED request gone before ITS fuse could have run.
 *    A call consuming it is the only cause §9 names for that.
 *  - `expired` — gone at or after the fuse: it simply ran out.
 *
 *  `null` means the recording cannot say: the request was already on the map
 *  in the first frame (so its age is only a lower bound), the game clock was
 *  not readable, or it is still up when the recording ends. */
export type RequestOutcome = "deleted" | "consumed" | "expired";

export interface RequestLife {
  /** The request's own key — from the first marker that showed it. */
  key: string;
  team: number | null;
  squad: number | null;
  fireTeamId: number | null;
  position: { x: number; y: number; z: number | null };
  /** Its state the last time it was on the map. */
  phase: RequestPhase;
  firstSeenFrameIdx: number;
  firstSeenGameTime: number | null;
  /** When the approved marker first appeared, if it ever did. */
  approvedFrameIdx: number | null;
  approvedGameTime: number | null;
  lastSeenFrameIdx: number;
  lastSeenGameTime: number | null;
  /** The first frame with no marker at this spot, and its game time. Null
   *  while the request is still up at the end of the recording. */
  goneFrameIdx: number | null;
  goneGameTime: number | null;
  outcome: RequestOutcome | null;
  /** The observed age at removal, in seconds — a LOWER bound when the
   *  request was already on the map in the first frame. Null when the game
   *  clock could not be read. */
  ageAtRemovalSec: number | null;
}

interface Track extends RequestLife {
  pendingId: string | null;
  approvedId: string | null;
  /** False once we know the age is a lower bound only. */
  ageIsKnown: boolean;
}

function near(
  a: { x: number; y: number }, b: { x: number; y: number },
): boolean {
  const dx = a.x - b.x, dy = a.y - b.y;
  return dx * dx + dy * dy <= SAME_PLACE_CM * SAME_PLACE_CM;
}

function keyOf(m: Marker, idx: number): string {
  return `${m.team ?? "?"}|${m.squad ?? "?"}|${idx}`;
}

/** Every request the recording carries, with how it ended where the sweep
 *  model can say.
 *
 *  Frames are matched by team, squad and position rather than by marker id,
 *  because approval replaces one actor with another — that swap IS the
 *  request changing state, and a pending marker still sitting beside its
 *  approved twin for up to a minute is one request, not two. */
export function trackRequests(frames: Snapshot[]): RequestLife[] {
  const done: Track[] = [];
  let live: Track[] = [];

  for (let i = 0; i < frames.length; i++) {
    const f = frames[i]!;
    const now = f.gameState?.worldTimeSec ?? null;
    // A reconstructed frame shares its base full frame's `markers` array by
    // reference, so this walks the same list several times over an interval;
    // that is harmless — it is the same state, and the tracks only move when
    // it changes.
    const seen = new Set<Track>();
    for (const m of f.markers ?? []) {
      const phase = requestPhase(m.type);
      if (!phase || !m.position) continue;
      let t = live.find(
        (x) => x.team === m.team && x.squad === m.squad
               && near(x.position, m.position!));
      if (!t) {
        t = {
          key: keyOf(m, done.length + live.length),
          team: m.team, squad: m.squad, fireTeamId: m.fireTeamId,
          position: m.position, phase,
          firstSeenFrameIdx: i, firstSeenGameTime: now,
          approvedFrameIdx: null, approvedGameTime: null,
          lastSeenFrameIdx: i, lastSeenGameTime: now,
          goneFrameIdx: null, goneGameTime: null,
          outcome: null, ageAtRemovalSec: null,
          pendingId: null, approvedId: null,
          // A request already on the map in the very first frame started
          // before the recording did, so nothing here can date it.
          ageIsKnown: i > 0,
        };
        live.push(t);
      }
      t.lastSeenFrameIdx = i;
      t.lastSeenGameTime = now;
      if (phase === "pending") {
        t.pendingId = m.id;
      } else {
        t.approvedId = m.id;
        if (t.phase !== "approved") {
          t.phase = "approved";
          t.approvedFrameIdx = i;
          t.approvedGameTime = now;
        }
      }
      seen.add(t);
    }
    const gone = live.filter((t) => !seen.has(t));
    if (gone.length) {
      for (const t of gone) {
        t.goneFrameIdx = i;
        t.goneGameTime = now;
        finish(t);
        done.push(t);
      }
      live = live.filter((t) => seen.has(t));
    }
  }
  // Whatever is still up when the recording ends keeps outcome null: the
  // recording does not say what happened next, and nor will the viewer.
  return [...done, ...live].sort(
    (a, b) => a.firstSeenFrameIdx - b.firstSeenFrameIdx);
}

function finish(t: Track): void {
  // The clock the fuses are measured on. Without it there is no age and so
  // no reading — the request is simply gone.
  const from = t.phase === "approved" ? t.approvedGameTime : t.firstSeenGameTime;
  if (from == null || t.goneGameTime == null) return;
  const age = t.goneGameTime - from;
  t.ageAtRemovalSec = age;
  const fuse = t.phase === "approved" ? APPROVED_FUSE_SEC : PENDING_FUSE_SEC;
  if (age >= fuse) {
    // At or past the fuse, the sweep that took it is the fuse's own.
    t.outcome = "expired";
    return;
  }
  // Below the fuse. For a PENDING request that is only readable when we
  // watched it appear — otherwise the age is a lower bound and an
  // early-looking removal may be a perfectly ordinary expiry.
  if (t.phase === "pending" && !t.ageIsKnown) return;
  // An approved request is dated from its own approval, which we always
  // watched happen when the pending half was tracked; when the request was
  // approved before the recording began, `approvedGameTime` is null and we
  // returned above.
  t.outcome = t.phase === "approved" ? "consumed" : "deleted";
}
