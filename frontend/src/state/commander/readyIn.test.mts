// The "ready in" arithmetic — the one thing about the commander's cooldowns
// the recorder deliberately does not compute, so that a wrong rule is wrong
// for exactly as long as it takes to fix it here.
//
// Every number below is the spec's own: the UAV's 30 / 300 / 600 and the
// server's 300 s new-commander extension (§3, §4).
import {
  actionDisplayName, actionReadiness, actionReadyGameTime, categoryFor,
  categoryReadyGameTime, effectiveDurationSec, findActionEntry,
  reclaimReadyGameTime,
} from "./readyIn.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}
function eq(a: any, b: any, msg: string) {
  ok(a === b, `${msg} (got ${JSON.stringify(a)}, want ${JSON.stringify(b)})`);
}

const UAV = (o: any = {}) => ({
  action: "CommandAction_UAV_MQ9_USMC_C", displayName: "MQ-9 UAV Recon",
  createdGameTime: 843.6, remainingAtChange: 0, destroyedDuringActive: false,
  categoryId: 0, enrouteSec: 30, activeSec: 300, cooldownSec: 600, ...o,
}) as any;
const CATEGORY = (o: any = {}) => ({
  id: 0, name: "Air", intervalSec: 600, lastUseGameTime: null, ...o,
}) as any;

// --- the base arithmetic -----------------------------------------------------
eq(effectiveDurationSec(UAV()), 930, "enroute + active + cooldown");
eq(actionReadyGameTime(UAV()), 843.6 + 930,
   "ready when the whole window has run from the call");
eq(effectiveDurationSec(UAV({ cooldownSec: undefined })), null,
   "two thirds of a duration is not a duration");
eq(actionReadyGameTime(UAV({ createdGameTime: undefined })), null,
   "and a window with no stamp starts nowhere");
eq(actionReadyGameTime(null), null, "nor does an entry that is not there");

// --- the match's first claim: every entry on its full cooldown ---------------
{
  // Entries appear at the first claim BACK-DATED by enroute plus active, so
  // each asset starts with its own cooldown to run and nothing else.
  const claim = 1000;
  const e = UAV({ createdGameTime: claim - (30 + 300) });
  eq(actionReadyGameTime(e), claim + 600,
     "a back-dated entry is ready one cooldown after the claim");
  const r = actionReadiness(e, [CATEGORY()], claim);
  eq(r.readyInSec, 600, "which is what the countdown says at the claim");
  eq(r.ready, false, "and it is not callable yet");
}

// --- a call ------------------------------------------------------------------
{
  // A call rewrites `createdGameTime`, so the whole window starts again.
  const called = UAV({ createdGameTime: 2000 });
  const r = actionReadiness(called, [CATEGORY()], 2000);
  eq(r.readyInSec, 930, "the call restarts enroute, active and cooldown");
  eq(actionReadiness(called, [CATEGORY()], 2930).ready, true,
     "and it is callable again when the window has run");
}

// --- destroyed mid-flight: the stamps do NOT move ---------------------------
{
  // A UAV and a drone destroyed on 2026-09-07 kept their `createdGameTime`,
  // and the game's own UI matched the unchanged arithmetic rather than
  // destruction + cooldown. The 09-02 reading of a restart was not
  // reproduced and is not applied.
  const shot = UAV({ createdGameTime: 2000, destroyedDuringActive: true });
  eq(actionReadyGameTime(shot), 2930,
     "a shoot-down does not restart the cooldown");
}

// --- a commander change ------------------------------------------------------
{
  // The GAME moves the stamp: an entry still cooling has `createdGameTime`
  // pushed forward by the extension and `remainingAtChange` written with the
  // time it had left. The viewer reads the moved stamp, so the base
  // arithmetic is still the whole answer.
  const change = 2500;
  const before = UAV({ createdGameTime: 2000 });        // ready at 2930
  const after = UAV({ createdGameTime: 2000 + 300, remainingAtChange: 430 });
  eq(actionReadyGameTime(before), 2930, "before the change");
  eq(actionReadyGameTime(after), 3230,
     "after it, later by the 300 s extension");
  eq(actionReadiness(after, [CATEGORY()], change).readyInSec, 730,
     "and the countdown follows the stamp with no special case");
  // An entry whose own cooldown had already run is re-stamped to become
  // ready `newCommanderExtensionSec` after the change.
  const wasReady = UAV({ createdGameTime: change + 300 - 930 });
  eq(actionReadyGameTime(wasReady), change + 300,
     "a ready entry comes back 300 s after the new commander sat down");
}

