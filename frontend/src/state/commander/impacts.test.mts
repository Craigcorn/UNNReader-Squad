// The rounds a call fired and where they landed — spec §9, "Asset impacts".
//
// The two joins under test are the ones the fields make narrower than the
// sentence: the caller reaches a round only through the roster (the recorder
// writes `firer` as a NAME and `callerEosId` as an EOS id), and the class half
// applies only where the actor names a class, which is artillery and nothing
// else.
import {
  buildCallImpacts, callerFirerName, landedBy, roundMatchesCall, roundsByCall,
} from "./impacts.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}
function eq(a: any, b: any, msg: string) {
  ok(a === b, `${msg} (got ${JSON.stringify(a)}, want ${JSON.stringify(b)})`);
}

const RUBY = "eos-ruby", SOL = "eos-sol";
const PLAYERS = [
  { eosId: RUBY, name: "Ruby", teamId: 1 },
  { eosId: SOL, name: "Sol", teamId: 1 },
] as any[];

/** An artillery call: it names its own projectile class. */
const GUNS = (o: any = {}) => ({
  id: "0xa-guns", class: "BP_CommandActor_Artillery_Radius_C", team: 1,
  action: "CommandAction_Artillery_Barrage_USMC_C", callerEosId: RUBY,
  position: { x: 100, y: 100, z: 60 }, yaw: 0, actionDestroyed: false,
  shellsPerBarrage: 10, barrageCount: 8, currentBarrage: 0,
  preWarningShells: 2, preWarningDelaySec: 12, currentPrewarningShells: 0,
  projectile: "BP_Projectile_155mm_C", ...o,
}) as any;
/** A strike aircraft: its family declares no `projectile` at all. */
const JET = (o: any = {}) => ({
  id: "0xa-jet", class: "BP_CommandActor_FA18_Strafe_C", team: 1,
  action: "CommandAction_FA18CASStrafe_C", callerEosId: RUBY,
  position: { x: 0, y: 0, z: 6000 }, yaw: 0, actionDestroyed: false,
  shotsMade: 40, maxShots: 120, ...o,
}) as any;

const round = (id: string, o: any = {}) => ({
  id, classShort: "BP_Projectile_155mm_C", hasImpacted: false,
  isTracer: false, isExplosive: true,
  explosiveBaseDamage: 250, explosiveKillZoneRadius: 500,
  firer: "Ruby", team: 1, position: { x: 500, y: 500, z: 60 }, ...o,
}) as any;

const frame = (t: number, o: any = {}): any => ({
  timestamp: new Date(1700000000000 + t * 1000).toISOString(),
  gameState: { worldTimeSec: t }, teams: [], players: PLAYERS,
  markers: [], vehicles: [], damageEvents: [],
  commandActions: [], projectiles: [], ...o,
});

// --- the caller reaches a round only through the roster ----------------------
{
  eq(callerFirerName(GUNS(), PLAYERS), "Ruby",
     "the caller's EOS id becomes the name a round carries");
  eq(callerFirerName(GUNS({ callerEosId: "eos-ghost" }), PLAYERS), null,
     "a caller off the frame's roster names no rounds at all");
  eq(callerFirerName(GUNS({ callerEosId: null }), PLAYERS), null,
     "and an actor with no caller names none either");
  eq(callerFirerName(GUNS(), []), null, "nor does an empty roster");
}

// --- what belongs to a call --------------------------------------------------
{
  eq(roundMatchesCall(round("0x1"), GUNS(), "Ruby"), true,
     "the caller's own shell, of the class the plan names");
  eq(roundMatchesCall(round("0x1", { firer: "Sol" }), GUNS(), "Ruby"), false,
     "somebody else's round is somebody else's");
  eq(roundMatchesCall(round("0x1", { firer: null }), GUNS(), "Ruby"), false,
     "a round whose instigator did not resolve joins nothing");
  eq(roundMatchesCall(
       round("0x1", { classShort: "BP_Projectile_Mortar_C" }),
       GUNS(), "Ruby"), false,
     "and a class the plan does not name is not this plan's shell");
  // The strike families declare no `projectile`, so there is no recorded
  // class to filter on and the firer is the whole join.
  eq(roundMatchesCall(
       round("0x1", { classShort: "BP_Projectile_Rocket_S5_C" }),
       JET(), "Ruby"), true,
     "a strike's rounds are the caller's rounds, whatever class they are");
  eq(roundMatchesCall(round("0x1"), GUNS(), null), false,
     "with no name resolved, nothing joins");
}

