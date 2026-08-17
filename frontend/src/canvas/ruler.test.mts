// The number this puts on screen is the whole feature, so it is the thing
// worth testing. Framework-free, like the tests beside it.
import { metresBetween, formatMetres, bearingDegrees, pathMetres,
         legLabel } from "./ruler.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}
function near(a: number, b: number, tol: number, msg: string) {
  ok(Math.abs(a - b) <= tol, `${msg} (got ${a}, want ~${b})`);
}

// --- distance ---------------------------------------------------------------
{
  // Squad's world unit is the centimetre: 100 000 units is a kilometre.
  near(metresBetween({ x: 0, y: 0 }, { x: 100000, y: 0 }), 1000, 1e-9,
       "100 000 units east is 1000 m");
  near(metresBetween({ x: 0, y: 0 }, { x: 30000, y: 40000 }), 500, 1e-9,
       "a 3-4-5 triangle measures its hypotenuse");
  near(metresBetween({ x: -12345, y: 6789 }, { x: -12345, y: 6789 }), 0, 1e-9,
       "a point measured against itself is zero");
  // Order cannot matter, or dragging backwards would read differently.
  const a = { x: -100221, y: 15909 }, b = { x: 4180, y: -32607 };
  near(metresBetween(a, b), metresBetween(b, a), 1e-9,
       "measuring the other way round gives the same number");
}

// --- height is deliberately ignored -----------------------------------------
{
  // Two points either side of a valley are not closer for sharing an
  // altitude, and a keypad grid is flat. Passing a z must change nothing.
  const flat = metresBetween({ x: 0, y: 0 }, { x: 30000, y: 40000 });
  const withZ = metresBetween({ x: 0, y: 0, z: -9999 } as never,
                              { x: 30000, y: 40000, z: 50000 } as never);
  near(withZ, flat, 1e-9, "a z component is not part of a map distance");
}

// --- the label --------------------------------------------------------------
{
  ok(formatMetres(0) === "0 m", "zero reads as 0 m");
  ok(formatMetres(846.5) === "847 m" || formatMetres(846.5) === "846 m",
     `sub-metre precision is rounded away (got ${formatMetres(846.5)})`);
  ok(formatMetres(212.4) === "212 m", "a normal range is whole metres");
  // Always metres — Squad's grid is 300 m keypads and ranges are called out
  // in metres, so kilometres would have to be converted back in the head.
  ok(formatMetres(1240) === "1 240 m", `thousands are grouped, not converted `
     + `to km (got ${formatMetres(1240)})`);
  ok(!formatMetres(4200).includes("km"), "never kilometres");
}

// --- bearing ----------------------------------------------------------------
{
  // World +Y runs south on this map, so north is -Y.
  near(bearingDegrees({ x: 0, y: 0 }, { x: 0, y: -100 }), 0, 1e-6, "north is 0");
  near(bearingDegrees({ x: 0, y: 0 }, { x: 100, y: 0 }), 90, 1e-6, "east is 90");
  near(bearingDegrees({ x: 0, y: 0 }, { x: 0, y: 100 }), 180, 1e-6, "south is 180");
  near(bearingDegrees({ x: 0, y: 0 }, { x: -100, y: 0 }), 270, 1e-6, "west is 270");
  ok(bearingDegrees({ x: 0, y: 0 }, { x: -1, y: -100 }) < 360,
     "a bearing never reaches 360");
  ok(bearingDegrees({ x: 0, y: 0 }, { x: -1, y: -100 }) >= 0,
     "a bearing is never negative");
}

// --- a path, not just a pair ------------------------------------------------
{
  // A route round a hill is several legs and the total is the answer.
  const pts = [{ x: 0, y: 0 }, { x: 30000, y: 40000 }, { x: 30000, y: 140000 }];
  near(pathMetres(pts), 500 + 1000, 1e-9, "legs add up");
  near(pathMetres([{ x: 0, y: 0 }]), 0, 1e-9, "one point has no length");
  near(pathMetres([]), 0, 1e-9, "an empty path has no length");
  // Adding a point you are already standing on must not change the total.
  const same = [...pts, { x: 30000, y: 140000 }];
  near(pathMetres(same), pathMetres(pts), 1e-9,
       "a zero-length leg adds nothing");
}

// --- the label a leg carries ------------------------------------------------
{
  const l = legLabel({ x: 0, y: 0 }, { x: 0, y: -100000 });
  ok(l.includes("1 000 m"), `leg label carries the distance (got ${l})`);
  ok(l.includes("0°"), `leg label carries the bearing (got ${l})`);
  const east = legLabel({ x: 0, y: 0 }, { x: 100000, y: 0 });
  ok(east.includes("90°"), `east reads 90 degrees (got ${east})`);
  // 360 must never appear — it is the same direction as 0 and reads as a bug.
  const almost = legLabel({ x: 0, y: 0 }, { x: -1, y: -100000 });
  ok(!almost.includes("360"), `a bearing never prints 360 (got ${almost})`);
}

console.log(`ruler: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
