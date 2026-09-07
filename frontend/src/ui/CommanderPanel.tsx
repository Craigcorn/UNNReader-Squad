// One team's commander, in full: the seat, the vote, every asset's cooldown
// with its ready-in, the category gates, and the squads' tactical requests.
//
// Everything here is either a raw read off the frame or an arithmetic from
// state/commander/. Nothing is remembered between frames, nothing is
// defaulted, and where the recording cannot say — no game clock, a config
// the class pointer never reached, a request whose life started before the
// recording did — the panel says so in words instead of showing a number.

import { useMemo } from "react";
import { teamColor } from "../canvas/draw";
import { requestsOnMap } from "../state/commander/markers";
import {
  actionReadiness, categoryFor, categoryReadyGameTime, reclaimReadyGameTime,
} from "../state/commander/readyIn";
import { trackRequests, type RequestLife } from "../state/commander/requests";
import { useViewerStore } from "../state/viewerStore";
import type {
  CommanderActionCooldown, CommanderCategoryCooldown, Snapshot, TeamState,
} from "../state/types";
import { fmtDuration } from "./entityInfo";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="info-row">
      <span className="info-k">{label}</span>
      <span className="info-v">{children}</span>
    </div>
  );
}

/** One asset's row: what it is called, and when it can be called again. */
function ActionRow({ e, categories, worldTimeSec, seatEmpty, extensionSec }: {
  e: CommanderActionCooldown;
  categories: CommanderCategoryCooldown[] | undefined;
  worldTimeSec: number | null | undefined;
  seatEmpty: boolean;
  extensionSec: number | null | undefined;
}) {
  const r = actionReadiness(e, categories, worldTimeSec);
  const cat = categoryFor(e, categories);
  // With the seat empty, an entry's `remainingAtChange` is what the last
  // commander left on it, and §9 gives what a fresh claim would do with it.
  // A projection, labelled as one — the stamps say nothing about a claim
  // that has not happened.
  const onClaim = seatEmpty && worldTimeSec != null
    ? reclaimReadyGameTime(e, worldTimeSec, extensionSec) : null;
  const state = r.ready === null ? "unknown" : r.ready ? "ready" : "cooling";
  return (
    <li className={"cmd-action cmd-" + state}>
      <span className="cmd-a-name">
        {/* The config's own display text. Without it the class name stands,
            because a label made up from a class name is a guess. */}
        {e.displayName ?? e.action ?? <i>unreadable action</i>}
      </span>
      <span className="cmd-a-when">
        {r.ready === null
          ? <span className="info-mute">—</span>
          : r.ready
            ? <span className="cmd-ready">ready</span>
            : <span className="cmd-cooling">{fmtDuration(r.readyInSec)}</span>}
      </span>
      <span className="cmd-a-note">
        {r.gatedBy === "category" && cat && (
          <span className="cmd-gate" title="the category gate is the later one">
            {cat.name ?? `category ${cat.id}`}
          </span>
        )}
        {e.destroyedDuringActive && (
          <span className="cmd-cut" title="destroyed during its active window — the stamps did not move">
            cut short
          </span>
        )}
        {onClaim != null && (
          <span className="info-mute" title="what a fresh claim would leave on it (spec §9)">
            on claim +{fmtDuration(onClaim - (worldTimeSec ?? 0))}
          </span>
        )}
      </span>
    </li>
  );
}

function RequestRow({ r, worldTimeSec }: {
  r: RequestLife; worldTimeSec: number | null | undefined;
}) {
  const gone = r.goneFrameIdx != null;
  const state = gone ? (r.outcome ?? "gone") : r.phase;
  const age = !gone && r.firstSeenGameTime != null && worldTimeSec != null
    ? worldTimeSec - r.firstSeenGameTime : null;
  return (
    <li className={"cmd-req cmd-req-" + state}>
      <span className="cmd-req-sq">Squad {r.squad ?? "?"}</span>
      <span className="cmd-req-state">{state}</span>
      <span className="info-mute">
        {gone
          ? (r.ageAtRemovalSec != null ? `after ${Math.round(r.ageAtRemovalSec)}s` : "")
          : (age != null ? `${Math.round(age)}s` : "")}
        {gone && r.outcome === null && " · the recording cannot say why"}
      </span>
    </li>
  );
}

