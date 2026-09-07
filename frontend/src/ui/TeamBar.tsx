// Top-center match strip: both teams (faction, tickets, K/D, players)
// flanking a centre block (map · mode + match timer + state). The single
// highest-value at-a-glance addition for a tactical map — tickets and
// who's ahead without opening the scoreboard.

import { useViewerStore } from "../state/viewerStore";
import type { TeamState } from "../state/types";

function fmtClock(sec: number | null | undefined): string {
  if (sec == null || sec < 0) return "—";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// "USMC_LO_Motorized" -> "USMC"; "PLA_LO_Motorized" -> "PLA".
function shortFaction(id: string | null | undefined): string {
  if (!id) return "—";
  return id.split("_")[0] || id;
}

// The commander seat, one line under a team's tickets. The seat itself is a
// per-frame field, so nothing falls between frames: a claim, a step-down and
// a team switch (which behaves as a step-down) all show as the name changing
// or clearing. `null` there is an EMPTY seat, read successfully; the field
// missing means the recorder could not read it, and the line then says
// nothing at all rather than "no commander".
function CommanderLine({ team, onOpen }: {
  team: TeamState | undefined; onOpen: () => void;
}) {
  const c = team?.commander;
  if (!c && team?.commanderName === undefined) return null;
  const vote = c?.vote;
  const voting = vote?.inProgress === true;
  const name = team?.commanderName;
  const seat = name ? name
    : name === null ? "no commander"
    : "—";
  return (
    <button className="tb-commander" onClick={onOpen}
            title="commander, vote and cooldowns">
      <span className="tb-cmd-star">★</span>
      <span className="tb-cmd-name">{seat}</span>
      {voting && (
        <span className="tb-cmd-vote">
          vote {vote?.timer != null ? `${vote.timer}s` : ""}
        </span>
      )}
      {!voting && c?.actionsEnabled === false && name && (
        // The commander's presence in a command zone, both ways — walked out
        // and back in, 2026-09-07. Not a guess: it is the team's own flag.
        <span className="tb-cmd-off" title="out of a command zone">no zone</span>
      )}
    </button>
  );
}

function TeamCol({ team, align, onOpenCommander }: {
  team: TeamState | undefined; align: "left" | "right";
  onOpenCommander: () => void;
}) {
  const id = team?.id ?? (align === "left" ? 1 : 2);
  const tickets = team?.tickets;
  const kd = `${team?.kills ?? 0} / ${team?.deaths ?? 0}`;
  return (
    <div className={"tb-team " + (align === "left" ? "tb-left" : "tb-right")}
         style={{ ["--tc" as string]: `var(--team${id})` }}>
      <div className="tb-team-main">
        <span className="tb-faction">{shortFaction(team?.factionId)}</span>
        <span className="tb-tickets">{tickets ?? "—"}</span>
      </div>
      <div className="tb-team-sub">
        <span className="tb-players">{team?.playerCount ?? "—"}👤</span>
        <span className="tb-kd">{kd}</span>
      </div>
      <CommanderLine team={team} onOpen={onOpenCommander} />
    </div>
  );
}

export function TeamBar() {
  const curSnap = useViewerStore((s) => s.curSnap);
  const setCommanderTeam = useViewerStore((s) => s.setCommanderPanelTeam);
  const gs = curSnap?.gameState ?? null;
  const teams = curSnap?.teams ?? [];
  const t1 = teams.find((t) => t.id === 1) ?? teams[0];
  const t2 = teams.find((t) => t.id === 2) ?? teams[1];

  // Relative ticket share for the split bar under the centre block.
  const a = Math.max(0, t1?.tickets ?? 0);
  const b = Math.max(0, t2?.tickets ?? 0);
  const total = a + b;
  const pct1 = total > 0 ? (a / total) * 100 : 50;

  const mapLine = [gs?.mapName, gs?.gameModeName].filter(Boolean).join(" · ") || "—";
  const state = gs?.matchState ?? "";
  const live = state === "InProgress";

  return (
    <div id="team-bar">
      <TeamCol team={t1} align="left"
               onOpenCommander={() => setCommanderTeam(t1?.id ?? 1)} />
      <div className="tb-centre">
        <div className="tb-map">{mapLine}</div>
        <div className="tb-clock">{fmtClock(gs?.elapsedSec)}
          {state && <span className={"tb-state " + (live ? "on" : "")}>{state}</span>}
        </div>
        <div className="tb-ticketbar">
          <div className="tb-tb-fill tb-tb-1" style={{ width: `${pct1}%` }} />
          <div className="tb-tb-fill tb-tb-2" style={{ width: `${100 - pct1}%` }} />
        </div>
      </div>
      <TeamCol team={t2} align="right"
               onOpenCommander={() => setCommanderTeam(t2?.id ?? 2)} />
    </div>
  );
}
