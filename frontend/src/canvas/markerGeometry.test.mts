// A Direction marker is a stroke, not a point. Getting that wrong is not a
// visual nit: with no geometry the viewer fell back to the generic infantry
// glyph, so an order dragged across half the map was drawn as one soldier
// standing where the drag began — indistinguishable from a spotted enemy.
// Framework-free, same as the tests beside it.
import { arrowEnd, commandFootprint, markerShape } from "./markerGeometry.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}
function near(a: number, b: number, msg: string) {
  ok(Math.abs(a - b) < 1e-6, `${msg} (got ${a}, want ${b})`);
}

const mk = (o: any) => ({
  id: "1", type: null, team: 1, squad: 1, fireTeamId: -1,
  ownerPlayerStateAddr: null, position: { x: 0, y: 0, z: 0 }, ...o,
}) as any;

// --- which shape ------------------------------------------------------------
ok(markerShape(mk({ type: "BP_MapMarker_POI" })) === "diamond",
   "a POI is a diamond, not a picture");
ok(markerShape(mk({ type: "BP_MapMarker_FriendlyDirector",
                    arrowLength: 14391, arrowHeading: 0 })) === "arrow",
   "a Director with a shaft is an arrow");
ok(markerShape(mk({ type: "FrontlineGold",
                    arrowLength: 9000, arrowHeading: 12 })) === "frontline",
   "a Frontline is a picket fence, not one arrowhead");
ok(markerShape(mk({ type: "BP_MapMarker_Action_Attack_SL" })) === null,
   "a dropped marker keeps its icon");

// --- the case that made this a regression -----------------------------------
{
  // Every replay recorded before the agent read the geometry looks like this.
  // Returning null here dropped them onto a 5px loading dot, which is worse
  // than the wrong icon they had before.
  const m = mk({ type: "BP_MapMarker_EnemyDirector" });
  ok(markerShape(m) === "point",
     "a Director with no recorded shaft is still drawn, as a point");
  ok(arrowEnd(m) === null, "and no endpoint is invented for it");
}
ok(markerShape(mk({ type: "BP_MapMarker_FriendlyDirector",
                    arrowLength: 0, arrowHeading: 0 })) === "point",
   "a zero-length shaft is not an arrow");

// --- where the arrow ends ---------------------------------------------------
{
  // UE heading is degrees about +X, and the map axes are the world axes, so
  // the endpoint is plain trigonometry in world space — projected afterwards
  // like any other position, which is what keeps it pinned to the terrain.
  const e = arrowEnd(mk({ type: "director", arrowLength: 1000,
                          arrowHeading: 0 }))!;
  near(e.x, 1000, "0 deg runs along +X");
  near(e.y, 0, "0 deg does not drift in Y");

  const n = arrowEnd(mk({ type: "director", arrowLength: 1000,
                          arrowHeading: 90 }))!;
  near(n.x, 0, "90 deg does not drift in X");
  near(n.y, 1000, "90 deg runs along +Y");

  const off = arrowEnd(mk({ type: "director", arrowLength: 200,
                            arrowHeading: 180,
                            position: { x: -32607, y: -41224, z: 0 } }))!;
  near(off.x, -32807, "the shaft starts at the marker, not at the origin");
  near(off.y, -41224, "and 180 deg is straight back along -X");
}

// --- refusing nonsense ------------------------------------------------------
ok(arrowEnd(mk({ type: "director", arrowLength: 1000,
                 arrowHeading: null })) === null,
   "a length with no heading is not half an arrow");
ok(arrowEnd(mk({ type: "director", arrowLength: 1000,
                 arrowHeading: NaN })) === null,
   "a NaN heading is refused rather than drawn at zero");
ok(arrowEnd(mk({ type: "director", arrowLength: 1000, arrowHeading: 45,
                 position: null })) === null,
   "an arrow with no start point has no end point");

