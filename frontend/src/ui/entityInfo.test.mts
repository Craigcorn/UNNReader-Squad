// The elevation display encodes one calibration fact: a mortar tube RESTS
// at Squad's 800-mil minimum, so the recorded delta maps [0 .. 43.875] onto
// [800 .. 1580] mils. These pins keep that mapping from drifting.
import { emplacementElevation } from "./entityInfo.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}

ok(emplacementElevation("BP_L16mortar_Baseplate_C", 0) === "800 mils (45.0°)",
   "a mortar at rest is at Squad's 800-mil minimum, not 0°");
ok(emplacementElevation("BP_L16mortar_Baseplate_C", 43.875)
     === "1580 mils (88.9°)",
   "a mortar cranked to its stop reads Squad's 1580-mil maximum");
ok(emplacementElevation("BP_M252mortar_Baseplate_C", 22.5)
     === "1200 mils (67.5°)",
   "mid-range mortar elevation converts to the in-game mils readout");
ok(emplacementElevation("BP_EmplacedBGM71TOW_Tripod_C", 20.0) === "20°",
   "a direct-fire emplacement rests level — the delta IS the elevation");
ok(emplacementElevation("BP_EmplacedM2_Tripod_ACOG_Bunker_C", -1.8) === "-2°",
   "depression below level shows negative degrees");
ok(emplacementElevation("BP_EmplacedM2_Tripod_ACOG_Bunker_C", null) === null,
   "no recorded pitch, no elevation line");

console.log(`entityInfo: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
