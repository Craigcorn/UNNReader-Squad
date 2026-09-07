// Which rounds a call fired, and where each of them landed.
//
// Spec §9, "Asset impacts": every shell, rocket and bomb an asset fires is a
// tracked projectile whose `firer` is the caller, and for artillery the
// actor's `projectile` names the class; a round's impact is its rest position
// in the FIRST frame `hasImpacted` reads true, joined to the call by firer and
// class, and counted per barrage against `shellsPerBarrage`. Nothing here is
// recorded: the recorder writes the call and it writes the rounds, and never
// says the two belong together — the join is the viewer's, and it is made from
// recorded values only.
//
// Two things the fields make narrower than the sentence sounds.
//
// The join is through the ROSTER. `firer` and `callerEosId` are not the same
// kind of string: the recorder reads a projectile's firer off the instigator
// pawn's player state as `PlayerNamePrivate` — a NAME — and a command actor's
// caller as an `OnlineUserId` — an EOS id. The frame's own `players` list is
// the only thing that carries both, so `callerEosId` is resolved to that
// player's name there and compared exactly. A caller who is off the frame's
// roster joins no rounds at all, rather than joining them by something looser.
//
// The class half of the join exists only where the actor names a class.
// `projectile` is an artillery field (§6): the strike families declare no such
// name, so for a strike there is no recorded class to filter on and its rounds
// are the caller's rounds inside the actor's own life — the window the frames
// themselves give. Naming the rocket and bomb classes here would be a rule
// invented in the viewer, and §9 does not carry one.

import type {
  CommandAction, Player, Projectile, Snapshot, Vec3,
} from "../types";

/** The name a round this caller fired would carry, from the frame's own
 *  roster. `null` when the actor names no caller (`callerEosId` null or
 *  absent) or the caller is not on this frame's roster. */
export function callerFirerName(
  a: CommandAction | null | undefined,
  players: Player[] | null | undefined,
): string | null {
  const eos = a?.callerEosId;
  if (!eos) return null;
  return (players ?? []).find((p) => p.eosId === eos)?.name ?? null;
}

/** Does this round belong to this call?
 *
 *  `firerName` is the caller's name as `callerFirerName` resolved it — passed
 *  in rather than looked up per round, because a frame resolves it once and a
 *  barrage brings a dozen rounds. A round with no `firer` joins nothing: an
 *  unresolvable instigator is recorded as null and is not a match. */
export function roundMatchesCall(
  p: Projectile | null | undefined,
  a: CommandAction | null | undefined,
  firerName: string | null,
): boolean {
  if (!p || !a || !firerName) return false;
  if (p.firer !== firerName) return false;
  // Only where the actor names a class — see the header.
  if (a.projectile && p.classShort !== a.projectile) return false;
  return true;
}

/** Every round in ONE frame that belongs to a live call, mapped to the call
 *  it belongs to. What the map needs to draw a call's own rounds differently
 *  from everything else in the air.
 *
 *  A round that matches two calls at once — the same commander running two
 *  artillery plans of the same class — is put with the call that named its
 *  class, and otherwise with the first that matched: the recording carries
 *  nothing that separates them, and this map holds one call per round because
 *  a round is drawn once. */
export function roundsByCall(
  snap: Snapshot | null | undefined,
): Map<string, CommandAction> {
  const out = new Map<string, CommandAction>();
  const calls = snap?.commandActions ?? [];
  const rounds = snap?.projectiles ?? [];
  if (!calls.length || !rounds.length) return out;
  const names = new Map<string, string | null>();
  for (const a of calls) names.set(a.id, callerFirerName(a, snap?.players));
  for (const p of rounds) {
    if (!p.firer) continue;
    for (const a of calls) {
      if (!roundMatchesCall(p, a, names.get(a.id) ?? null)) continue;
      const prev = out.get(p.id);
      if (!prev) { out.set(p.id, a); continue; }
      // A named class is the more specific claim; keep it over a bare
      // firer match.
      if (!prev.projectile && a.projectile) out.set(p.id, a);
    }
  }
  return out;
}