// --- command-asset footprints ------------------------------------------------
// The figures are the spec's own (§5): a UAV's chosen 16608 coverage radius,
// the static barrage's 15000 with a 7500 outer band, the mortar's fixed 7500
// with 4500, a strike run's chosen 6000, the creep's 45000 path with 7500 of
// drop scatter, an aim line's 4475.
{
  const uav = commandFootprint(mk({
    type: "BP_MapMarker_CommandRadius_Friendly_C",
    distance: 16608, addDistance: 0, yaw: 45,
    action: "CommandAction_UAV_MQ9_USMC_C" }))!;
  ok(uav.kind === "circle", "a coverage marker is a circle");
  if (uav.kind === "circle") {
    near(uav.radius, 16608, "of the radius the commander chose");
    near(uav.band, 0, "with no outer band when addDistance is zero");
  }

  const barrage = commandFootprint(mk({
    type: "BP_MapMarker_CommandRadius_C", distance: 15000,
    addDistance: 7500, yaw: 0 }))!;
  ok(barrage.kind === "circle", "a static barrage is a circle too");
  if (barrage.kind === "circle") near(barrage.band, 7500, "with an outer band");

  const mortar = commandFootprint(mk({
    type: "BP_MapMarker_CommandRadius_C", distance: 7500,
    addDistance: 4500, yaw: 0 }))!;
  if (mortar.kind === "circle") {
    near(mortar.radius, 7500, "the mortar's radius is fixed");
    near(mortar.band, 4500, "and its band with it");
  }

  const run = commandFootprint(mk({
    type: "BP_MapMarker_CommandLine_C", distance: 6000, addDistance: 0,
    yaw: 0 }))!;
  ok(run.kind === "run", "a strike marker is a run");
  if (run.kind === "run") {
    near(run.endX, 6000, "along its yaw");
    near(run.endY, 0, "and nowhere else");
  }

  const creep = commandFootprint(mk({
    type: "BP_MapMarker_CommandPath_C", distance: 45000, addDistance: 7500,
    yaw: 90 }))!;
  ok(creep.kind === "path", "a creeping barrage is a path");
  if (creep.kind === "path") {
    near(creep.endY, 45000, "45000 long, the same figure its actor carries");
    near(creep.scatter, 7500, "with the drop scatter either side");
  }

  const aim = commandFootprint(mk({
    type: "BP_MapMarker_CommandLineRadius_C", distance: 4475, addDistance: 0,
    yaw: 180, position: { x: 1000, y: 2000, z: 0 } }))!;
  ok(aim.kind === "aimPoints", "a precision strike is two aim points");
  if (aim.kind === "aimPoints") {
    ok(aim.points.length === 2, "exactly two");
    near(aim.points[0]!.x, 1000, "the first at the marker");
    near(aim.points[1]!.x, 1000 - 4475, "the second `distance` along the yaw");
  }
}

// --- what draws nothing ------------------------------------------------------
{
  ok(commandFootprint(mk({ type: "BP_MapMarker_POI" })) === null,
     "a POI is no one's footprint");
  ok(commandFootprint(mk({ type: "BP_MapMarker_Command_SLRequest_C",
                           distance: 0, addDistance: 0, action: null })) === null,
     "a request has no shape of its own: its `distance` reads 0");
  ok(commandFootprint(mk({ type: "BP_MapMarker_CommandLine_C",
                           distance: 6000 })) === null,
     "a run with no bearing is refused, not drawn along zero");
  ok(commandFootprint(mk({ type: "BP_MapMarker_CommandLine_C",
                           distance: 6000, yaw: NaN })) === null,
     "and a NaN bearing with it");
  ok(commandFootprint(mk({ type: "BP_MapMarker_CommandRadius_C" })) === null,
     "a recording made before the geometry was read draws no circle");
  ok(commandFootprint(mk({ type: "BP_MapMarker_CommandRadius_C",
                           distance: 15000, position: null })) === null,
     "and a footprint with nowhere to sit is not drawn at the origin");
}

console.log(`markerGeometry: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