export function CommanderPanel() {
  const teamId = useViewerStore((s) => s.commanderPanelTeam);
  const close = useViewerStore((s) => s.setCommanderPanelTeam);
  const snap = useViewerStore((s) => s.curSnap);
  const frames = useViewerStore((s) => s.replay.frames);
  const idx = useViewerStore((s) => s.replay.currentIdx);

  // The request lifecycle is a pass over the whole recording, so it is
  // computed once per loaded replay rather than per frame. Live mode has no
  // frame list, and the panel then shows only what is on the map now.
  const lives = useMemo(
    () => (frames.length ? trackRequests(frames) : []), [frames]);

  if (teamId == null) return null;
  const team: TeamState | undefined = (snap?.teams ?? [])
    .find((t) => t.id === teamId);
  const c = team?.commander;
  const rules = snap?.gameState?.commanderRules;
  const now = snap?.gameState?.worldTimeSec ?? null;
  const seatEmpty = team?.commanderName == null;
  const vote = c?.vote;
  const actions = c?.cooldowns?.actions;
  const categories = c?.cooldowns?.categories;
  const requests = requestsForTeam(lives, snap, teamId, idx);

  return (
    <div id="commander-panel" className="detail-panel"
         style={{ ["--tc" as string]: teamColor(teamId) }}>
      <header>
        <h2>
          <span className="dot" style={{ background: teamColor(teamId) }} />
          Commander · team {teamId}
        </h2>
        <button onClick={() => close(null)} title="Close">✕</button>
      </header>
      <div className="body">
        {!c && (
          <div className="empty">
            this recording carries no commander block — it was made before the
            agent read one
          </div>
        )}
        {c && <>
          <div className="info-rows">
            <Row label="SEAT">
              {team?.commanderName
                ? <b>{team.commanderName}</b>
                : team?.commanderName === null
                  ? <span className="info-mute">empty</span>
                  : <span className="info-mute">unknown</span>}
            </Row>
            <Row label="SYSTEM">
              {c.enabled === true ? "on this layer"
                : c.enabled === false ? "not on this layer"
                : <span className="info-mute">unknown</span>}
            </Row>
            <Row label="ACTIONS">
              {c.actionsEnabled === true
                ? "enabled"
                : c.actionsEnabled === false
                  ? <span title="the commander's presence in a command zone">
                      disabled · out of a command zone
                    </span>
                  : <span className="info-mute">unknown</span>}
            </Row>
          </div>

          <h3 className="cmd-h">Vote</h3>
          <div className="info-rows">
            <Row label="STATE">
              {vote?.inProgress === true
                ? <b>open{vote.timer != null ? ` · ${vote.timer}s left` : ""}</b>
                : vote?.inProgress === false
                  ? <span className="info-mute">closed</span>
                  : <span className="info-mute">unknown</span>}
            </Row>
            {vote?.endsGameTime != null && now != null && (
              <Row label="ENDS">
                <span className="info-mono">
                  {fmtDuration(vote.endsGameTime - now) ?? "—"}
                </span>
              </Row>
            )}
            {vote?.cooldownActive != null && (
              <Row label="COOLDOWN">
                {vote.cooldownActive
                  ? <>blocking a claim
                      {vote.cooldownTimer != null ? ` · ${vote.cooldownTimer}s` : ""}</>
                  : <span className="info-mute">clear</span>}
              </Row>
            )}
            {rules && (
              <Row label="RULES">
                <span className="info-mono">
                  {rules.votingTimeSec ?? "?"}s vote · {rules.voteCooldownSec ?? "?"}s
                  cooldown · {rules.newCommanderExtensionSec ?? "?"}s extension
                </span>
              </Row>
            )}
          </div>
          {/* Nominees keep their entries after a vote resolves — that is the
              game's own state, and the final tallies are worth seeing. */}
          {vote?.nominees != null && (
            vote.nominees.length ? (
              <ul className="cmd-nominees">
                {vote.nominees.map((n, i) => (
                  <li key={n.eosId ?? i}>
                    <span>{n.name ?? (n.eosId === null ? "—" : n.eosId)}</span>
                    <span className="cmd-votes">{n.votes ?? "—"}</span>
                  </li>
                ))}
              </ul>
            ) : <div className="empty">nobody has been nominated</div>
          )}

          <h3 className="cmd-h">Assets</h3>
          {actions == null ? (
            <div className="empty">the cooldown list could not be read</div>
          ) : actions.length === 0 ? (
            <div className="empty">no entries yet — nobody has claimed the seat</div>
          ) : (
            <ul className="cmd-actions">
              {actions.map((e, i) => (
                <ActionRow key={e.action ?? i} e={e} categories={categories}
                           worldTimeSec={now} seatEmpty={seatEmpty}
                           extensionSec={rules?.newCommanderExtensionSec} />
              ))}
            </ul>
          )}

          {!!categories?.length && <>
            <h3 className="cmd-h">Category gates</h3>
            <ul className="cmd-actions">
              {categories.map((cat) => {
                const ready = categoryReadyGameTime(cat);
                const left = ready != null && now != null ? ready - now : null;
                return (
                  <li key={cat.id} className="cmd-action">
                    <span className="cmd-a-name">{cat.name ?? `category ${cat.id}`}</span>
                    <span className="cmd-a-when">
                      {cat.lastUseGameTime == null
                        ? <span className="info-mute">never called</span>
                        : left != null && left > 0
                          ? <span className="cmd-cooling">{fmtDuration(left)}</span>
                          : <span className="cmd-ready">open</span>}
                    </span>
                    <span className="cmd-a-note info-mute">
                      {cat.intervalSec != null ? `every ${fmtDuration(cat.intervalSec)}` : ""}
                    </span>
                  </li>
                );
              })}
            </ul>
          </>}

          <h3 className="cmd-h">Requests</h3>
          {requests.length === 0 ? (
            <div className="empty">no tactical requests from this team</div>
          ) : (
            <ul className="cmd-requests">
              {requests.map((r) => (
                <RequestRow key={r.key} r={r} worldTimeSec={now} />
              ))}
            </ul>
          )}
        </>}
      </div>
    </div>
  );
}

