// The decoder has to agree with the Python encoder exactly, and it has to SHARE
// what it did not change — that sharing is the memory saving, and a decoder
// that quietly deep-copied would satisfy every assertion about values while
// giving back none of the point. Framework-free, same as the tests beside it.
import { isPackedHeader, ReplayUnpacker } from "./replayUnpack.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}
function eqJson(a: any, b: any, msg: string) {
  ok(JSON.stringify(a) === JSON.stringify(b),
     `${msg} (got ${JSON.stringify(a)}, want ${JSON.stringify(b)})`);
}
function threw(fn: () => void, re: RegExp, msg: string) {
  try { fn(); ok(false, `${msg} (did not throw)`); }
  catch (e: any) { ok(re.test(String(e && e.message)), `${msg} (${e})`); }
}

const feed = (lines: any[]): any[] => {
  const u = new ReplayUnpacker();
  const out: any[] = [];
  for (const l of lines) { const f = u.push(l); if (f) out.push(f); }
  return out;
};

// --- rebuilding ------------------------------------------------------------
eqJson(feed([{ v: 2 },
             { tick: 1, players: { o: [0], u: [[0, { eosId: "a", hp: 100 }]] } }])[0],
       { tick: 1, players: [{ eosId: "a", hp: 100 }] },
       "first-sight entity");

{
  const out = feed([
    { v: 2 },
    { tick: 1, gameState: { map: "Narva" },
      players: { o: [0], u: [[0, { eosId: "a", hp: 100 }]] } },
    { tick: 2, players: { o: [0], u: [[0, { hp: 90 }]] } },
  ]);
  eqJson(out[1].gameState, { map: "Narva" }, "unmentioned key carries forward");
  eqJson(out[1].players, [{ eosId: "a", hp: 90 }], "entity delta applies");
}

// --- the assertion that actually matters -----------------------------------
{
  // `stats` is half of a player and identical for thousands of frames. Holding
  // one copy per frame is what broke the viewer; sharing is the fix.
  const out = feed([
    { v: 2 },
    { players: { o: [0], u: [[0, { eosId: "a", stats: { kills: 3 }, hp: 100 }]] } },
    { players: { o: [0], u: [[0, { hp: 90 }]] } },
    { players: { o: [0], u: [[0, { hp: 80 }]] } },
  ]);
  ok(out[2].players[0].stats === out[0].players[0].stats,
     "an unchanged sub-object is the SAME object, not a copy");
}

// --- losing things ---------------------------------------------------------
{
  const out = feed([{ v: 2 }, { tick: 1, projectiles: [{ x: 1 }] },
                    { tick: 2, "-": ["projectiles"] }]);
  ok(!("projectiles" in out[1]), "a removed top-level key is gone");
}
{
  const out = feed([
    { v: 2 },
    { players: { o: [0], u: [[0, { eosId: "a", squad: "Alpha" }]] } },
    { players: { o: [0], u: [[0, { "-": ["squad"] }]] } },
  ]);
  eqJson(out[1].players, [{ eosId: "a" }], "a removed entity field is gone");
}
{
  // `clanTag` and `soldier` are genuinely null for most players in most frames.
  const out = feed([
    { v: 2 },
    { players: { o: [0], u: [[0, { eosId: "a", clanTag: "ABC" }]] } },
    { players: { o: [0], u: [[0, { clanTag: null }]] } },
  ]);
  ok("clanTag" in out[1].players[0] && out[1].players[0].clanTag === null,
     "a null VALUE is kept, not treated as a removal");
}

// --- order and membership come from the frame ------------------------------
{
  const out = feed([
    { v: 2 },
    { players: { o: [0, 1], u: [[0, { eosId: "a" }], [1, { eosId: "b" }]] } },
    { players: { o: [1, 0] } },
  ]);
  eqJson(out[1].players.map((p: any) => p.eosId), ["b", "a"], "frame order wins");
}
{
  const out = feed([
    { v: 2 },
    { players: { o: [-1, 0], u: [[0, { eosId: "a" }]], b: [{ name: "no id" }] } },
  ]);
  eqJson(out[0].players, [{ name: "no id" }, { eosId: "a" }],
         "an unidentifiable entity keeps its place");
}
{
  const out = feed([
    { v: 2 },
    { players: { o: [0, 1], u: [[0, { eosId: "a" }], [1, { eosId: "b" }]] } },
    { players: { o: [1] } },
  ]);
  eqJson(out[1].players.map((p: any) => p.eosId), ["b"],
         "an entity the frame omits is absent");
}

// --- refusing what it cannot read ------------------------------------------
threw(() => new ReplayUnpacker().push({ v: 99 }), /unsupported/,
      "a future format is refused rather than half-read");
threw(() => new ReplayUnpacker().push({ tick: 1 }), /header/,
      "a stream with no header is refused");
ok(isPackedHeader({ v: 2 }) && !isPackedHeader({ tick: 1 })
   && !isPackedHeader({ v: 2, tick: 1 }) && !isPackedHeader(null),
   "the header is recognised and nothing else is");


// --- the other kind of line -------------------------------------------------
{
  // Two-tier recordings interleave 4 Hz position-only lines between full
  // snapshots, and the encoder wraps them as `{"p": frame}` precisely so they
  // are NOT diffed. This decoder had no branch for them at all: the line fell
  // through to the diff path, so a position update came back as a COPY OF THE
  // PREVIOUS FULL FRAME with a stray `p` key — every 4 Hz update silently
  // discarded, and a quarter of the download spent on frames that were then
  // thrown away. The Python decoder always handled this; nothing compared the
  // two, which is exactly why it survived.
  const out = feed([
    { v: 2 },
    { tick: 1, t: "full", players: { o: [0], u: [[0, { eosId: "a", playerId: 1 }]] } },
    { p: { t: "pos", tick: 2, players: [{ id: "a", x: 5, y: 6 }] } },
    { tick: 3 },
  ]);
  eqJson(out[1], { t: "pos", tick: 2, players: [{ id: "a", x: 5, y: 6 }] },
         "a position line comes back as itself, not as the last full frame");
  ok(!("p" in out[2]), "and it does not leak into the frames after it");
  eqJson(out[2].tick, 3, "the next full frame still diffs against the last FULL one");
  eqJson(out[2].players, [{ eosId: "a", playerId: 1 }],
         "a position line is not a diff baseline");
}

// --- a key that is not a field in JavaScript --------------------------------
{
  // `JSON.parse` makes `__proto__` an ordinary own property, but `{...o}` and
  // `o[k] = v` re-point the prototype instead of storing it. Python keeps it as
  // a field, and the server verifies with the PYTHON decoder — so a difference
  // here is invisible to every check that exists.
  const out = feed([
    { v: 2 },
    { players: { o: [0], u: [[0, JSON.parse('{"eosId":"a","__proto__":{"x":1}}')]] } },
  ]);
  const p = out[0].players[0];
  ok(Object.prototype.hasOwnProperty.call(p, "__proto__"),
     "__proto__ is kept as a field, the way Python keeps it");
  ok(Object.getPrototypeOf(p) === Object.prototype,
     "and it does not become the object's prototype");
}

console.log(`replayUnpack: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
