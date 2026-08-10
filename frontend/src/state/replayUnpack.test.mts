// The decoder has to agree with the Python encoder exactly, and it has to
// SHARE what it did not change — that sharing is the memory saving, and a
// decoder that quietly deep-copied would look correct in every assertion about
// values while giving back none of the point.
import { describe, expect, it } from "vitest";

import { isPackedHeader, ReplayUnpacker } from "./replayUnpack";

type Obj = Record<string, unknown>;

const feed = (lines: Obj[]): Obj[] => {
  const u = new ReplayUnpacker();
  const out: Obj[] = [];
  for (const l of lines) {
    const f = u.push(l);
    if (f) out.push(f);
  }
  return out;
};

describe("ReplayUnpacker", () => {
  it("rebuilds a frame from a first-sight entity", () => {
    const [f] = feed([
      { v: 2 },
      { tick: 1, players: { o: [0], u: [[0, { eosId: "a", hp: 100 }]] } },
    ]);
    expect(f).toEqual({ tick: 1, players: [{ eosId: "a", hp: 100 }] });
  });

  it("carries forward what a frame does not mention", () => {
    const out = feed([
      { v: 2 },
      { tick: 1, gameState: { map: "Narva" },
        players: { o: [0], u: [[0, { eosId: "a", hp: 100 }]] } },
      { tick: 2, players: { o: [0], u: [[0, { hp: 90 }]] } },
    ]);
    expect(out[1]!["gameState"]).toEqual({ map: "Narva" });
    expect(out[1]!["players"]).toEqual([{ eosId: "a", hp: 90 }]);
  });

  it("shares the objects it did not change", () => {
    // The assertion that actually matters. `stats` is half of a player and is
    // identical for thousands of frames; holding one copy per frame is what
    // broke the viewer in the first place.
    const out = feed([
      { v: 2 },
      { players: { o: [0], u: [[0, { eosId: "a", stats: { kills: 3 },
                                     hp: 100 }]] } },
      { players: { o: [0], u: [[0, { hp: 90 }]] } },
      { players: { o: [0], u: [[0, { hp: 80 }]] } },
    ]);
    const a = (out[0]!["players"] as Obj[])[0]!["stats"];
    const b = (out[2]!["players"] as Obj[])[0]!["stats"];
    expect(b).toBe(a);
  });

  it("drops a top-level key the frame says is gone", () => {
    const out = feed([
      { v: 2 },
      { tick: 1, projectiles: [{ x: 1 }] },
      { tick: 2, "-": ["projectiles"] },
    ]);
    expect(out[1]!["projectiles"]).toBeUndefined();
  });

  it("drops a field the entity says is gone", () => {
    const out = feed([
      { v: 2 },
      { players: { o: [0], u: [[0, { eosId: "a", squad: "Alpha" }]] } },
      { players: { o: [0], u: [[0, { "-": ["squad"] }]] } },
    ]);
    expect(out[1]!["players"]).toEqual([{ eosId: "a" }]);
  });

  it("keeps a null that is a value, not a removal", () => {
    // `clanTag` and `soldier` are genuinely null for most players in most
    // frames. Confusing the two is how a diff format silently loses fields.
    const out = feed([
      { v: 2 },
      { players: { o: [0], u: [[0, { eosId: "a", clanTag: "ABC" }]] } },
      { players: { o: [0], u: [[0, { clanTag: null }]] } },
    ]);
    const p = (out[1]!["players"] as Obj[])[0]!;
    expect("clanTag" in p).toBe(true);
    expect(p["clanTag"]).toBeNull();
  });

  it("puts entities in the order the frame gives, not roster order", () => {
    const out = feed([
      { v: 2 },
      { players: { o: [0, 1], u: [[0, { eosId: "a" }], [1, { eosId: "b" }]] } },
      { players: { o: [1, 0] } },
    ]);
    expect((out[1]!["players"] as Obj[]).map((p) => p["eosId"]))
      .toEqual(["b", "a"]);
  });

  it("places an unidentifiable entity where it belongs", () => {
    const out = feed([
      { v: 2 },
      { players: { o: [-1, 0], u: [[0, { eosId: "a" }]],
                   b: [{ name: "no id" }] } },
    ]);
    expect(out[0]!["players"]).toEqual([{ name: "no id" }, { eosId: "a" }]);
  });

  it("leaves out an entity the frame does not list", () => {
    const out = feed([
      { v: 2 },
      { players: { o: [0, 1], u: [[0, { eosId: "a" }], [1, { eosId: "b" }]] } },
      { players: { o: [1] } },
    ]);
    expect((out[1]!["players"] as Obj[]).map((p) => p["eosId"])).toEqual(["b"]);
  });

  it("refuses a format it cannot read", () => {
    expect(() => new ReplayUnpacker().push({ v: 99 })).toThrow(/unsupported/);
  });

  it("refuses a stream with no header", () => {
    expect(() => new ReplayUnpacker().push({ tick: 1 })).toThrow(/header/);
  });

  it("recognises the header and nothing else", () => {
    expect(isPackedHeader({ v: 2 })).toBe(true);
    expect(isPackedHeader({ tick: 1 })).toBe(false);
    expect(isPackedHeader({ v: 2, tick: 1 })).toBe(false);
    expect(isPackedHeader(null)).toBe(false);
  });
});
