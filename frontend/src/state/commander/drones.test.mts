// The three things about a drone the pawn does not carry: its team, its
// remaining flight time, and its killer.
//
// The recon numbers are the spec's: 15 / 15 health, a 100 s battery. The
// commander drone has no battery field at all — its budget is its calling
// action's active window.
import {
  buildDroneTracks, droneBudgetSec, droneRemainingSec, droneTeam,
  killerFromSamples,
} from "./drones.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}
function eq(a: any, b: any, msg: string) {
  ok(a === b, `${msg} (got ${JSON.stringify(a)}, want ${JSON.stringify(b)})`);
}

const RECON = (o: any = {}) => ({
  id: "0xd1", class: "BP_FlyingDrone_Recoverable_C",
  position: { x: 100, y: 200, z: 4000 }, yaw: 10, dead: false,
  health: 15, maxHealth: 15,
  pilotEosId: "eos-pilot", ownerEosId: "eos-owner",
  commandAction: null, batteryLifetimeMax: 100, lastHitByEosId: null, ...o,
}) as any;
const CMD = (o: any = {}) => ({
  ...RECON(), id: "0xd2", class: "BP_FlyingDrone_C",
  commandAction: "CommandAction_Drone_C", ...o,
});
delete (CMD() as any).batteryLifetimeMax;   // the class does not declare it

const TEAMS: any[] = [{
  id: 2,
  commander: { cooldowns: { actions: [
    { action: "CommandAction_Drone_C", displayName: "Recon Drone",
      enrouteSec: 10, activeSec: 420, cooldownSec: 600, categoryId: 0 },
  ] } },
}];
const frame = (t: number, drones: any[], players: any[] = []): any => ({
  timestamp: new Date(1700000000000 + t * 1000).toISOString(),
  gameState: { worldTimeSec: t }, teams: TEAMS, players,
  markers: [], vehicles: [], damageEvents: [], drones,
});

// --- the team comes from the owner ------------------------------------------
{
  const players = [{ eosId: "eos-owner", name: "Ruby", teamId: 2 }] as any;
  eq(droneTeam(RECON(), frame(0, [RECON()], players)), 2,
     "the drone flies for whoever deployed it");
  eq(droneTeam(RECON(), frame(0, [RECON()], [])), null,
     "an owner off this frame's roster gives no team, not a default");
  eq(droneTeam(RECON({ ownerEosId: null }), frame(0, [], players)), null,
     "and a pawn with no owner read gives none either");
}

// --- the budget --------------------------------------------------------------
{
  eq(droneBudgetSec(RECON(), TEAMS), 100, "the recon kit's own battery");
  const cmd = { ...CMD() };
  delete cmd.batteryLifetimeMax;
  eq(droneBudgetSec(cmd, TEAMS), 420,
     "a commander drone's budget is its calling action's active window");
  const orphan = { ...cmd, commandAction: "CommandAction_Unknown_C" };
  eq(droneBudgetSec(orphan, TEAMS), null,
     "with no entry to join, there is no budget to state");
}

// --- the killer: the first sample carrying `dead` ----------------------------
{
  // A second shooter moved the pointer 1.1 s after a kill — inside the full
  // frame's one-second gap — so the LAST reading is the wrong answer.
  eq(killerFromSamples([
    { dead: false, lastHitBy: null },
    { dead: true, lastHitBy: "eos-killer" },
    { dead: true, lastHitBy: "eos-second" },
  ]), "eos-killer", "the killer is the hitter at the moment of the death");
  eq(killerFromSamples([{ dead: true, lastHitBy: null }]), null,
     "a battery running out kills a drone with no shooter at all");
  eq(killerFromSamples([{ dead: false, lastHitBy: "eos-a" }]), null,
     "a hit that did not kill names nobody");
  eq(killerFromSamples([]), null, "and no samples name nobody");
}

// --- the whole life, over frames --------------------------------------------
{
  const frames = [
    frame(100, []),
    frame(101, [RECON()]),
    frame(102, [RECON()]),
    frame(103, [RECON({ dead: true, lastHitByEosId: "eos-killer" })]),
    frame(104, [RECON({ dead: true, lastHitByEosId: "eos-second" })]),
    frame(105, []),
  ];
  const t = buildDroneTracks(frames).get("0xd1")!;
  eq(t.firstSeenFrameIdx, 1, "the deploy is the frame the id first appears in");
  eq(t.firstSeenGameTime, 101, "dated on the game clock");
  eq(t.budgetSec, 100, "with its battery read off the pawn");
  eq(t.deadFromFrameIdx, 3, "the death is the first frame reading dead");
  eq(t.killerEosId, "eos-killer", "and the killer that frame's hitter");
  eq(t.lastSeenFrameIdx, 4, "the wreck lingers a frame and then is freed");
  eq(t.ownerEosId, "eos-owner", "the owner rides the whole way");
  // Remaining flight time: first seen + budget − now.
  eq(droneRemainingSec(t, 121), 80, "twenty seconds of a hundred flown");
  eq(droneRemainingSec(t, 201), 0, "and none left at the budget");
  eq(droneRemainingSec(t, null), null, "no clock, no countdown");
}
{
  // A drone already up in the first frame did not spawn there, so its
  // remaining time is unknowable rather than optimistic.
  const frames = [frame(100, [RECON()]), frame(101, [RECON()])];
  const t = buildDroneTracks(frames).get("0xd1")!;
  eq(t.firstFrameOfRecording, true, "we did not watch this one launch");
  eq(droneRemainingSec(t, 101), null, "so no flight time is claimed for it");
}
{
  // A commander drone whose calling action is only in the commander block:
  // the budget is joined there and nowhere else.
  const cmd = { ...CMD() };
  delete cmd.batteryLifetimeMax;
  const frames = [frame(50, []), frame(51, [cmd])];
  const t = buildDroneTracks(frames).get("0xd2")!;
  eq(t.budgetSec, 420, "the action's active window is the flight budget");
  eq(droneRemainingSec(t, 251), 220, "and the countdown runs off it");
}
{
  // Two drones up at once keep their own lives.
  const other = RECON({ id: "0xd9", ownerEosId: "eos-other" });
  const frames = [
    frame(10, []), frame(11, [RECON()]), frame(12, [RECON(), other]),
    frame(13, [other]),
  ];
  const tracks = buildDroneTracks(frames);
  eq(tracks.size, 2, "two pawns, two tracks");
  eq(tracks.get("0xd1")!.lastSeenFrameIdx, 2, "each with its own last frame");
  eq(tracks.get("0xd9")!.firstSeenGameTime, 12, "and its own deploy");
  eq(tracks.get("0xd9")!.killerEosId, null, "neither of them shot down");
}
{
  eq(buildDroneTracks([]).size, 0, "no frames, no drones");
  eq(buildDroneTracks([frame(1, [])]).size, 0, "no drones, no tracks");
}

console.log(`commander drones: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
