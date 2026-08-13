// A Direction marker is a stroke, not a point. Getting that wrong is not a
// visual nit: with no geometry the viewer fell back to the generic infantry
// glyph, so an order dragged across half the map was drawn as one soldier
// standing where the drag began — indistinguishable from a spotted enemy.
// Framework-free, same as the tests beside it.
import { arrowEnd, markerShape } from "./markerGeometry.ts";

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

console.log(`markerGeometry: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
