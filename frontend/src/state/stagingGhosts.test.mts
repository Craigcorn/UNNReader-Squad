// The staging-ghost strip has to be surgical: exact world origin and
// unmanned, nothing else. A vehicle someone is sitting in must survive no
// matter where it is, and a clean snapshot must come back by REFERENCE so
// the per-frame ingest path costs a scan, not an allocation.
import { isStagingGhost, stripStagingGhosts } from "./stagingGhosts.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}

const veh = (over: any = {}) => ({
  id: "0x1", classShort: "BP_ZiS3_Base_C", team: 0, health: 500,
  maxHealth: 800, position: { x: 0, y: 0, z: 0 }, yaw: 0, attached: false,
  ...over,
});
const snap = (vehicles: any[]) => ({ tick: 1, vehicles } as any);

// --- the predicate ---------------------------------------------------------
ok(isStagingGhost(veh()), "origin-parked unmanned actor is a ghost");
ok(isStagingGhost(veh({ seats: [] })), "empty seat array is still a ghost");
ok(isStagingGhost(veh({ seats: [{ idx: 0, occupantName: null }] })),
   "unoccupied seats are still a ghost");
ok(!isStagingGhost(veh({ seats: [{ idx: 0, occupantName: "Crayon" }] })),
   "an occupied actor is kept even at origin");
ok(!isStagingGhost(veh({ position: { x: 1234.5, y: -20, z: 88 } })),
   "a placed vehicle is not a ghost");
ok(!isStagingGhost(veh({ position: { x: 0, y: 0, z: 150 } })),
   "origin XY but real height is not a ghost");
ok(!isStagingGhost(veh({ position: null })),
   "missing position is not this filter's business");
ok(!isStagingGhost(veh({ position: { x: 0, y: 0, z: null } })),
   "a reconstructed frame's null z is not treated as zero");

// --- the strip -------------------------------------------------------------
{
  const real = veh({ id: "0x2", position: { x: 500, y: 500, z: 10 } });
  const s = snap([veh(), real]);
  const out = stripStagingGhosts(s);
  ok(out.vehicles.length === 1 && out.vehicles[0] === real,
     "ghosts are stripped, real vehicles survive by reference");
  ok(out !== s, "a stripped snapshot is a new object");
}
{
  const s = snap([veh({ position: { x: 500, y: 500, z: 10 } })]);
  ok(stripStagingGhosts(s) === s,
     "a clean snapshot passes through by reference");
}
{
  const s = { tick: 1 } as any;
  ok(stripStagingGhosts(s) === s, "no vehicles array at all is left alone");
}

console.log(`stagingGhosts: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
