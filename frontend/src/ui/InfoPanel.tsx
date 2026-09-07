// Shared click-detail panel for every map entity that isn't a player or a
// vehicle (those keep their own panels). One component, keyed on the generic
// `selectedInfo` selection; the body switches on the entity kind. Squad
// terminology (marker/vehicle/faction names) stays English.

import type React from "react";
import { useMemo } from "react";
import { teamColor } from "../canvas/draw";
import { vehicleDisplayName } from "../data/vehicleDisplayNames";
import { useViewerStore } from "../state/viewerStore";
import type {
  CaptureZone, CommandAction, Deployable, Drone, Marker, Projectile,
  RallyPoint, Snapshot, Vec3, VehicleSpawner,
} from "../state/types";
import {
  artilleryPhase, artilleryTimeline, isArtillery, isShotDown,
} from "../state/commander/assets";
import {
  buildDroneTracks, droneBudgetSec, droneRemainingSec, droneTeam,
} from "../state/commander/drones";
import { actionDisplayName, findActionEntry } from "../state/commander/readyIn";
import {
  fmtDuration, fmtInt, ftLabel, findPlacer, markerLabel, playerLabel,
} from "./entityInfo";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="info-row">
      <span className="info-k">{label}</span>
      <span className="info-v">{children}</span>
    </div>
  );
}

// Reuse the shared .hp-row bar (health / ammo / construction).
function Bar({ label, cur, max, color }: {
  label: string; cur: number | null | undefined; max: number | null | undefined;
  color?: string;
}) {
  const pct = (cur != null && max != null && max > 0)
    ? Math.max(0, Math.min(100, (cur / max) * 100)) : null;
  return (
    <div className="hp-row">
      <span className="hp-label">{label}</span>
      <div className="hp-bar">
        <div className="hp-fill" style={{ width: `${pct ?? 0}%`,
          background: color ?? "var(--good)" }} />
      </div>
      <span className="hp-num">{fmtInt(cur)}{max != null ? `/${fmtInt(max)}` : ""}</span>
    </div>
  );
}

