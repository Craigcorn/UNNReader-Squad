// A command actor's own state: shot down or not, and where in its fire plan.
// The numbers are the spec's — the 08-30 creep (2 warning shells, 12 s
// delay, 60 s enroute) and the 2026-09-07 mortar (no warning phase, 10
// shells a barrage, 8 barrages).
import {
  artilleryPhase, artilleryTimeline, entryDestroyed, isArtillery, isShotDown,
} from "./assets.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}
function eq(a: any, b: any, msg: string) {
  ok(a === b, `${msg} (got ${JSON.stringify(a)}, want ${JSON.stringify(b)})`);
}

const CREEP = (o: any = {}) => ({
  id: "0xc", class: "BP_CommandActor_Artillery_Creep_C", team: 1,
  action: "CommandAction_Artillery_Creep_USMC_C",
  position: { x: 0, y: 0, z: 0 }, yaw: 0, actionDestroyed: false,
  distance: 45000,
  originLocation: { x: 0, y: 0, z: 0 }, targetLocation: { x: 45000, y: 0, z: 0 },
  maxDropRadius: 1500, preWarningShells: 2, preWarningDelaySec: 12,
  shellsPerBarrage: 10, barrageCount: 8,
  currentPrewarningShells: 0, currentBarrage: 0,
  projectile: "BP_Projectile_155mm_C", ...o,
}) as any;
const MORTAR = (o: any = {}) => ({
  ...CREEP(), id: "0xm", class: "BP_CommandActor_Mortar_Radius_C",
  action: "CommandAction_Mortar_Barrage_INS_C", distance: 7500,
  maxDropRadius: 1.0, preWarningShells: 0, preWarningDelaySec: 0,
  shellsPerBarrage: 10, barrageCount: 8, ...o,
}) as any;
const UAV = (o: any = {}) => ({
  id: "0xu", class: "BP_CommandActor_UAV_MQ9_C", team: 1,
  action: "CommandAction_UAV_MQ9_USMC_C",
  position: { x: 0, y: 0, z: 9000 }, yaw: 0, actionDestroyed: false,
  distance: 0, ...o,
}) as any;
const entry = (o: any = {}) => ({
  action: "CommandAction_Artillery_Creep_USMC_C", createdGameTime: 1000,
  enrouteSec: 60, activeSec: 300, cooldownSec: 900, categoryId: 1, ...o,
}) as any;

// --- shoot-downs -------------------------------------------------------------
eq(isShotDown(UAV()), false, "an actor on station was not shot down");
eq(isShotDown(UAV({ actionDestroyed: true })), true,
   "the flag reads true only while a shot-down actor lingers");
eq(isShotDown(null), false, "and nothing is not a shoot-down");
eq(entryDestroyed({ destroyedDuringActive: true } as any), true,
   "the team entry says the same from the other side");
eq(entryDestroyed({} as any), false,
   "an entry that never read the flag is not a kill");
// A natural end removes the actor without any frame reading the flag, so
// "gone" is not "destroyed" and the viewer must never read it as one.
eq(isShotDown(undefined), false, "an actor that simply ended is not a kill");

// --- which actors have a fire plan ------------------------------------------
eq(isArtillery(CREEP()), true, "the creep carries a plan");
eq(isArtillery(MORTAR()), true, "and so does the mortar");
eq(isArtillery(UAV()), false, "the UAV carries none, and is not asked by name");

// --- the creep's timeline ----------------------------------------------------
{
  const t = artilleryTimeline(CREEP(), entry());
  eq(t.gunsOpenGameTime, 1060, "the guns open one enroute after the call");
  eq(t.hasWarningPhase, true, "the creep warns first");
  eq(t.warningShellsTotal, 2, "with two shells");
  eq(t.warningComplete, false, "none fired yet");
  eq(artilleryPhase(t, 1030), "enroute", "and until then it is enroute");
  eq(artilleryPhase(t, 1061), "warning",
     "past the enroute the warning phase has begun");
}
{
  // The counter went 0 → 1 at +59.7 s and 2 at +67.1 s; `Current Barrage`
  // reached 1 at +79.2 s, twelve seconds after the second warning shell.
  const warned = artilleryTimeline(
    CREEP({ currentPrewarningShells: 2 }), entry(), 1067.1);
  eq(warned.warningComplete, true, "both warning shells are away");
  eq(warned.mainBarrageOpensGameTime, 1079.1,
     "and the main barrage opens the 12 s delay after the last one");
  eq(artilleryPhase(warned, 1070), "warning",
     "the delay after the warning shells is still the warning phase");
  const firing = artilleryTimeline(
    CREEP({ currentPrewarningShells: 2, currentBarrage: 3 }), entry(), 1067.1);
  eq(artilleryPhase(firing, 1100), "barrage", "then the barrage is on");
  eq(firing.currentBarrage, 3, "reading the count, never predicting it");
  eq(firing.barrageCount, 8, "against the plan's own total");
}

// --- the mortar has no warning phase ----------------------------------------
{
  const e = entry({ action: "CommandAction_Mortar_Barrage_INS_C",
                    createdGameTime: 2000, enrouteSec: 30 });
  const t = artilleryTimeline(MORTAR(), e);
  eq(t.hasWarningPhase, false, "no warning shells: no warning phase");
  eq(t.gunsOpenGameTime, 2030, "the guns open at the enroute");
  eq(artilleryPhase(t, 2010), "enroute", "before which it is enroute");
  // Its counter and its barrage BOTH reach 1 at the enroute.
  const open = artilleryTimeline(
    MORTAR({ currentPrewarningShells: 1, currentBarrage: 1 }), e);
  eq(artilleryPhase(open, 2031), "barrage",
     "and at the enroute the first barrage is already away");
  eq(open.mainBarrageOpensGameTime, null,
     "with no warning phase there is no delayed opening to name");
}

// --- what the frame does not say --------------------------------------------
{
  // Past the enroute, no warning phase, no barrage recorded yet: nothing in
  // the frame says which the guns are in, so nothing is said.
  const e = entry({ enrouteSec: 30, createdGameTime: 2000 });
  const t = artilleryTimeline(MORTAR({ currentBarrage: 0 }), e);
  eq(artilleryPhase(t, 2031), null, "an unnamed moment stays unnamed");
}
{
  // No cooldown entry to join — the call's own stamp is on the team record,
  // not on the actor, so without it there is no timeline to build.
  const t = artilleryTimeline(CREEP(), null);
  eq(t.gunsOpenGameTime, null, "no entry, no enroute, no opening time");
  eq(t.currentBarrage, 0, "but the actor's own progress is still read");
  eq(artilleryPhase(t, 9999), "warning",
     "and the warning phase it declares is still the phase");
}
{
  const t = artilleryTimeline({ id: "0x1", class: null } as any, entry());
  eq(t.hasWarningPhase, null, "an actor with no plan fields declares nothing");
  eq(t.warningComplete, null, "and completes nothing");
  eq(artilleryPhase(t, 2000), null, "which is not a phase either");
}

console.log(`commander assets: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
