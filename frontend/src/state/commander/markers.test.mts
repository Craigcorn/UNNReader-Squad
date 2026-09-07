// The marker half of the commander rules: which request a marker is, when
// two markers are one request, and when two markers are one placement.
// Framework-free, same as the tests beside it.
import {
  dedupeMarkers, isActorMarker, markerFamily, requestPhase, requestsOnMap,
  REQUEST_CIRCLE_CM, SAME_PLACE_CM,
} from "./markers.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}
function eq(a: any, b: any, msg: string) {
  ok(a === b, `${msg} (got ${JSON.stringify(a)}, want ${JSON.stringify(b)})`);
}

const mk = (o: any = {}) => ({
  id: "0x1", type: null, team: 1, squad: 3, fireTeamId: -1,
  ownerPlayerStateAddr: null, position: { x: 0, y: 0, z: 0 }, ...o,
}) as any;

// --- which request -----------------------------------------------------------
eq(requestPhase("BP_MapMarker_Command_SLRequest_C"), "pending",
   "the SL's marker is the pending request");
eq(requestPhase("BP_MapMarker_Command_Request_C"), "approved",
   "the commander's is the approved one");
eq(requestPhase("BP_MapMarker_CommandRadius_Friendly_C"), null,
   "a footprint is not a request");
eq(requestPhase(null), null, "and neither is a marker with no class name");
eq(REQUEST_CIRCLE_CM, 5000, "the request circle is 50 m, in centimetres");

// --- an approval and its pending twin are ONE request ------------------------
{
  // The server sweeps markers every ~61 s, so for up to a minute after an
  // approval BOTH markers are really on the map. Drawing two says two squads
  // asked for two things.
  const pending = mk({ id: "0xp", type: "BP_MapMarker_Command_SLRequest_C" });
  const approved = mk({ id: "0xa", type: "BP_MapMarker_Command_Request_C" });
  const one = requestsOnMap([pending, approved]);
  eq(one.length, 1, "the pair is one request");
  eq(one[0]!.phase, "approved", "and it is the approved state that shows");
  eq(one[0]!.marker.id, "0xa", "the approved marker is the one to draw");
  eq(one[0]!.pendingId, "0xp", "the pending twin is still named");
  eq(one[0]!.approvedId, "0xa", "beside its approved half");
  // Order must not matter — the marker list is in walk order, not game order.
  const other = requestsOnMap([approved, pending]);
  eq(other.length, 1, "the pairing does not depend on list order");
  eq(other[0]!.phase, "approved", "nor does the state it reads");
}
{
  const alone = requestsOnMap([
    mk({ id: "0xp", type: "BP_MapMarker_Command_SLRequest_C" })]);
  eq(alone.length, 1, "a lone pending request is a request");
  eq(alone[0]!.phase, "pending", "still waiting");
  eq(alone[0]!.approvedId, null, "with no approved half");
}
{
  // Two squads asking from the same spot are two requests: the squad is part
  // of the identity, not just the position.
  const two = requestsOnMap([
    mk({ id: "0xa", squad: 1, type: "BP_MapMarker_Command_SLRequest_C" }),
    mk({ id: "0xb", squad: 2, type: "BP_MapMarker_Command_SLRequest_C" }),
  ]);
  eq(two.length, 2, "different squads are different requests");
}
{
  // And one squad's two requests, far apart, stay apart.
  const two = requestsOnMap([
    mk({ id: "0xa", type: "BP_MapMarker_Command_SLRequest_C" }),
    mk({ id: "0xb", type: "BP_MapMarker_Command_SLRequest_C",
         position: { x: SAME_PLACE_CM * 10, y: 0, z: 0 } }),
  ]);
  eq(two.length, 2, "two placements apart are two requests");
}

// --- one placement, one shape ------------------------------------------------
eq(isActorMarker(mk({ id: "0x707db0c584a0" })), true,
   "an actor marker's id is its address");
eq(isActorMarker(mk({ id: "412" })), false,
   "a squad-data entry's id is its replication id");
eq(markerFamily("BP_MapMarker_FriendlyDirector_C"), "friendlydirector",
   "the family drops the path's prefix and the Blueprint suffix");
eq(markerFamily("DA_MapMarker_FriendlyDirector"), "friendlydirector",
   "so the two paths' names meet");
eq(markerFamily("BP_MapMarker_Action_MoveSL"), "actionmove",
   "and the SL/FT role tail comes off too");

{
  // A Director is two markers to the game: the squad-data one carrying the
  // drag the SL made, and the team actor one carrying `Distance`. Before
  // this rule the viewer drew an arrow AND a point at the same spot.
  const dragged = mk({ id: "77", type: "DA_MapMarker_FriendlyDirector",
                       arrowLength: 14391, arrowHeading: 30 });
  const actor = mk({ id: "0xdir", type: "BP_MapMarker_FriendlyDirector_C",
                     distance: 14391, yaw: 30 });
  const kept = dedupeMarkers([dragged, actor]);
  eq(kept.length, 1, "one placement, one shape");
  eq(kept[0]!.id, "77",
     "and it is the drag the viewer already draws, not the point");
}
{
  // The request markers are the other way round: neither twin has a shaft,
  // and the actor marker's class name is what says pending or approved, so
  // the actor half must survive.
  const dataTwin = mk({ id: "88", type: "DA_MapMarker_Command_SLRequest",
                        arrowLength: 0, arrowHeading: 0 });
  const actor = mk({ id: "0xreq", type: "BP_MapMarker_Command_SLRequest_C",
                     distance: 0, addDistance: 0, action: null });
  const kept = dedupeMarkers([dataTwin, actor]);
  eq(kept.length, 1, "the request's two markers draw one thing");
  eq(kept[0]!.id, "0xreq", "and it is the one whose class name is readable");
}
{
  // Different families, same spot: two different orders, both drawn.
  const a = mk({ id: "0xa", type: "BP_MapMarker_Action_AttackSL" });
  const b = mk({ id: "0xb", type: "BP_MapMarker_Action_DefendSL" });
  eq(dedupeMarkers([a, b]).length, 2,
     "two different orders at one spot are two orders");
}
{
  // Same family, same squad, metres apart: two placements.
  const a = mk({ id: "0xa", type: "BP_MapMarker_POI" });
  const b = mk({ id: "0xb", type: "BP_MapMarker_POI",
                 position: { x: SAME_PLACE_CM * 4, y: 0, z: 0 } });
  eq(dedupeMarkers([a, b]).length, 2, "apart is apart");
}
{
  // Nothing without a position or a name is ever dropped: there is nothing
  // to match it on, and losing a real object is worse than drawing it twice.
  const a = mk({ id: "0xa", type: null, position: null });
  const b = mk({ id: "0xb", type: null, position: null });
  eq(dedupeMarkers([a, b]).length, 2, "unmatched markers are kept, both");
  eq(dedupeMarkers([]).length, 0, "an empty list stays empty");
  eq(dedupeMarkers(undefined).length, 0, "and so does no list at all");
}
{
  // The overwhelmingly common case: nothing to merge, order preserved.
  const list = [mk({ id: "0xa", type: "BP_MapMarker_POI" }),
                mk({ id: "0xb", type: "BP_MapMarker_Action_MoveSL",
                     position: { x: 90000, y: 0, z: 0 } })];
  const kept = dedupeMarkers(list);
  eq(kept.length, 2, "an ordinary frame loses nothing");
  eq(kept[0]!.id, "0xa", "and keeps its order");
}

console.log(`commander markers: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