// mm:ss for a countdown in seconds. Negative/undefined → null (caller hides it).
function fmtCountdown(sec: number | null | undefined): string | null {
  if (sec == null || sec <= 0) return null;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function Badges({ items }: { items: [string, boolean | null | undefined][] }) {
  const on = items.filter(([, v]) => v);
  if (!on.length) return null;
  return (
    <div className="info-badges">
      {on.map(([label]) => <span key={label} className="info-badge">{label}</span>)}
    </div>
  );
}

function posRow(pos: Vec3 | null | undefined) {
  if (!pos) return null;
  return (
    <Row label="POS">
      <span className="info-mono">
        {Math.round(pos.x)}, {Math.round(pos.y)}, {pos.z != null ? Math.round(pos.z) : "—"}
      </span>
    </Row>
  );
}

// ---- per-kind bodies -------------------------------------------------------

function MarkerBody({ e }: { e: Marker }) {
  const players = useViewerStore((s) => s.curSnap?.players ?? []);
  const placer = findPlacer(e, players);
  return (
    <>
      <Row label="TEAM">{e.team ?? "—"}</Row>
      {e.squad != null && (
        <Row label="ROLE">Squad {e.squad} · {ftLabel(e.fireTeamId)}</Row>
      )}
      <Row label="PLACER">
        {placer
          ? <b>{placer.clanTag ? `[${placer.clanTag}] ` : ""}{placer.name ?? "?"}</b>
          : <span className="info-mute">offline / unresolved</span>}
      </Row>
      <Row label="ID">{e.id}</Row>
      <Row label="RAW TYPE">
        <span className="info-mono">{(e.type ?? "").replace(/^BP_MapMarker_/, "") || "—"}</span>
      </Row>
      {posRow(e.position)}
    </>
  );
}

function DeployableBody({ e, worldTimeSec }: {
  e: Deployable; worldTimeSec?: number | null;
}) {
  // FOB bleed-out countdown: only while bleeding, and only when both the death
  // stamp and the current world clock are known. Otherwise hidden — never a
  // guessed timer.
  const bleedLeft = (e.fobBleeding && e.estimatedDeathTime != null
                     && worldTimeSec != null)
    ? fmtCountdown(e.estimatedDeathTime - worldTimeSec) : null;
  return (
    <>
      <Row label="TEAM">{e.team ?? "—"}{e.isFob ? " · FOB" : ""}</Row>
      <Bar label="HP" cur={e.health} max={e.maxHealth} />
      <Row label="PLACED BY">
        {e.placer ? <b>{e.placer}</b> : <span className="info-mute">—</span>}
      </Row>
      {e.isFob && <>
        <Bar label="AMMO" cur={e.ammo} max={e.maxAmmo} color="var(--accent)" />
        <Bar label="BUILD" cur={e.construction} max={e.maxConstruction} color="var(--accent-2)" />
        {(e.ammoPerSecond != null || e.cpPerSecond != null) && (
          <Row label="RATE">
            <span className="info-mono">
              ammo {e.ammoPerSecond ?? "—"}/s · build {e.cpPerSecond ?? "—"}/s
            </span>
          </Row>
        )}
        {bleedLeft && (
          <Row label="BLEED"><span className="info-danger">☠ {bleedLeft}</span></Row>
        )}
        {e.nearbyEnemies != null && <Row label="ENEMIES">{e.nearbyEnemies}</Row>}
        <Badges items={[
          ["Sieged", e.fobSieged], ["Spawning", e.fobSpawningEnabled],
          ["Bleeding", e.fobBleeding], ["Overrun", e.fobOverrun],
        ]} />
      </>}
      {e.buildState != null && <Row label="BUILT">{e.buildState}</Row>}
      {e.classShort && <Row label="CLASS"><span className="info-mono">{e.classShort}</span></Row>}
      <Row label="ID">{e.id}</Row>
      {posRow(e.position)}
    </>
  );
}

function SpawnerBody({ e }: { e: VehicleSpawner }) {
  return (
    <>
      <Row label="TEAM">{e.team ?? "—"}</Row>
      <Row label="VEHICLE"><b>{vehicleDisplayName(e.vehicleClass ?? e.classShort)}</b></Row>
      {(() => { const cd = fmtCountdown(e.nextSpawnSec);
        return cd ? <Row label="RESPAWN"><span className="info-mono">{cd}</span></Row>
                  : null; })()}
      <Badges items={[
        ["Ready", e.spawnerEnabled], ["Spawning", e.spawnInProgress],
        ["Overlapped", e.spawnOverlapped],
      ]} />
      {e.actorName && <Row label="ACTOR"><span className="info-mono">{e.actorName}</span></Row>}
      <Row label="ID">{e.id}</Row>
      {posRow(e.position)}
    </>
  );
}

function RallyBody({ e }: { e: RallyPoint }) {
  return (
    <>
      <Row label="TEAM">{e.team ?? "—"}</Row>
      <Row label="SQUAD">{e.squadName ?? (e.squadId != null ? `#${e.squadId}` : "—")}</Row>
      {e.spawnsRemaining != null && <Row label="SPAWNS">{e.spawnsRemaining}</Row>}
      <Badges items={[["Spawning", e.spawningEnabled], ["Sieged", e.sieged]]} />
      <Row label="ID">{e.id}</Row>
      {posRow(e.position)}
    </>
  );
}

function CapzoneBody({ e }: { e: CaptureZone }) {
  return (
    <>
      <Row label="FLAG"><b>{e.flagName ?? e.name ?? "?"}</b></Row>
      <Row label="OWNER">
        <span className="info-dot" style={{ background: teamColor(e.owningTeam) }} />
        Team {e.owningTeam ?? "—"}
      </Row>
      {e.capturingTeam != null && (
        <Row label="CAPPING">
          <span className="info-dot" style={{ background: teamColor(e.capturingTeam) }} />
          Team {e.capturingTeam}
        </Row>
      )}
      <Bar label="CAP" cur={e.capturePercent != null ? e.capturePercent * 100 : null} max={100} />
      {e.captureRate != null && <Row label="RATE">{e.captureRate}</Row>}
      {e.playerAdvantage != null && (
        <Row label="ADVANTAGE">
          <span className="info-mono">{e.playerAdvantage > 0 ? "+" : ""}
            {e.playerAdvantage.toFixed(1)}</span>
        </Row>
      )}
      <Badges items={[["Locked", e.isLocked]]} />
      <Row label="ID">{e.id}</Row>
      {posRow(e.position)}
    </>
  );
}

function ProjectileBody({ e }: { e: Projectile }) {
  return (
    <>
      <Row label="TEAM">{e.team ?? "—"}</Row>
      <Row label="KIND">{e.kind ?? e.classShort ?? "projectile"}</Row>
      {e.isExplosive && e.explosiveBaseDamage != null && (
        <Row label="DAMAGE">{fmtInt(e.explosiveBaseDamage)}
          {e.explosiveKillZoneRadius != null
            ? ` · r ${fmtInt(e.explosiveKillZoneRadius)}` : ""}</Row>
      )}
      {e.firer && <Row label="FIRER"><b>{e.firer}</b></Row>}
      <Badges items={[["Tracer", e.isTracer], ["Impacted", e.hasImpacted]]} />
      <Row label="ID">{e.id}</Row>
      {posRow(e.position)}
    </>
  );
}

/** A called asset: what it is, who called it, and where it is in its plan.
 *
 *  Everything shown is a read off this frame or an arithmetic over it. Who
 *  shot an asset down is NOT shown, because it is not recorded: no
 *  last-damager field exists on any command actor, the server log carries no
 *  line for it, and nothing nearby is read as one (decision D18). */
function CommandActionBody({ e, snap }: { e: CommandAction; snap: Snapshot | null }) {
  const entry = findActionEntry(e.action, snap?.teams, e.team ?? null);
  const now = snap?.gameState?.worldTimeSec ?? null;
  const caller = playerLabel(e.callerEosId, snap?.players);
  const t = isArtillery(e) ? artilleryTimeline(e, entry) : null;
  const phase = t ? artilleryPhase(t, now) : null;
  return (
    <>
      <Row label="TEAM">{e.team ?? "—"}</Row>
      <Row label="CALLED BY">
        {caller ? <b>{caller}</b>
          : e.callerEosId === null
            ? <span className="info-mute">nobody — no caller on the actor</span>
            : <span className="info-mute">off the roster</span>}
      </Row>
      {isShotDown(e) && (
        <Row label="STATE">
          <span className="info-danger">shot down — the call was cut short</span>
        </Row>
      )}
      {e.shotsMade != null && (
        <Row label="SHOTS">{fmtInt(e.shotsMade)}
          {e.maxShots != null ? ` / ${fmtInt(e.maxShots)}` : ""}</Row>
      )}
      {t && <>
        {phase && <Row label="PHASE">{phase}</Row>}
        {t.gunsOpenGameTime != null && now != null && (
          <Row label="GUNS OPEN">
            <span className="info-mono">
              {fmtDuration(t.gunsOpenGameTime - now) ?? "—"}
            </span>
          </Row>
        )}
        {t.hasWarningPhase === true && (
          <Row label="WARNING">{fmtInt(t.warningShellsFired)} / {fmtInt(t.warningShellsTotal)} shells</Row>
        )}
        {t.barrageCount != null && (
          <Row label="BARRAGE">{fmtInt(t.currentBarrage)} / {fmtInt(t.barrageCount)}
            {e.shellsPerBarrage != null ? ` · ${fmtInt(e.shellsPerBarrage)} shells each` : ""}</Row>
        )}
        {e.projectile && (
          <Row label="ROUND"><span className="info-mono">{e.projectile}</span></Row>
        )}
      </>}
      {e.action !== undefined && (
        <Row label="ACTION">
          <span className="info-mono">{e.action ?? "—"}</span>
        </Row>
      )}
      <Row label="CLASS"><span className="info-mono">{e.class ?? "—"}</span></Row>
      <Row label="ID">{e.id}</Row>
      {posRow(e.position)}
    </>
  );
}

/** A drone pawn: who is flying it, who deployed it, and what became of it.
 *
 *  Its team is the owner's — the pawn carries none — and its killer, where
 *  it has one, is the hitter at the moment of the death rather than the last
 *  one to hit the falling wreck. Both are read at the frame's own resolution
 *  here; the whole-recording answer is on the drone track. */
function DroneBody({ e, snap }: { e: Drone; snap: Snapshot | null }) {
  const frames = useViewerStore((s) => s.replay.frames);
  const pilot = playerLabel(e.pilotEosId, snap?.players);
  const owner = playerLabel(e.ownerEosId, snap?.players);
  const hitter = playerLabel(e.lastHitByEosId, snap?.players);
  const budget = droneBudgetSec(e, snap?.teams);
  // The killer and the flight budget are whole-recording answers: a drone's
  // spawn is the frame its id first appears in, and its killer is the hitter
  // of the FIRST 4 Hz sample reading dead — not the second shooter who hits
  // the falling pawn a second later, which is what this frame's
  // `lastHitByEosId` will already have become. Live mode has no frame list
  // and so has neither answer.
  const track = useMemo(
    () => (frames.length ? buildDroneTracks(frames).get(e.id) ?? null : null),
    [frames, e.id]);
  const killer = playerLabel(track?.killerEosId, snap?.players);
  const left = droneRemainingSec(track, snap?.gameState?.worldTimeSec);
  return (
    <>
      <Row label="TEAM">{droneTeam(e, snap) ?? "—"}
        <span className="info-mute"> · from the owner</span></Row>
      {e.dead && (
        <Row label="STATE"><span className="info-danger">destroyed</span></Row>
      )}
      {(e.health != null || e.maxHealth != null) && (
        <Bar label="HP" cur={e.health} max={e.maxHealth} />
      )}
      <Row label="PILOT">
        {pilot ? <b>{pilot}</b>
          : e.pilotEosId === null
            ? <span className="info-mute">nobody flying it</span>
            : <span className="info-mute">off the roster</span>}
      </Row>
      <Row label="OWNER">
        {owner ? <b>{owner}</b> : <span className="info-mute">—</span>}
      </Row>
      {e.lastHitByEosId !== undefined && (
        <Row label="LAST HIT BY">
          {hitter ? <b>{hitter}</b>
            : e.lastHitByEosId === null
              ? <span className="info-mute">nothing has hit it</span>
              : <span className="info-mute">off the roster</span>}
        </Row>
      )}
      {/* Distinct from the row above on purpose: a later hit moves the
          pointer on the falling pawn, so the killer is the reading at the
          moment of the death and nothing after it. */}
      {track?.deadFromFrameIdx != null && (
        <Row label="KILLED BY">
          {killer ? <b>{killer}</b>
            : track.killerEosId === null
              ? <span className="info-mute">nothing had hit it — the battery ran out</span>
              : <span className="info-mute">off the roster</span>}
        </Row>
      )}
      {budget != null && (
        <Row label="BUDGET"><span className="info-mono">
          {fmtDuration(budget)}</span>
          <span className="info-mute">
            {e.batteryLifetimeMax != null ? " · battery" : " · the call's window"}
          </span>
        </Row>
      )}
      {left != null && !e.dead && (
        <Row label="FLIGHT LEFT">
          <span className="info-mono">{fmtDuration(left)}</span>
        </Row>
      )}
      {e.commandAction !== undefined && e.commandAction !== null && (
        <Row label="CALLED BY">
          <span className="info-mono">{e.commandAction}</span>
        </Row>
      )}
      <Row label="CLASS"><span className="info-mono">{e.class ?? "—"}</span></Row>
      <Row label="ID">{e.id}</Row>
      {posRow(e.position)}
    </>
  );
}

// ---- panel shell -----------------------------------------------------------

export function InfoPanel() {
  const sel   = useViewerStore((s) => s.selectedInfo);
  const snap  = useViewerStore((s) => s.curSnap);
  const close = useViewerStore((s) => s.setSelectedInfo);

  if (!sel) return null;

  // Resolve the live entity from the current snapshot by kind + id.
  let title = "";
  let team: number | null | undefined = null;
  let bodyEl: React.ReactNode = null;
  const find = <T extends { id: string }>(arr: T[] | undefined) =>
    (arr ?? []).find((x) => x.id === sel.id) ?? null;

  switch (sel.kind) {
    case "marker": {
      const e = find<Marker>(snap?.markers);
      if (e) { title = markerLabel(e.type); team = e.team; bodyEl = <MarkerBody e={e} />; }
      break;
    }
    case "deployable": {
      const e = find<Deployable>(snap?.deployables);
      if (e) { title = (e.isFob ? "FOB · " : "") + (e.classShort ?? "Deployable");
               team = e.team;
               bodyEl = <DeployableBody e={e}
                          worldTimeSec={snap?.gameState?.worldTimeSec} />; }
      break;
    }
    case "spawner": {
      const e = find<VehicleSpawner>(snap?.vehicleSpawners);
      if (e) { title = vehicleDisplayName(e.vehicleClass ?? e.classShort);
               team = e.team; bodyEl = <SpawnerBody e={e} />; }
      break;
    }
    case "rally": {
      const e = find<RallyPoint>(snap?.rallyPoints);
      if (e) { title = "Rally Point"; team = e.team; bodyEl = <RallyBody e={e} />; }
      break;
    }
    case "capzone": {
      const e = find<CaptureZone>(snap?.captureZones);
      if (e) { title = e.flagName ?? e.name ?? "Capture Zone";
               team = e.owningTeam; bodyEl = <CapzoneBody e={e} />; }
      break;
    }
    case "projectile": {
      const e = find<Projectile>(snap?.projectiles);
      if (e) { title = e.classShort ?? "Projectile"; team = e.team; bodyEl = <ProjectileBody e={e} />; }
      break;
    }
    case "commandAction": {
      const e = find<CommandAction>(snap?.commandActions);
      if (e) {
        // The config's own display text where the commander block carries
        // it; the class name otherwise, never a label made up from it.
        title = actionDisplayName(e.action, snap?.teams, e.team ?? null)
          ?? e.class ?? "Command asset";
        team = e.team ?? null;
        bodyEl = <CommandActionBody e={e} snap={snap} />;
      }
      break;
    }
    case "drone": {
      const e = find<Drone>(snap?.drones);
      if (e) {
        title = e.class ?? "Drone";
        team = droneTeam(e, snap);
        bodyEl = <DroneBody e={e} snap={snap} />;
      }
      break;
    }
  }

  const tc = teamColor(team);

  if (!bodyEl) {
    // Entity left the snapshot (despawned / out of range) since selection.
    return (
      <div id="info-panel" className="detail-panel">
        <header>
          <h2>no longer on the map</h2>
          <button onClick={() => close(null)} title="Close (esc)">✕</button>
        </header>
        <div className="body">
          <div className="empty">this object is no longer in the snapshot — despawned or out of range</div>
        </div>
      </div>
    );
  }

  return (
    <div id="info-panel" className="detail-panel">
      <header>
        <h2>
          <span className="dot" style={{ background: tc }} />
          {title}
        </h2>
        <button onClick={() => close(null)} title="Close (esc)">✕</button>
      </header>
      <div className="body">
        <div className="info-rows">{bodyEl}</div>
      </div>
    </div>
  );
}
