// How a tactical request ends, read out of when its marker stopped being in
// the list. Nothing about the end is recorded; the server's ~61 s marker
// sweep is what lets an early removal be told from a fuse running out.
import {
  APPROVED_FUSE_SEC, PENDING_FUSE_SEC, trackRequests,
} from "./requests.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}
function eq(a: any, b: any, msg: string) {
  ok(a === b, `${msg} (got ${JSON.stringify(a)}, want ${JSON.stringify(b)})`);
}

const PENDING = "BP_MapMarker_Command_SLRequest_C";
const APPROVED = "BP_MapMarker_Command_Request_C";

const marker = (id: string, type: string) => ({
  id, type, team: 1, squad: 3, fireTeamId: -1,
  ownerPlayerStateAddr: null, position: { x: 1000, y: 2000, z: 30 },
});
// One frame per whole second of game clock, which is the recorder's own
// full-frame cadence.
const frame = (t: number, markers: any[]): any => ({
  timestamp: new Date(1700000000000 + t * 1000).toISOString(),
  gameState: { worldTimeSec: t }, teams: [], players: [],
  markers, vehicles: [], damageEvents: [],
});

// --- deleted: gone before its fuse could have run ---------------------------
{
  // The three removals measured on 2026-09-07 were at 22, 55 and 57 s, and
  // no untouched marker ever went before 61.
  const frames = [frame(100, [])];
  for (let t = 101; t < 101 + 55; t++) frames.push(frame(t, [marker("0xp", PENDING)]));
  frames.push(frame(156, []));
  const [r] = trackRequests(frames);
  eq(r!.phase, "pending", "it never got approved");
  eq(r!.outcome, "deleted", "gone at 55 s with no approved twin: a delete");
  eq(r!.ageAtRemovalSec, 55, "and the age it went at is reported");
  eq(r!.goneFrameIdx, frames.length - 1, "with the frame it vanished on");
}

// --- expired: it ran its fuse ------------------------------------------------
{
  const frames = [frame(100, [])];
  for (let t = 101; t <= 101 + PENDING_FUSE_SEC; t++)
    frames.push(frame(t, [marker("0xp", PENDING)]));
  frames.push(frame(163, []));
  const [r] = trackRequests(frames);
  eq(r!.outcome, "expired", "past the 61 s fuse, the sweep simply took it");
  ok(r!.ageAtRemovalSec! >= PENDING_FUSE_SEC, "and the age says so");
}

// --- approval: one request changing state, twin and all ----------------------
{
  const frames = [
    frame(100, []),
    frame(101, [marker("0xp", PENDING)]),
    frame(102, [marker("0xp", PENDING)]),
    // The approved marker appears at once; the pending twin stays until the
    // next sweep, so both are on the map for up to a minute.
    frame(103, [marker("0xp", PENDING), marker("0xa", APPROVED)]),
    frame(104, [marker("0xp", PENDING), marker("0xa", APPROVED)]),
    frame(105, [marker("0xa", APPROVED)]),
  ];
  const tracked = trackRequests(frames);
  eq(tracked.length, 1, "the pending marker and its approved twin are one request");
  const r = tracked[0]!;
  eq(r.phase, "approved", "which ends up approved");
  eq(r.approvedGameTime, 103, "stamped when the approved half appeared");
  eq(r.firstSeenGameTime, 101, "and dated from when the SL asked");
  eq(r.outcome, null, "still on the map when the recording ends: no reading");
}

// --- consumed: an approved request gone before ITS fuse ----------------------
{
  const frames = [frame(100, []), frame(101, [marker("0xp", PENDING)])];
  for (let t = 102; t < 102 + 20; t++)
    frames.push(frame(t, [marker("0xa", APPROVED)]));
  frames.push(frame(122, []));
  const [r] = trackRequests(frames);
  eq(r!.phase, "approved", "it was approved");
  eq(r!.outcome, "consumed",
     "and went 20 s later, before its own ~60 s fuse: a call took it");
  eq(r!.ageAtRemovalSec, 20, "dated from the approval, not the request");
}
{
  const frames = [frame(100, []), frame(101, [marker("0xp", PENDING)])];
  for (let t = 102; t <= 102 + APPROVED_FUSE_SEC; t++)
    frames.push(frame(t, [marker("0xa", APPROVED)]));
  frames.push(frame(200, []));
  const [r] = trackRequests(frames);
  eq(r!.outcome, "expired", "an approved request that ran its fuse expired");
}

// --- what the recording cannot say ------------------------------------------
{
  // Already on the map in the first frame: its age is a lower bound, so an
  // early-looking removal could be a perfectly ordinary expiry.
  const frames = [
    frame(100, [marker("0xp", PENDING)]),
    frame(101, [marker("0xp", PENDING)]),
    frame(102, []),
  ];
  const [r] = trackRequests(frames);
  eq(r!.outcome, null,
     "a request we did not watch appear is never called deleted");
  eq(r!.firstSeenFrameIdx, 0, "because its first frame is the recording's");
  eq(r!.ageAtRemovalSec, 2, "the observed age is still reported, as a bound");
}
{
  // No game clock, no fuse, no reading.
  const noClock = (t: number, markers: any[]): any => ({
    ...frame(t, markers), gameState: {},
  });
  const frames = [noClock(0, []), noClock(1, [marker("0xp", PENDING)]),
                  noClock(2, [])];
  const [r] = trackRequests(frames);
  eq(r!.outcome, null, "without worldTimeSec nothing can be dated");
  eq(r!.ageAtRemovalSec, null, "and no age is invented");
}
{
  eq(trackRequests([]).length, 0, "no frames, no requests");
  eq(trackRequests([frame(1, [])]).length, 0, "no markers, no requests");
  eq(trackRequests([frame(1, [
    { id: "0xz", type: "BP_MapMarker_POI", team: 1, squad: 1, fireTeamId: -1,
      ownerPlayerStateAddr: null, position: { x: 0, y: 0, z: 0 } },
  ])]).length, 0, "and a POI is not a request");
}

// --- two squads, two lives ---------------------------------------------------
{
  const other = (id: string, type: string) => ({
    ...marker(id, type), squad: 5, position: { x: 90000, y: 2000, z: 30 },
  });
  const frames = [
    frame(100, []),
    frame(101, [marker("0xp", PENDING), other("0xq", PENDING)]),
    frame(102, [other("0xq", PENDING)]),
    frame(103, []),
  ];
  const tracked = trackRequests(frames);
  eq(tracked.length, 2, "two squads' requests are tracked apart");
  eq(tracked[0]!.squad, 3, "in the order they first appeared");
  eq(tracked[0]!.goneGameTime, 102, "and each one's end is its own");
  eq(tracked[1]!.goneGameTime, 103, "not its neighbour's");
}

console.log(`commander requests: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