/** The requests worth showing: this team's, as they stood AT THE PLAYHEAD.
 *
 *  The lifecycle is a pass over the whole recording, so every one of its
 *  answers is about a frame that may still be in the viewer's future. Each
 *  one is therefore cut back to the playhead: a request that has yet to
 *  appear is not listed, one whose approval is still ahead reads pending,
 *  and one whose end is still ahead is simply on the map. Scrubbing then
 *  moves through the requests' lives instead of spoiling them.
 *
 *  Live mode has no frame list at all, so the frame's own markers are the
 *  whole list and nothing about how they end can be said. */
function requestsForTeam(
  lives: RequestLife[], snap: Snapshot | null, teamId: number,
  frameIdx: number,
): RequestLife[] {
  if (lives.length) {
    return lives
      .filter((r) => r.team === teamId && r.firstSeenFrameIdx <= frameIdx)
      .map((r) => {
        const goneYet = r.goneFrameIdx != null && r.goneFrameIdx <= frameIdx;
        const approvedYet = r.approvedFrameIdx != null
          && r.approvedFrameIdx <= frameIdx;
        return {
          ...r,
          phase: approvedYet ? r.phase : "pending" as const,
          approvedFrameIdx: approvedYet ? r.approvedFrameIdx : null,
          approvedGameTime: approvedYet ? r.approvedGameTime : null,
          goneFrameIdx: goneYet ? r.goneFrameIdx : null,
          goneGameTime: goneYet ? r.goneGameTime : null,
          outcome: goneYet ? r.outcome : null,
          ageAtRemovalSec: goneYet ? r.ageAtRemovalSec : null,
        };
      });
  }
  return requestsOnMap(snap?.markers)
    .filter((r) => r.team === teamId)
    .map((r) => ({
      key: r.key, team: r.team, squad: r.squad, fireTeamId: r.fireTeamId,
      position: r.position, phase: r.phase,
      firstSeenFrameIdx: 0, firstSeenGameTime: null,
      approvedFrameIdx: null, approvedGameTime: null,
      lastSeenFrameIdx: 0, lastSeenGameTime: null,
      goneFrameIdx: null, goneGameTime: null,
      outcome: null, ageAtRemovalSec: null,
    }));
}