/** One round landing: where it came to rest, when, and which barrage was
 *  running when it did. */
export interface ImpactEvent {
  /** The projectile's id — the actor address, new for every round. */
  id: string;
  classShort: string | null;
  /** The rest position in the first frame `hasImpacted` read true. */
  position: Vec3;
  /** That frame's game clock, and its index in the frames handed in. */
  gameTime: number | null;
  frameIdx: number;
  /** The call's `currentBarrage` in that same frame — which barrage this
   *  round landed during, read rather than derived. `null` where the actor
   *  records no barrage counter at all (every family but artillery), and 0
   *  where the counter has yet to move: on an artillery plan that is the
   *  warning phase, whose shells land before barrage 1. */
  barrage: number | null;
}

/** Every round one call landed, over a whole recording. */
export interface CallImpacts {
  actorId: string;
  /** The plan's own figures, as the last frame carrying the actor read them. */
  shellsPerBarrage: number | null;
  barrageCount: number | null;
  events: ImpactEvent[];
}

/** How many rounds landed, in total and split by the barrage they landed
 *  during. */
export interface LandedCount {
  landed: number;
  perBarrage: { barrage: number | null; landed: number }[];
}

/** Walk the frames for one call and collect its impacts.
 *
 *  The call's LIFE is the set of frames carrying its actor — the only window
 *  the recording gives — and a round is joined only inside it. An impact is
 *  taken once, at the first frame the round reads `hasImpacted`: the record
 *  lingers with the flag set for a few frames afterwards, and those are the
 *  same landing, not more of them. `null` when no frame carries the actor. */
export function buildCallImpacts(
  frames: Snapshot[],
  actorId: string,
): CallImpacts | null {
  let seenActor = false;
  const events: ImpactEvent[] = [];
  const landed = new Set<string>();
  let shellsPerBarrage: number | null = null;
  let barrageCount: number | null = null;
  for (let i = 0; i < frames.length; i++) {
    const f = frames[i]!;
    const a = (f.commandActions ?? []).find((c) => c.id === actorId);
    if (!a) continue;
    seenActor = true;
    if (a.shellsPerBarrage != null) shellsPerBarrage = a.shellsPerBarrage;
    if (a.barrageCount != null) barrageCount = a.barrageCount;
    const firerName = callerFirerName(a, f.players);
    if (!firerName) continue;
    const now = f.gameState?.worldTimeSec ?? null;
    for (const p of f.projectiles ?? []) {
      if (p.hasImpacted !== true || !p.position) continue;
      if (landed.has(p.id)) continue;
      if (!roundMatchesCall(p, a, firerName)) continue;
      landed.add(p.id);
      events.push({
        id: p.id,
        classShort: p.classShort ?? null,
        position: p.position,
        gameTime: now,
        frameIdx: i,
        barrage: a.currentBarrage ?? null,
      });
    }
  }
  if (!seenActor) return null;
  return { actorId, shellsPerBarrage, barrageCount, events };
}

/** The count as at `gameTime` — what has landed by the moment on screen.
 *
 *  An event with no clock of its own is not counted: it cannot be placed
 *  either side of the playhead, and a round of unknown time counted as landed
 *  would make the total run ahead of the picture. With no `gameTime` to
 *  compare against, every event counts — the whole call. */
export function landedBy(
  events: ImpactEvent[],
  gameTime?: number | null,
): LandedCount {
  const byBarrage = new Map<number | null, number>();
  let landed = 0;
  for (const e of events) {
    if (gameTime != null) {
      if (e.gameTime == null || e.gameTime > gameTime) continue;
    }
    landed++;
    byBarrage.set(e.barrage, (byBarrage.get(e.barrage) ?? 0) + 1);
  }
  const perBarrage = Array.from(byBarrage, ([barrage, n]) => ({
    barrage, landed: n,
  }));
  // Ascending by barrage, with the unknown ones last.
  perBarrage.sort((x, y) => {
    if (x.barrage == null) return y.barrage == null ? 0 : 1;
    if (y.barrage == null) return -1;
    return x.barrage - y.barrage;
  });
  return { landed, perBarrage };
}