// --- a step-down, then a re-claim -------------------------------------------
{
  // A step-down writes `remainingAtChange` and moves nothing else; at the
  // next claim the entry is ready at claim + min(left + extension, cooldown).
  const steppedDown = UAV({ remainingAtChange: 120 });
  eq(reclaimReadyGameTime(steppedDown, 5000, 300), 5000 + 420,
     "120 s left plus the 300 s extension, both under the cooldown");
  const nearlyFull = UAV({ remainingAtChange: 500 });
  eq(reclaimReadyGameTime(nearlyFull, 5000, 300), 5000 + 600,
     "and the cooldown is the ceiling: 800 clamps to 600");
  const fresh = UAV({ remainingAtChange: 0 });
  eq(reclaimReadyGameTime(fresh, 5000, 300), 5000 + 600,
     "an entry with nothing left starts on its full cooldown");
  eq(reclaimReadyGameTime(steppedDown, 5000, null), null,
     "without the server's extension the projection is refused");
  eq(reclaimReadyGameTime(UAV({ cooldownSec: undefined }), 5000, 300), null,
     "and so is one with no cooldown to clamp against");
}

// --- the category gate -------------------------------------------------------
{
  const never = CATEGORY();
  eq(categoryReadyGameTime(never), null,
     "a category nothing has called gates nothing");
  const used = CATEGORY({ lastUseGameTime: 3000 });
  eq(categoryReadyGameTime(used), 3600, "last use plus the interval");
  eq(categoryFor(UAV(), [used])!.id, 0, "an action finds its own category");
  eq(categoryFor(UAV({ categoryId: 7 }), [used]), null,
     "and none when the index names no category");
}
{
  // The gate holds across everything else: a strike really was refused with
  // 15:00 on it after an artillery call, and a commander change does not
  // touch the category stamps.
  const e = UAV({ createdGameTime: 1000 });      // own cooldown ends at 1930
  const cat = CATEGORY({ lastUseGameTime: 2000, intervalSec: 900 });
  const r = actionReadiness(e, [cat], 1950);
  eq(r.readyGameTime, 2900, "the later of the two gates wins");
  eq(r.gatedBy, "category", "and the UI can say which one");
  eq(r.readyInSec, 950, "counting down to it");
  eq(r.ready, false, "the entry's own cooldown having run is not enough");
}
{
  const e = UAV({ createdGameTime: 3000 });      // ends at 3930
  const cat = CATEGORY({ lastUseGameTime: 3000, intervalSec: 600 });
  const r = actionReadiness(e, [cat], 3000);
  eq(r.readyGameTime, 3930, "an early category gate does not shorten anything");
  eq(r.gatedBy, "action", "the action's own window is the binding one");
}
{
  const r = actionReadiness(UAV({ enrouteSec: undefined }), [CATEGORY()], 1);
  eq(r.readyGameTime, null, "an unreadable entry produces no ready time");
  eq(r.ready, null, "and no hopeful false");
  eq(actionReadiness(UAV(), [CATEGORY()], null).readyInSec, null,
     "nor does a frame with no clock");
}

// --- the joins ---------------------------------------------------------------
{
  const teams: any[] = [
    { id: 1, commander: { cooldowns: { actions: [UAV()] } } },
    { id: 2, commander: { cooldowns: { actions: [
      UAV({ action: "CommandAction_Mortar_Barrage_INS_C",
            displayName: "Heavy Mortar Barrage" })] } } },
  ];
  eq(findActionEntry("CommandAction_UAV_MQ9_USMC_C", teams)!.displayName,
     "MQ-9 UAV Recon", "a footprint finds the entry that produced it");
  eq(actionDisplayName("CommandAction_Mortar_Barrage_INS_C", teams),
     "Heavy Mortar Barrage", "and the config's own display text with it");
  eq(actionDisplayName("CommandAction_UAV_MQ9_USMC_C", teams, 2), null,
     "narrowed to a team, the other team's list is not searched");
  eq(actionDisplayName("CommandAction_Nothing_C", teams), null,
     "an unknown action gets no label rather than a guessed one");
  eq(actionDisplayName(null, teams), null, "and neither does a null pointer");
  eq(findActionEntry("CommandAction_UAV_MQ9_USMC_C", null), null,
     "no teams, no entry");
}

console.log(`commander readyIn: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
