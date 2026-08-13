// Reading the compact recording format the server can now send.
//
// A match expands to nearly two gigabytes of frames, and the viewer used to
// hold every one of them fully materialised — which is why a long game
// answered "Recording failed to load" rather than playing. Almost none of that
// is new information: a player's identity, their name and their `stats` object
// are identical in thousands of consecutive frames.
//
// Format v2 sends each of those once and then only what changed. This decoder
// is the exact mirror of `central/replaypack.py`, and the two are checked
// against each other on the server: every cached file is decoded and compared
// to the original frame by frame before it is ever served.
//
// THE POINT IS NOT THE DOWNLOAD. It is that an unchanged sub-object is REUSED
// here rather than copied, so ten thousand frames share one `stats` object
// instead of holding ten thousand of them. Deep-copying while decoding would
// give back the bandwidth saving and none of the memory saving, which is the
// part that was actually broken.

export const REPLAY_FORMAT_VERSION = 2;

/** Arrays the encoder sends as tracked entities. The decoder only needs to
 *  know WHICH keys those are — how an entity was identified is the encoder's
 *  business, and the indices it assigned are transmitted. Keeping an identity
 *  function here too would be a second place for the two implementations to
 *  disagree about something only one of them decides. */
const KEYED = new Set(["players", "vehicles"]);

type Obj = Record<string, unknown>;

/** Copy an object's own keys without ever letting one of them change what the
 *  result inherits from. `JSON.parse` makes `__proto__` an ordinary own
 *  property, but `{...o}` and `o[k] = v` do NOT: they re-point the prototype
 *  instead of storing a field. Python keeps it as a field, so without this the
 *  two decoders disagree — and the server verifies with the Python one, which
 *  makes the difference invisible to every check we have. */
function put(target: Obj, key: string, value: unknown): void {
  Object.defineProperty(target, key, {
    value, writable: true, enumerable: true, configurable: true,
  });
}

function copy(o: Obj): Obj {
  const out: Obj = {};
  for (const [k, v] of Object.entries(o)) put(out, k, v);
  return out;
}

export class ReplayUnpacker {
  private prev: Obj = {};
  private ents = new Map<string, Map<number, Obj>>();
  private sawHeader = false;

  constructor() {
    for (const k of KEYED) this.ents.set(k, new Map());
  }

  /** True once the stream has identified itself as the compact format. */
  get active(): boolean {
    return this.sawHeader;
  }

  /**
   * Feed one parsed line. Returns the frame it completes, or null for the
   * header. Throws only on a version this build cannot read — better than
   * silently rendering a match that is not the one that was played.
   */
  push(obj: Obj): Obj | null {
    if (!this.sawHeader) {
      this.sawHeader = true;
      const v = obj["v"];
      if (typeof v === "number") {
        if (v !== REPLAY_FORMAT_VERSION) {
          throw new Error(`unsupported replay format ${v}`);
        }
        return null;
      }
      throw new Error("missing replay format header");
    }

    // A line the encoder could not parse travels through untouched, so the
    // compact stream is exactly as lossy as the original: not at all.
    if (typeof obj["raw"] === "string" && Object.keys(obj).length === 1) {
      return null;
    }

    // A two-tier recording interleaves 4 Hz position-only lines between full
    // snapshots, and the encoder wraps them as `{"p": frame}` so they are not
    // diffed against anything. `prev` is deliberately NOT updated: the next
    // full frame must still diff against the last FULL frame, which is what
    // the encoder assumed when it built it.
    //
    // Without this branch the line fell through to the diff path below and a
    // position update came back as a copy of the previous full frame carrying
    // a stray `p` key — so every 4 Hz update was discarded and the junk key
    // then rode along on every frame after it.
    if ("p" in obj && Object.keys(obj).length === 1) {
      return obj["p"] as Obj;
    }

    const frame: Obj = copy(this.prev);
    const removed = obj["-"];
    if (Array.isArray(removed)) {
      for (const k of removed) delete frame[k as string];
    }
    for (const [key, val] of Object.entries(obj)) {
      if (key === "-") continue;
      if (KEYED.has(key) && val && typeof val === "object"
          && !Array.isArray(val)) {
        frame[key] = this.entities(key, val as Obj);
      } else {
        put(frame, key, val);
      }
    }
    this.prev = frame;
    return frame;
  }

  private entities(key: string, enc: Obj): unknown[] {
    const table = this.ents.get(key)!;
    const updates = enc["u"];
    if (Array.isArray(updates)) {
      for (const pair of updates) {
        const [idx, payload] = pair as [number, Obj];
        const base = table.get(idx);
        if (!base) {
          const fresh: Obj = {};
          for (const [k, v] of Object.entries(payload)) {
            if (k !== "-") put(fresh, k, v);
          }
          table.set(idx, fresh);
          continue;
        }
        // A shallow copy: every field the frame did not mention keeps pointing
        // at the very same object it did before. That sharing is the whole
        // memory saving.
        const merged: Obj = copy(base);
        const gone = payload["-"];
        if (Array.isArray(gone)) {
          for (const k of gone) delete merged[k as string];
        }
        for (const [k, v] of Object.entries(payload)) {
          if (k !== "-") put(merged, k, v);
        }
        table.set(idx, merged);
      }
    }

    // Order and membership are transmitted, not inferred. Rebuilding them from
    // a roster put entities in the wrong places as soon as one of them had no
    // id to be tracked by.
    const verbatim = Array.isArray(enc["b"]) ? [...(enc["b"] as unknown[])] : [];
    const out: unknown[] = [];
    const order = enc["o"];
    if (Array.isArray(order)) {
      for (const idx of order as number[]) {
        if (idx === -1) {
          if (verbatim.length) out.push(verbatim.shift());
          continue;
        }
        const ent = table.get(idx);
        if (ent) out.push(ent);
      }
    }
    return out;
  }
}

/** Does this first line announce the compact format? */
export function isPackedHeader(obj: unknown): boolean {
  return !!obj && typeof obj === "object"
    && typeof (obj as Obj)["v"] === "number"
    && Object.keys(obj as Obj).length === 1;
}

export { KEYED as _KEYED };
