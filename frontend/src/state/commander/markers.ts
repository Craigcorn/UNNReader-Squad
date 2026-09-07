// Marker-level commander rules: which request a marker is, which markers are
// the same request, and which of two markers describing one placement to draw.
//
// Everything here is interpretation, and every rule is one the capture spec
// writes down (docs/command-assets-spec.md §9). Nothing is recorded about a
// request beyond its class name, its position and its squad, so nothing else
// is read.

import type { Marker } from "../types";

/** The request circle the game draws: 50 m around an APPROVED request. A
 *  documented game constant — edge-stands on two maps measured 49.95 m and
 *  50.20 m — and absent from server memory, so the viewer supplies it and
 *  the recorder never will (spec §9, "Request circle"). */
export const REQUEST_CIRCLE_CM = 5000;

/** The pair of circles a precision bomb draws at each aim point: the bomb
 *  config's 45 m and 100 m, in centimetres (spec §9, "Precision bombs").
 *
 *  Viewer constants for the same reason the request circle is one — nothing
 *  in server memory carries them, so the recorder never will. Unlike it they
 *  are NOT measured: the spec takes them from the bomb config and assumes
 *  they are what the in-game map draws, because the ratio matches and every
 *  observed bomb fell inside the inner one (five calls, 09-02/03). An
 *  edge-stand during a bomb call settles them — tracker T9's item (f),
 *  W20's B2, unrun because neither faction on the layer played carries a
 *  bomb strike. If that stand moves them, these two numbers are the whole
 *  of the change. */
export const BOMB_INNER_CM = 4500;
export const BOMB_OUTER_CM = 10000;

/** How close two markers must sit to be one placement. The game gives no
 *  identity linking a request's two markers, so "same position" is the join
 *  and this is the viewer's own tolerance for it — 3 m, well under the
 *  spacing of two deliberately separate requests and well over the jitter
 *  between two actors written from the same placement. */
export const SAME_PLACE_CM = 300;

export type RequestPhase = "pending" | "approved";

/** Which request a marker is, by class name — the ONLY discriminator there
 *  is. The two classes have byte-identical layouts and the `Request` bool
 *  reads 1 on both, so the name is the whole of it (spec §5, §9).
 *  `null` for every marker that is not a request. */
export function requestPhase(
  type: string | null | undefined,
): RequestPhase | null {
  const t = (type ?? "").toLowerCase();
  if (!t) return null;
  // SLRequest first: "command_request" is a substring of neither, but the
  // two names differ only by that prefix and order is what keeps them apart.
  if (t.includes("command_slrequest")) return "pending";
  if (t.includes("command_request")) return "approved";
  return null;
}

function samePlace(
  a: Marker, b: Marker, tolCm = SAME_PLACE_CM,
): boolean {
  const pa = a.position, pb = b.position;
  if (!pa || !pb) return false;
  const dx = pa.x - pb.x, dy = pa.y - pb.y;
  return dx * dx + dy * dy <= tolCm * tolCm;
}

/** One tactical request as the frame shows it. */
export interface RequestOnMap {
  /** Stable within a frame: team, squad and the rounded placement. Two
   *  frames of the same request produce the same key as long as the marker
   *  does not move, which a placed request never does. */
  key: string;
  phase: RequestPhase;
  team: number | null;
  squad: number | null;
  fireTeamId: number | null;
  position: { x: number; y: number; z: number | null };
  /** The marker the viewer should draw and hit-test — the approved one when
   *  a request is mid-swap and both are on the map. */
  marker: Marker;
  /** Both halves while the sweep has yet to take the pending twin. */
  pendingId: string | null;
  approvedId: string | null;
}

/** The requests a frame carries, with an approval and its pending twin read
 *  as ONE request changing state.
 *
 *  On approval the approved marker appears at once and the pending twin
 *  stays until the server's next ~61 s marker sweep, so for up to a minute
 *  both are really on the map. Drawing two would say two squads asked for
 *  two things (spec §9, "Pending versus approved, and deletes"). */
export function requestsOnMap(
  markers: Marker[] | null | undefined,
): RequestOnMap[] {
  const out: RequestOnMap[] = [];
  for (const m of markers ?? []) {
    const phase = requestPhase(m.type);
    if (!phase || !m.position) continue;
    const twin = out.find(
      (r) => r.team === m.team && r.squad === m.squad && samePlace(r.marker, m));
    if (twin) {
      if (phase === "pending") twin.pendingId = m.id;
      else {
        twin.approvedId = m.id;
        // The approved marker is the request now; it is also the one whose
        // class name says so.
        twin.phase = "approved";
        twin.marker = m;
        twin.position = m.position;
      }
      continue;
    }
    out.push({
      key: requestKey(m),
      phase,
      team: m.team,
      squad: m.squad,
      fireTeamId: m.fireTeamId,
      position: m.position,
      marker: m,
      pendingId: phase === "pending" ? m.id : null,
      approvedId: phase === "approved" ? m.id : null,
    });
  }
  return out;
}