// --- one frame's rounds, mapped to their calls -------------------------------
{
  const snap = frame(100, {
    commandActions: [GUNS(), JET()],
    projectiles: [
      round("0x1"),                                   // Ruby's 155
      round("0x2", { firer: "Sol" }),                  // nobody's call
      round("0x3", { classShort: "BP_Projectile_Rocket_S5_C" }),
    ],
  });
  const m = roundsByCall(snap);
  eq(m.get("0x1")?.id, "0xa-guns",
     "the shell of the class the guns name goes to the guns");
  eq(m.has("0x2"), false, "another player's round joins no call");
  eq(m.get("0x3")?.id, "0xa-jet",
     "and the rocket, which no plan names, goes to the strike");
  eq(roundsByCall(frame(100)).size, 0, "no calls, no joins");
  eq(roundsByCall(null).size, 0, "and no frame, no joins");
}
{
  // Two of the commander's calls could claim the same shell. The one that
  // NAMED the class is the more specific claim and keeps it.
  const snap = frame(100, {
    commandActions: [JET(), GUNS()],
    projectiles: [round("0x1")],
  });
  eq(roundsByCall(snap).get("0x1")?.id, "0xa-guns",
     "a named class beats a bare firer match");
}

// --- the impacts, over a recording -------------------------------------------
{
  const air = (id: string) => round(id, { hasImpacted: false });
  const rest = (id: string, x: number, o: any = {}) =>
    round(id, { hasImpacted: true, position: { x, y: 500, z: 60 }, ...o });
  const guns = (barrage: number) => GUNS({ currentBarrage: barrage });
  const frames = [
    // Before the guns open: a shell in the air, nothing landed.
    frame(100, { commandActions: [guns(0)], projectiles: [air("0x1")] }),
    // The first warning shell lands while the barrage counter is still 0.
    frame(101, { commandActions: [guns(0)], projectiles: [rest("0x1", 700)] }),
    // ...and lingers, with the flag still set. Same landing, not another.
    frame(102, { commandActions: [guns(0)],
                 projectiles: [rest("0x1", 700), air("0x2")] }),
    // Barrage 1: two shells land.
    frame(103, { commandActions: [guns(1)],
                 projectiles: [rest("0x2", 900), rest("0x3", 1100)] }),
    // Barrage 2: one more, and one round of somebody else's that must not
    // be counted.
    frame(104, { commandActions: [guns(2)],
                 projectiles: [rest("0x4", 1300),
                               rest("0x9", 9999, { firer: "Sol" })] }),
  ];
  const ci = buildCallImpacts(frames, "0xa-guns")!;
  ok(ci, "the call has impacts to report");
  eq(ci.events.length, 4, "four rounds landed, the lingering one counted once");
  eq(ci.events[0]!.id, "0x1", "the first is the warning shell");
  eq(ci.events[0]!.gameTime, 101,
     "dated on the frame it first read hasImpacted, not on the linger");
  eq(ci.events[0]!.frameIdx, 1, "and indexed there too");
  eq(ci.events[0]!.position.x, 700, "at the rest position that frame carried");
  eq(ci.events[0]!.barrage, 0,
     "landing during barrage 0 — the warning phase, read not derived");
  eq(ci.events[1]!.barrage, 1, "the next two land during barrage 1");
  eq(ci.events[3]!.barrage, 2, "and the fourth during barrage 2");
  eq(ci.shellsPerBarrage, 10, "with the plan's own shells-per-barrage");
  eq(ci.barrageCount, 8, "and its barrage count");
  ok(!ci.events.some((e) => e.id === "0x9"),
     "another player's round is not this call's, wherever it fell");

  // The count at the playhead.
  eq(landedBy(ci.events).landed, 4, "four in the whole call");
  eq(landedBy(ci.events, 102).landed, 1, "one by the second 101 frame");
  eq(landedBy(ci.events, 103).landed, 3, "three once barrage 1 has landed");
  const per = landedBy(ci.events, 104).perBarrage;
  eq(per.length, 3, "three barrages have put rounds down");
  eq(per[0]!.barrage, 0, "the warning phase first");
  eq(per[0]!.landed, 1, "one warning shell");
  eq(per[1]!.landed, 2, "two in barrage 1");
  eq(per[2]!.landed, 1, "and one so far in barrage 2");
  eq(landedBy([], 104).landed, 0, "no rounds, no count");
}
{
  // A round that lands after the actor is gone belongs to no frame of the
  // call's life, and the call does not claim it.
  const frames = [
    frame(100, { commandActions: [GUNS()],
                 projectiles: [round("0x1")] }),
    frame(101, { projectiles: [round("0x1", { hasImpacted: true })] }),
  ];
  const ci = buildCallImpacts(frames, "0xa-guns")!;
  eq(ci.events.length, 0,
     "the actor's life is the window, and this one landed outside it");
}
{
  eq(buildCallImpacts([], "0xa-guns"), null, "no frames, no call");
  eq(buildCallImpacts([frame(1)], "0xa-guns"), null,
     "and an actor no frame carries is not a call to report on");
}
{
  // A caller off the roster: the join has nothing to go through, so the
  // call lands nothing rather than claiming every round in the air.
  const frames = [
    frame(100, { players: [],
                 commandActions: [GUNS()],
                 projectiles: [round("0x1", { hasImpacted: true })] }),
  ];
  eq(buildCallImpacts(frames, "0xa-guns")!.events.length, 0,
     "no name to join on, no rounds joined");
}

console.log(`commander impacts: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