/** A within-frame identity for a request — its squad and its rounded spot,
 *  for a React list key and nothing more. The marker ids cannot serve, since
 *  approval swaps one actor for another. Do NOT join on this across frames:
 *  two positions a centimetre apart can round either side of a bucket edge,
 *  which is why the lifecycle (requests.ts) matches by proximity instead. */
export function requestKey(m: Marker): string {
  const q = (v: number) => Math.round(v / SAME_PLACE_CM);
  const p = m.position;
  return [m.team ?? "?", m.squad ?? "?",
          p ? q(p.x) : "?", p ? q(p.y) : "?"].join("|");
}

// ---- director de-duplication ----------------------------------------------

/** Is this the ACTOR marker rather than the squad-data one?
 *
 *  The two paths write two different ids and that is the only place they
 *  differ structurally: an actor marker's id is its address as a lowercase
 *  hex string, a squad-data entry's is its replication id, a plain integer.
 *  (`sqreader/squad/snapshot.py`: `read_marker` writes `f"{addr:#x}"`, the
 *  marker-manager FastArray writes `f"{rep_id}"`.) */
export function isActorMarker(m: Marker): boolean {
  return /^0x/i.test(m.id);
}

/** The family two markers must share to be one placement: the name with the
 *  path's own prefix, the Blueprint suffix and the SL/FT role tail removed,
 *  lowercased. `BP_MapMarker_FriendlyDirector_C` and
 *  `DA_MapMarker_FriendlyDirector` both come back "friendlydirector". */
export function markerFamily(type: string | null | undefined): string | null {
  const raw = (type ?? "")
    .replace(/^(BP|DA)_MapMarker_/i, "")
    .replace(/_C$/, "")
    .replace(/_?(SL|FT)$/i, "")
    .replace(/[_\s]/g, "")
    .toLowerCase();
  return raw || null;
}

/** How much drawable geometry a marker carries. Higher wins the de-dup.
 *
 *  A dragged shaft outranks the Command/Director `distance`, deliberately:
 *  §9 says draw ONE shape and gives no rule for reading a Director's
 *  `distance` as an arrow, so the marker whose recorded geometry the viewer
 *  already draws is the one kept. A request marker's `distance` is 0 and its
 *  twin's shaft is 0 too, so there the actor marker wins on rank 1 — which
 *  is right, because its class name is what says pending or approved. */
function drawRank(m: Marker): number {
  if ((m.arrowLength ?? 0) > 1) return 2;
  if (m.distance !== undefined) return 1;
  return 0;
}

/** One shape per placement.
 *
 *  A squad leader's marker is two markers to the game — the squad-data one
 *  the placing squad sees and the team actor one every other squad leader
 *  sees — and the recorder records both and never merges them, because they
 *  are two real objects. Drawing both draws one order twice, so the viewer
 *  merges what the recorder would not: same family, same owner (team, squad
 *  and fireteam) and same position within SAME_PLACE_CM is one placement,
 *  and the entry carrying the most drawable geometry is the one drawn
 *  (spec §9, "Director markers").
 *
 *  Returns the markers to draw, in the input's order. A marker with no
 *  family or no position is passed through untouched — there is nothing to
 *  match it on, and dropping it would lose a real object. */
export function dedupeMarkers(
  markers: Marker[] | null | undefined,
): Marker[] {
  const list = markers ?? [];
  if (list.length < 2) return list.slice();
  // Group by family + owner; positions inside a group are compared pairwise,
  // because "within 3 m" does not partition into buckets cleanly.
  const groups = new Map<string, Marker[][]>();
  const dropped = new Set<Marker>();
  for (const m of list) {
    const fam = markerFamily(m.type);
    if (!fam || !m.position) continue;
    const key = `${fam}|${m.team ?? "?"}|${m.squad ?? "?"}|${m.fireTeamId ?? "?"}`;
    const cells = groups.get(key) ?? [];
    if (!groups.has(key)) groups.set(key, cells);
    const cell = cells.find((c) => samePlace(c[0]!, m));
    if (cell) cell.push(m); else cells.push([m]);
  }
  for (const cells of groups.values()) {
    for (const cell of cells) {
      if (cell.length < 2) continue;
      let best = cell[0]!;
      for (const m of cell) if (drawRank(m) > drawRank(best)) best = m;
      for (const m of cell) if (m !== best) dropped.add(m);
    }
  }
  return dropped.size ? list.filter((m) => !dropped.has(m)) : list.slice();
}
