#!/usr/bin/env node
// Build the commander scenario fixtures — one recording per viewer rule, small
// enough to read, large enough to play.
//
//   node src/state/__fixtures__/commander/generate.mjs
//
// Deterministic by construction: no clock, no randomness, every stamp derived
// from a fixed base. Run it and `git diff` should be empty unless a scenario
// changed here.
//
// WHERE THE NUMBERS COME FROM. Every figure the capture spec records is used
// verbatim, so a reviewer comparing a scenario against
// docs/command-assets-spec.md sees the same numbers:
//
//   §3  the UAV's 30 / 300 / 600 and its category's 600 s interval; its
//       display text "MQ-9 UAV Recon"; the mortar's "Heavy Mortar Barrage"
//   §4  the server's rules, read live 2026-09-04: 60 / 300 / 300 / 2 / 3
//   §5  the footprint figures: a UAV coverage radius of 16608, the static
//       barrage's 15000 with a 7500 outer band, the mortar's fixed 7500 with
//       4500, a strike run of 6000, the creep's 45000 path with 7500 of drop
//       scatter, an aim line's 4475
//   §6  the creep's 2 warning shells and 12 s delay on a 60 s enroute, its
//       counter reaching 1 at +59.7 s and 2 at +67.1 s with the first barrage
//       at +79.2 s; the mortar's 0 / 0 / 10 shells / 8 barrages / 1.0 drop
//       radius, its barrages every 8-9 s from +30 s
//   §7  the recon drone's 15 / 15 health and 100 s battery
//
// Everything else is a fixture value chosen to exercise a rule, and
// docs/viewer-scenarios.md says which is which. No fixture number
// contradicts a recorded one.
//
// WHERE THEY ARE. Every scenario plays on Al Basrah AAS v1, on the layer
// block a real recording of that layer carries — texture and world bounds
// included. Without a map the viewer draws a bare grid, and a reviewer cannot
// tell a 75 m mortar circle from a 450 m creeping barrage: the whole claim
// these fixtures make is that the shapes are drawn at true map scale, and
// that claim is unreadable against nothing. See MAP_ORIGIN below for how the
// scenarios were carried onto it.

import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

// ---- the clock -------------------------------------------------------------
// One base instant; every frame's wall clock is its game clock projected onto
// it, so `timestamp` and `worldTimeSec` never disagree.
const BASE_MS = Date.parse("2026-09-07T12:00:00.000Z");
const BASE_T = 1000;
const iso = (t) => new Date(BASE_MS + Math.round((t - BASE_T) * 1000)).toISOString();
const r1 = (v) => Math.round(v * 10) / 10;

// ---- the ground ------------------------------------------------------------
// The layer block a recording of Al Basrah AAS v1 carries, verbatim: the
// texture the viewer draws and the world bounds it draws it between. The map
// is a 4 km square from -200000 to 200000 on both axes.
const LAYER = {
  name: "Al Basrah AAS v1",
  mapId: "AlBasrah",
  mapName: "Al Basrah",
  gameMode: "AAS",
  texture: "T_AlBasrah_Minimap",
  topLeft: { x: -200000.0, y: -200000.0 },
  bottomRight: { x: 200000.0, y: 200000.0 },
};
/** The display name of the layer, which is what `gameState.mapName` carries —
 *  `layer.mapName` is the map's own name, and the two really do differ. */
const MAP_NAME = LAYER.name;

// Every scenario below was laid out around a local origin, with the cast at
// (0, 0) and each shape placed a few hundred metres off it. MAP_ORIGIN
// carries that whole arrangement onto the map in one piece, so every figure
// the spec recorded stays exactly what it recorded — a distance is a
// distance wherever it is drawn — and only the absolute placement moves.
//
// It puts the cast on the north-west side of the airfield, near where the
// session the spec was written from was fought (its players read about
// x -128000, y -143000). Every shape of every scenario lands on playable
// ground: the creeping barrage's 450 m path, the widest thing here, clears
// the western out-of-bounds band by more than 140 m at its closest, and the
// outermost point of any scenario — the static barrage's 225 m outer band —
// is still 475 m inside the layer bounds.
const MAP_ORIGIN = { x: -110000, y: -120000 };
const wx = (x) => MAP_ORIGIN.x + x;
const wy = (y) => MAP_ORIGIN.y + y;
/** A local (x, y, z) as the world position it becomes on the map. Every
 *  position in this file goes through here — the one exception is the drone
 *  call actor's literal (0, 0, z), which is a memory read, not a placement. */
const at = (x, y, z) => ({ x: wx(x), y: wy(y), z });

// ---- the cast --------------------------------------------------------------
const EOS = {
  ruby: "eos-0001-ruby",
  sol: "eos-0002-sol",
  wren: "eos-0003-wren",
  pike: "eos-0004-pike",
  vale: "eos-0005-vale",
  thorn: "eos-0006-thorn",
};
const NAMES = {
  [EOS.ruby]: "Ruby", [EOS.sol]: "Sol", [EOS.wren]: "Wren",
  [EOS.pike]: "Pike", [EOS.vale]: "Vale", [EOS.thorn]: "Thorn",
};
const ROSTER = [
  [EOS.ruby, 1, 1, 0, 0], [EOS.sol, 1, 1, 3000, 1500],
  [EOS.wren, 1, 2, -6000, 4000], [EOS.pike, 1, 2, -4500, 6500],
  [EOS.vale, 2, 1, 52000, 48000], [EOS.thorn, 2, 1, 50000, 51000],
];

function player(eosId, teamId, squadId, x, y, over = {}) {
  return {
    name: NAMES[eosId], eosId, playerId: null, teamId,
    roleId: "USMC_Rifleman_01", score: 0, ping: 30, isBot: false,
    clanTag: null, squadStateAddr: `0xsq${teamId}${squadId}`,
    teamStateAddr: `0xts${teamId}`, squadId, squadName: `Squad ${squadId}`,
    squadTeamId: teamId,
    stats: { fireTeamIndex: 0, fireTeamPosition: 0 },
    soldier: {
      addr: `0xsol-${eosId}`, classShort: "BP_Soldier_C", health: 100,
      breathHoldStamina: 100, stance: "standing",
      position: at(x, y, 100), yaw: 0, attached: false,
    },
    ...over,
  };
}
const PLAYERS = ROSTER.map(([e, t, s, x, y]) => player(e, t, s, x, y));

// ---- the server's own commander settings (§4, read live 2026-09-04) --------
const RULES = {
  enabled: true, votingTimeSec: 60, voteCooldownSec: 300,
  newCommanderExtensionSec: 300, minSquadSize: 2, minSquads: 3,
};

// ---- the action configs ----------------------------------------------------
// The UAV's four numbers and both display texts marked (spec) are recorded
// values; the rest are fixture values, chosen so the arithmetic has something
// to say. `displayName` for a config the spec does not quote is the class
// name's own distinctive segment, mechanically — never a label invented for
// the fixture.
const ACTIONS = {
  uav: {
    action: "CommandAction_UAV_MQ9_USMC_C",
    displayName: "MQ-9 UAV Recon",                      // spec §3
    categoryId: 0, enrouteSec: 30, activeSec: 300, cooldownSec: 600, // spec §3
  },
  strike: {
    action: "CommandAction_FA18CASStrafe_C",
    displayName: "FA18CASStrafe",
    categoryId: 0, enrouteSec: 45, activeSec: 60, cooldownSec: 600,
  },
  creep: {
    action: "CommandAction_Artillery_Creep_USMC_C",
    displayName: "Artillery Creep USMC",
    categoryId: 1, enrouteSec: 60, activeSec: 300, cooldownSec: 900, // 60 s: §6
  },
  barrage: {
    action: "CommandAction_Artillery_Barrage_USMC_C",
    displayName: "Artillery Barrage USMC",
    categoryId: 1, enrouteSec: 45, activeSec: 240, cooldownSec: 900,
  },
  mortar: {
    action: "CommandAction_Mortar_Barrage_INS_C",
    displayName: "Heavy Mortar Barrage",                // spec §3
    categoryId: 1, enrouteSec: 30, activeSec: 120, cooldownSec: 600, // 30 s: §6
  },
  drone: {
    action: "CommandAction_Drone_C",
    displayName: "Drone",
    categoryId: 2, enrouteSec: 10, activeSec: 420, cooldownSec: 600,
  },
};
// The category names are FText reads the spec quotes no value for; these are
// fixture labels.
const CATEGORIES = [
  { id: 0, name: "Air", intervalSec: 600, lastUseGameTime: null },
  { id: 1, name: "Artillery", intervalSec: 900, lastUseGameTime: null },
  { id: 2, name: "Support", intervalSec: 300, lastUseGameTime: null },
];

/** Every entry as the first claim leaves it: back-dated by enroute plus
 *  active, so each asset starts with its own cooldown to run (spec §3). */
function entriesAtClaim(claim, over = {}) {
  return Object.values(ACTIONS).map((a) => ({
    action: a.action,
    displayName: a.displayName,
    createdGameTime: r1(claim - (a.enrouteSec + a.activeSec)),
    remainingAtChange: 0,
    destroyedDuringActive: false,
    categoryId: a.categoryId,
    enrouteSec: a.enrouteSec,
    activeSec: a.activeSec,
    cooldownSec: a.cooldownSec,
    ...(over[a.action] ?? {}),
  }));
}
function categories(over = {}) {
  return CATEGORIES.map((c) => ({ ...c, ...(over[c.id] ?? {}) }));
}
const EMPTY_VOTE = {
  inProgress: false, timer: 0, endsGameTime: 0, nominees: [],
  cooldownActive: false, cooldownTimer: 0, cooldownEndsGameTime: 0,
};

// ---- teams -----------------------------------------------------------------
function team(id, over = {}) {
  const { commander, ...rest } = over;
  return {
    id, tickets: 300, score: 0, objectiveScore: 0,
    kills: 0, deaths: 0, woundeds: 0,
    factionId: id === 1 ? "USMC_LO_Motorized" : "PLA_LO_Motorized",
    commanderStateAddr: `0xcs${id}`,
    commanderName: null, commanderEosId: null,
    playerCount: 4, squadCount: 2, vehicleSlotCount: 0,
    deployableSlotCount: 0, roleSlotCount: 0,
    commander: {
      enabled: true, actionsEnabled: false,
      vote: { ...EMPTY_VOTE },
      cooldowns: { categories: categories(), actions: [] },
      ...commander,
    },
    ...rest,
  };
}
/** A team whose seat is held, with every entry cooling from `claim`. */
function seated(id, eosId, claim, over = {}) {
  const { commander = {}, ...rest } = over;
  return team(id, {
    commanderName: NAMES[eosId], commanderEosId: eosId,
    commander: {
      enabled: true, actionsEnabled: true,
      vote: { ...EMPTY_VOTE, cooldownActive: true, cooldownTimer: 217,
              cooldownEndsGameTime: 1343 },
      cooldowns: {
        categories: commander.categories ?? categories(),
        actions: commander.actions ?? entriesAtClaim(claim),
      },
      ...(commander.extra ?? {}),
    },
    ...rest,
  });
}

// ---- frames ----------------------------------------------------------------
function full(t, over = {}) {
  const { teams, ...rest } = over;
  return {
    timestamp: iso(t), server: "scenario", schemaVersion: "phase3-draft",
    tick: Math.round(t),
    counts: { playerStatesNonCDO: PLAYERS.length, soldiersLive: PLAYERS.length,
              vehicleSeatsLive: 0, totalUObjects: 0 },
    gameState: {
      serverName: "viewer scenario", maxPlayers: 100, tickRate: 50,
      matchId: "scenario", matchState: "InProgress",
      elapsedSec: Math.round(t - 900), isTicketBased: true,
      gameModeId: "AAS", numTeams: 2, maxFireTeamCount: 3, maxFireTeamSize: 4,
      timeOfCompletion: null, serverStartTimestamp: null,
      worldTimeSec: r1(t), mapName: MAP_NAME, gameModeName: "AAS",
      instanceClass: null, layer: LAYER,
      commanderRules: RULES,
    },
    teams: teams ?? [team(1), team(2)],
    squads: [], players: PLAYERS,
    vehicles: [], captureZones: [], markers: [], deployables: [],
    vehicleSpawners: [], rallyPoints: [], projectiles: [], damageEvents: [],
    ...rest,
  };
}
function pos(t, fullT, over = {}) {
  return {
    t: "pos", tick: Math.round(t * 4), fullTick: Math.round(fullT),
    timestamp: iso(t), players: [], vehicles: [], ...over,
  };
}

// ---- markers ---------------------------------------------------------------
function marker(id, type, x, y, over = {}) {
  return {
    id, type, team: 1, squad: 2, fireTeamId: -1,
    ownerPlayerStateAddr: "0xps-wren",
    position: at(x, y, 60), ...over,
  };
}
const PENDING = "BP_MapMarker_Command_SLRequest_C";
const APPROVED = "BP_MapMarker_Command_Request_C";
/** A request marker as the recorder writes one: `distance` and `addDistance`
 *  read 0, and `action` is an explicit null — it belongs to no config. */
const request = (id, type, x, y, over = {}) =>
  marker(id, type, x, y, { distance: 0, addDistance: 0, action: null, ...over });

// ---- command actors and drones ---------------------------------------------
function actor(id, cls, x, y, over = {}) {
  return {
    id, class: cls, team: 1, action: null, callerEosId: EOS.ruby,
    position: at(x, y, 400), yaw: 0, actionDestroyed: false,
    distance: 0, ...over,
  };
}
function drone(id, cls, x, y, z, over = {}) {
  return {
    id, class: cls, position: at(x, y, z), yaw: 45, dead: false,
    health: 15, maxHealth: 15,                                  // spec §7
    pilotEosId: EOS.pike, ownerEosId: EOS.pike,
    commandAction: null, batteryLifetimeMax: 100,               // spec §7
    lastHitByEosId: null, ...over,
  };
}

// ---- the scenarios ---------------------------------------------------------
const scenarios = [];
function scenario(name, title, shows, rule, lines) {
  scenarios.push({ name, title, shows, rule, lines });
}

// 1. A pending request: the dashed 50 m circle, and the panel row that says
//    a squad leader is waiting.
scenario(
  "request-pending", "A pending request",
  "One squad leader's request, still waiting: the 50 m circle in outline and "
  + "the panel row reading pending, with the age it has run.",
  "§9 Request circle; §9 Pending versus approved, and deletes",
  (() => {
    const L = [full(1000, { teams: [seated(1, EOS.ruby, 990), team(2)] })];
    for (let t = 1001; t <= 1010; t += 2) {
      L.push(full(t, {
        teams: [seated(1, EOS.ruby, 990), team(2)],
        markers: [request("0xreq-p", PENDING, 10000, 10000)],
      }));
    }
    return L;
  })());

// 2. The approval, with the pending twin still on the map — one request
//    changing state, for as long as the sweep takes.
scenario(
  "request-approved-with-twin", "An approval, twin and all",
  "The commander approves: the approved marker appears at once and the "
  + "pending twin stays until the sweep. Both are on the map for eight "
  + "frames and the viewer draws ONE request, with the inner tick that says "
  + "the twin is still there.",
  "§9 Pending versus approved, and deletes (T15, 2026-09-07)",
  (() => {
    const T = () => [seated(1, EOS.ruby, 990), team(2)];
    const L = [full(1000, { teams: T() })];
    for (let t = 1001; t <= 1004; t += 2)
      L.push(full(t, { teams: T(), markers: [request("0xreq-p", PENDING, 10000, 10000)] }));
    for (let t = 1005; t <= 1012; t += 3)
      L.push(full(t, { teams: T(), markers: [
        request("0xreq-p", PENDING, 10000, 10000),
        request("0xreq-a", APPROVED, 10000, 10000),
      ] }));
    for (let t = 1013; t <= 1018; t += 3)
      L.push(full(t, { teams: T(), markers: [request("0xreq-a", APPROVED, 10000, 10000)] }));
    return L;
  })());

// 3. A delete: gone at 55 s, well inside the 61 s fuse, with no approved twin
//    at its spot. One of the three removals measured on 2026-09-07.
scenario(
  "request-deleted", "A request taken back",
  "A pending request that vanishes 55 s after it was placed — inside the "
  + "61 s fuse, with no approved twin — so its squad leader deleted it. The "
  + "panel says so once the playhead passes the removal.",
  "§9 Pending versus approved, and deletes (decision D20)",
  (() => {
    const T = () => [seated(1, EOS.ruby, 990), team(2)];
    const L = [full(1000, { teams: T() })];
    // One frame a second for 55 s, then the frame it is gone on. Frames are
    // thinned to every fifth second so the fixture stays small; the ages are
    // the same either way.
    for (let t = 1001; t <= 1055; t += 11)
      L.push(full(t, { teams: T(), markers: [request("0xreq-p", PENDING, 10000, 10000)] }));
    L.push(full(1056, { teams: T() }));
    L.push(full(1060, { teams: T() }));
    return L;
  })());

// 4. The UAV's coverage circle, and the aircraft on station inside it.
scenario(
  "footprint-uav-coverage", "UAV coverage",
  "A CommandRadius_Friendly marker with the 16608 radius the commander "
  + "chose, drawn at true map scale, with the MQ-9 on station inside it and "
  + "its display name off the commander block.",
  "§9 Asset shapes (CommandRadius_Friendly); §5 distance",
  (() => {
    const claim = 990;
    const called = 1000;
    const T = () => [
      seated(1, EOS.ruby, claim, { commander: {
        actions: entriesAtClaim(claim, {
          [ACTIONS.uav.action]: { createdGameTime: called },
        }),
        categories: categories({ 0: { lastUseGameTime: called } }),
      } }),
      team(2),
    ];
    const L = [];
    for (let i = 0; i <= 5; i++) {
      const t = 1030 + i * 2;
      // On station: the UAV orbits, so its position moves a little.
      const a = (i / 10) * Math.PI * 2;
      L.push(full(t, {
        teams: T(),
        markers: [marker("0xm-uav", "BP_MapMarker_CommandRadius_Friendly_C",
                         20000, 20000, {
          distance: 16608, addDistance: 0, yaw: 0,   // spec §5
          action: ACTIONS.uav.action })],
        commandActions: [actor("0xa-uav", "BP_CommandActor_UAV_MQ9_C",
                               Math.round(20000 + Math.cos(a) * 8000),
                               Math.round(20000 + Math.sin(a) * 8000), {
          action: ACTIONS.uav.action,
          yaw: Math.round(((a * 180) / Math.PI + 90) % 360),
          position: at(Math.round(20000 + Math.cos(a) * 8000),
                       Math.round(20000 + Math.sin(a) * 8000), 9000) })],
      }));
    }
    return L;
  })());

// 5. The static barrage: a circle with an outer band the rounds stray into.
scenario(
  "footprint-static-barrage", "Static barrage",
  "A CommandRadius marker at 15000 with a 7500 outer band — the circle "
  + "filled, the band dashed outside it — and the guns sitting at their "
  + "origin through the fire plan.",
  "§9 Asset shapes (CommandRadius with addDistance); §5 distance/addDistance",
  (() => {
    const claim = 990, called = 1000;
    const T = () => [
      seated(1, EOS.ruby, claim, { commander: {
        actions: entriesAtClaim(claim, {
          [ACTIONS.barrage.action]: { createdGameTime: called },
        }),
        categories: categories({ 1: { lastUseGameTime: called } }),
      } }),
      team(2),
    ];
    const L = [];
    for (let i = 0; i <= 5; i++) {
      const t = 1045 + i * 5;
      L.push(full(t, {
        teams: T(),
        markers: [marker("0xm-bar", "BP_MapMarker_CommandRadius_C",
                         30000, -10000, {
          distance: 15000, addDistance: 7500, yaw: 0,     // spec §5
          action: ACTIONS.barrage.action })],
        commandActions: [actor("0xa-bar", "BP_CommandActor_Artillery_Radius_C",
                               30000, -10000, {
          action: ACTIONS.barrage.action, distance: 15000,
          originLocation: at(30000, -10000, 60),
          targetLocation: at(30000, -10000, 60),
          maxDropRadius: 15000, preWarningShells: 2, preWarningDelaySec: 12,
          shellsPerBarrage: 10, barrageCount: 8,
          currentPrewarningShells: i === 0 ? 0 : 2,
          currentBarrage: Math.max(0, i - 1),
          projectile: "BP_Projectile_155mm_C" })],
      }));
    }
    return L;
  })());

// 6. The creeping barrage: a 450 m path with its drop scatter, walked
//    through the warning shells and into the barrage on the 08-30 timings.
scenario(
  "footprint-creep-path", "Creeping barrage",
  "A CommandPath marker 45000 long with 7500 of drop scatter, drawn as a "
  + "band along its bearing, and the creep actor stepping through its plan: "
  + "the first warning shell at +59.7 s of a 60 s enroute, the second at "
  + "+67.1 s, the first barrage at +79.2 s — twelve seconds after it, the "
  + "delay the config carries.",
  "§9 Asset shapes (CommandPath); §9 Artillery timeline; §6 the creep's plan",
  (() => {
    const claim = 990, called = 1000;
    const T = () => [
      seated(1, EOS.ruby, claim, { commander: {
        actions: entriesAtClaim(claim, {
          [ACTIONS.creep.action]: { createdGameTime: called },
        }),
        categories: categories({ 1: { lastUseGameTime: called } }),
      } }),
      team(2),
    ];
    // The counter and the barrage move on the measured stamps (spec §6).
    const steps = [
      [1030, 0, 0], [1050, 0, 0], [1060, 1, 0], [1065, 1, 0],
      [1068, 2, 0], [1075, 2, 0], [1080, 2, 1], [1087, 2, 2],
      [1094, 2, 3], [1101, 2, 4],
    ];
    return steps.map(([t, warn, barrage]) => full(t, {
      teams: T(),
      markers: [marker("0xm-creep", "BP_MapMarker_CommandPath_C",
                       -20000, 0, {
        distance: 45000, addDistance: 7500, yaw: 90,      // spec §5
        action: ACTIONS.creep.action })],
      commandActions: [actor("0xa-creep", "BP_CommandActor_Artillery_Creep_C",
                             -20000, 0, {
        action: ACTIONS.creep.action, distance: 45000,    // spec §6
        yaw: 90,
        originLocation: at(-20000, 0, 60),
        targetLocation: at(-20000, 45000, 60),
        maxDropRadius: 7500, preWarningShells: 2, preWarningDelaySec: 12,
        shellsPerBarrage: 10, barrageCount: 8,
        currentPrewarningShells: warn, currentBarrage: barrage,
        projectile: "BP_Projectile_155mm_C" })],
    }));
  })());

// 7. A strike run: the line the aircraft flies, and the aircraft flying it.
scenario(
  "footprint-strike-line", "Strike run",
  "A CommandLine marker 6000 long along its bearing, with the aircraft "
  + "moving down the run frame by frame and its shot counter climbing.",
  "§9 Asset shapes (CommandLine); §5 distance; §6 the strike family's fields",
  (() => {
    const claim = 990, called = 1000;
    const T = () => [
      seated(1, EOS.ruby, claim, { commander: {
        actions: entriesAtClaim(claim, {
          [ACTIONS.strike.action]: { createdGameTime: called },
        }),
        categories: categories({ 0: { lastUseGameTime: called } }),
      } }),
      team(2),
    ];
    const L = [];
    for (let i = 0; i <= 6; i++) {
      const t = 1045 + i;
      const along = i / 10;
      L.push(full(t, {
        teams: T(),
        markers: [marker("0xm-line", "BP_MapMarker_CommandLine_C",
                         0, 40000, {
          distance: 6000, addDistance: 0, yaw: 0,          // spec §5
          action: ACTIONS.strike.action })],
        commandActions: [actor("0xa-fa18", "BP_CommandActor_FA18_Strafe_C",
                               Math.round(-12000 + along * 24000), 40000, {
          action: ACTIONS.strike.action, distance: 6000, yaw: 0,
          position: at(Math.round(-12000 + along * 24000), 40000, 6000),
          shotsMade: Math.min(120, Math.round(along * 130)), maxShots: 120,
          splineDistance: Math.round(along * 30000),
          originLocation: at(-12000, 40000, 6000) })],
      }));
    }
    return L;
  })());

// 8. The mortar: a fixed circle with its band, and a fire plan with no
//    warning phase at all.
scenario(
  "footprint-mortar-radius", "Mortar barrage",
  "The mortar's fixed 7500 circle with its 4500 outer band, and its plan "
  + "running with no warning phase: the counter and the first barrage both "
  + "reach 1 at the enroute, then eight barrages of ten follow every 8-9 s.",
  "§9 Asset shapes (CommandRadius); §9 Artillery timeline (the mortar's "
  + "warning-free one); §6 the mortar's values",
  (() => {
    const claim = 990, called = 1000;
    const T = () => [
      seated(1, EOS.ruby, claim, { commander: {
        actions: entriesAtClaim(claim, {
          [ACTIONS.mortar.action]: { createdGameTime: called },
        }),
        categories: categories({ 1: { lastUseGameTime: called } }),
      } }),
      team(2),
    ];
    const L = [];
    // Enroute is 30 s; the counter and the first barrage both reach 1 there,
    // and the barrages advance every 8-9 s after it (spec §6).
    const stamps = [1010, 1025, 1030, 1039, 1047, 1056, 1064, 1073, 1081, 1090];
    stamps.forEach((t, i) => {
      const barrage = t < 1030 ? 0 : Math.min(8, i - 1);
      L.push(full(t, {
        teams: T(),
        markers: [marker("0xm-mortar", "BP_MapMarker_CommandRadius_C",
                         40000, 40000, {
          distance: 7500, addDistance: 4500, yaw: 0,      // spec §5
          action: ACTIONS.mortar.action })],
        commandActions: [actor("0xa-mortar", "BP_CommandActor_Mortar_Radius_C",
                               40000, 40000, {
          action: ACTIONS.mortar.action, distance: 7500,
          originLocation: at(40000, 40000, 60),
          targetLocation: at(40000, 40000, 60),
          maxDropRadius: 1.0, preWarningShells: 0, preWarningDelaySec: 0,
          shellsPerBarrage: 10, barrageCount: 8,          // spec §6
          currentPrewarningShells: t < 1030 ? 0 : 1,
          currentBarrage: Math.max(0, barrage),
          projectile: "BP_Projectile_Mortar_C" })],
      }));
    });
    return L;
  })());

// 9. A precision strike's two aim points — the fourth shape, which no other
//    scenario carries.
scenario(
  "footprint-aim-line", "Precision aim line",
  "A CommandLineRadius marker: two aim points, at 0 and 4475 along the "
  + "marker's bearing, with the line between them.",
  "§9 Asset shapes (CommandLineRadius); §5 distance",
  (() => {
    const claim = 990;
    const T = () => [seated(1, EOS.ruby, claim), team(2)];
    const L = [];
    for (let i = 0; i <= 2; i++) {
      L.push(full(1000 + i * 2, {
        teams: T(),
        markers: [marker("0xm-aim", "BP_MapMarker_CommandLineRadius_C",
                         55000, 10000, {
          distance: 4475, addDistance: 0, yaw: 135,       // spec §5
          action: ACTIONS.strike.action })],
      }));
    }
    return L;
  })());

// 10. A vote in progress, with the tallies the game keeps.
scenario(
  "vote-in-progress", "A commander vote",
  "A vote open on team 1 with three nominees and their live tallies, the "
  + "timer counting the 60 s window down, and the entries persisting into "
  + "the frames after it resolves — the game's own state, recorded as read.",
  "§9 Commander seat and votes; §3 the vote block",
  (() => {
    const L = [];
    const opened = 1000;
    const ends = opened + RULES.votingTimeSec;          // spec §4: 60 s
    const tallies = [[0, 0, 0], [1, 0, 0], [2, 1, 0], [3, 1, 0], [3, 2, 1]];
    tallies.forEach((v, i) => {
      const t = opened + i * 12;
      L.push(full(t, { teams: [
        team(1, { commander: {
          enabled: true, actionsEnabled: false,
          vote: {
            inProgress: true, timer: Math.max(0, ends - t), endsGameTime: ends,
            nominees: [
              { eosId: EOS.ruby, name: NAMES[EOS.ruby], votes: v[0] },
              { eosId: EOS.sol, name: NAMES[EOS.sol], votes: v[1] },
              { eosId: EOS.wren, name: NAMES[EOS.wren], votes: v[2] },
            ],
            cooldownActive: false, cooldownTimer: 0, cooldownEndsGameTime: 0,
          },
          cooldowns: { categories: categories(), actions: [] },
        } }),
        team(2),
      ] }));
    });
    // Resolved: the seat is held, the cooldown is running, and the nominee
    // entries still carry the final tallies.
    for (let i = 0; i < 3; i++) {
      const t = ends + 1 + i * 5;
      L.push(full(t, { teams: [
        seated(1, EOS.ruby, ends, { commander: {
          extra: { vote: {
            inProgress: false, timer: 0, endsGameTime: ends,
            nominees: [
              { eosId: EOS.ruby, name: NAMES[EOS.ruby], votes: 3 },
              { eosId: EOS.sol, name: NAMES[EOS.sol], votes: 2 },
              { eosId: EOS.wren, name: NAMES[EOS.wren], votes: 1 },
            ],
            cooldownActive: true,
            cooldownTimer: Math.max(0, RULES.voteCooldownSec - (t - ends)),
            cooldownEndsGameTime: ends + RULES.voteCooldownSec,
          } },
        } }),
        team(2),
      ] }));
    }
    return L;
  })());

// 11. A seated commander: every asset on its own cooldown, one of them
//     ready, one held by the category gate rather than its own window.
scenario(
  "seat-cooling-and-ready", "Cooling and ready",
  "The seat held and the cooldown panel doing its arithmetic: the UAV "
  + "already ready, the strike held by the AIR category gate rather than by "
  + "its own window, and the artillery still on its own.",
  "§9 Ready-in arithmetic (the later of entry and category, the category gate)",
  (() => {
    const claim = 400;
    const L = [];
    for (let i = 0; i <= 4; i++) {
      const t = 1000 + i * 15;
      L.push(full(t, { teams: [
        seated(1, EOS.ruby, claim, { commander: {
          actions: entriesAtClaim(claim, {
            // Called long ago: its own window has run.
            [ACTIONS.uav.action]: { createdGameTime: 200 },
            // Its own window has run too — the category gate is what holds
            // it, because the UAV's call stamped the same category.
            [ACTIONS.strike.action]: { createdGameTime: 200 },
            // Called at 900: still cooling on its own arithmetic.
            [ACTIONS.creep.action]: { createdGameTime: 900 },
          }),
          categories: categories({
            0: { lastUseGameTime: 950 },     // AIR: open at 1550
            1: { lastUseGameTime: 900 },     // ARTILLERY: open at 1800
          }),
        } }),
        team(2),
      ] }));
    }
    return L;
  })());

// 12. A commander change: the game moves the stamps, and the panel follows
//     them with no special case of its own.
scenario(
  "commander-change-restamp", "A commander change",
  "The seat changes hands at 1200. An entry still cooling has its "
  + "`createdGameTime` pushed forward by the server's 300 s extension and "
  + "`remainingAtChange` written with the time it had left; an entry whose "
  + "own cooldown had run is re-stamped to come back 300 s after the change. "
  + "The category stamps do not move, and the viewer reads the moved stamps.",
  "§9 Ready-in arithmetic (at a commander change)",
  (() => {
    const claim = 400, change = 1200;
    const before = () => seated(1, EOS.ruby, claim, { commander: {
      actions: entriesAtClaim(claim, {
        [ACTIONS.creep.action]: { createdGameTime: 900 },   // ready at 2160
        [ACTIONS.uav.action]: { createdGameTime: 200 },     // ready at 1130
      }),
      categories: categories({ 0: { lastUseGameTime: 950 },
                               1: { lastUseGameTime: 900 } }),
    } });
    const after = () => seated(1, EOS.sol, claim, { commander: {
      actions: entriesAtClaim(claim, {
        // Still cooling at the change: 960 s left, stamp pushed by 300.
        [ACTIONS.creep.action]: {
          createdGameTime: 1200, remainingAtChange: 960 },
        // Already ready: re-stamped to become ready 300 s after the change.
        [ACTIONS.uav.action]: {
          createdGameTime: change + RULES.newCommanderExtensionSec
            - (ACTIONS.uav.enrouteSec + ACTIONS.uav.activeSec
               + ACTIONS.uav.cooldownSec),
          remainingAtChange: 0 },
      }),
      // Untouched by the change.
      categories: categories({ 0: { lastUseGameTime: 950 },
                               1: { lastUseGameTime: 900 } }),
    } });
    const L = [];
    for (let t = 1170; t < change; t += 15) L.push(full(t, { teams: [before(), team(2)] }));
    for (let t = change; t <= change + 60; t += 20) L.push(full(t, { teams: [after(), team(2)] }));
    return L;
  })());

// 13. A step-down: the seat clears, every entry keeps what it had left, and
//     the last vote's cooldown still refuses a fresh claim.
scenario(
  "commander-step-down", "A step-down",
  "The commander stands down at 1200. The seat reads an explicit empty — "
  + "read successfully, nobody in it — every entry's `remainingAtChange` is "
  + "written with the time it had left and nothing else moves, and the last "
  + "vote's cooldown still refuses a claim. The panel shows what a fresh "
  + "claim would leave on each entry, labelled as the projection it is.",
  "§9 Commander seat and votes (a step-down); §9 Ready-in arithmetic (a "
  + "re-claim)",
  (() => {
    const claim = 400, down = 1200;
    const held = () => seated(1, EOS.ruby, claim, { commander: {
      actions: entriesAtClaim(claim, {
        [ACTIONS.creep.action]: { createdGameTime: 900 },
        [ACTIONS.uav.action]: { createdGameTime: 1000 },
      }),
      categories: categories({ 1: { lastUseGameTime: 900 } }),
    } });
    const empty = (t) => team(1, {
      commanderName: null, commanderEosId: null,
      commander: {
        enabled: true, actionsEnabled: false,
        vote: { ...EMPTY_VOTE, cooldownActive: true,
                cooldownTimer: Math.max(0, 1500 - t),
                cooldownEndsGameTime: 1500 },
        cooldowns: {
          categories: categories({ 1: { lastUseGameTime: 900 } }),
          // Nothing moved but `remainingAtChange`, written with what each
          // entry had left at the step-down.
          actions: entriesAtClaim(claim, {
            [ACTIONS.creep.action]: {
              createdGameTime: 900, remainingAtChange: 960 },
            [ACTIONS.uav.action]: {
              createdGameTime: 1000, remainingAtChange: 730 },
          }),
        },
      },
    });
    const L = [];
    for (let t = 1170; t < down; t += 15) L.push(full(t, { teams: [held(), team(2)] }));
    for (let t = down; t <= down + 80; t += 20) L.push(full(t, { teams: [empty(t), team(2)] }));
    return L;
  })());

// 14. A UAV shot down: `actionDestroyed` true while the actor lingers, and
//     the stamps that do NOT move because of it.
scenario(
  "uav-shot-down", "A UAV shot down",
  "The UAV is hit at +67 s of a 330 s window. `actionDestroyed` reads true "
  + "while the actor lingers, so the map strikes it through and the entry's "
  + "`destroyedDuringActive` says the same — and `createdGameTime` does not "
  + "move, so the ready-in is the unchanged arithmetic. Who shot it down is "
  + "not shown, because nothing in the recording says.",
  "§9 Shoot-downs (decision D18); §9 Ready-in arithmetic (destroyed "
  + "mid-flight)",
  (() => {
    const claim = 990, called = 1000, hit = 1067;
    const T = (destroyed) => [
      seated(1, EOS.ruby, claim, { commander: {
        actions: entriesAtClaim(claim, {
          [ACTIONS.uav.action]: {
            createdGameTime: called, destroyedDuringActive: destroyed },
        }),
        categories: categories({ 0: { lastUseGameTime: called } }),
      } }),
      team(2),
    ];
    const L = [];
    const uav = (t, destroyed) => {
      const a = ((t - 1030) / 60) * Math.PI * 2;
      return full(t, {
        teams: T(destroyed),
        markers: [marker("0xm-uav", "BP_MapMarker_CommandRadius_Friendly_C",
                         20000, 20000, {
          distance: 19958, addDistance: 0, yaw: 0,        // spec §5
          action: ACTIONS.uav.action })],
        commandActions: [actor("0xa-uav", "BP_CommandActor_UAV_MQ9_C",
                               20000, 20000, {
          action: ACTIONS.uav.action,
          position: at(Math.round(20000 + Math.cos(a) * 9000),
                       Math.round(20000 + Math.sin(a) * 9000), 9000),
          yaw: Math.round((((a * 180) / Math.PI) + 90 + 360) % 360),
          actionDestroyed: destroyed })],
      });
    };
    for (let t = 1035; t < hit; t += 12) L.push(uav(t, false));
    // It lingers 16 s with the flag set, then the actor is gone.
    for (let t = hit; t <= hit + 16; t += 8) L.push(uav(t, true));
    for (let t = hit + 20; t <= hit + 40; t += 20) {
      L.push(full(t, { teams: T(true) }));
    }
    return L;
  })());

// 15. A recon drone's whole life at 4 Hz: flown, landed and exited, then
//     shot down — with the killer taken from the first sample carrying
//     `dead`, not from the second shooter who hit the falling pawn.
scenario(
  "drone-recon-life", "A recon drone's life",
  "A recon drone deployed, flown, landed and exited (no pilot, owner "
  + "unchanged), then shot down. The 4 Hz line carries the movement and the "
  + "death, and the killer is the hitter of the FIRST sample reading dead — "
  + "a second shooter moves the pointer 1.1 s later, inside the full frame's "
  + "own gap.",
  "§9 Drones (stop drawing at dead, team from owner, remaining flight time, "
  + "the killer at 4 Hz, decision D19)",
  (() => {
    const L = [];
    const path = (i) => ({ x: 5000 + i * 900, y: -20000 + i * 400, z: 3500 });
    // Deploy at 1001; the battery is 100 s.
    L.push(full(1000));
    for (let f = 0; f < 6; f++) {
      const t = 1001 + f * 2;
      const p = path(f * 4);
      const flying = f < 4;
      L.push(full(t, {
        drones: [drone("0xdrone-1", "BP_FlyingDrone_Recoverable_C", p.x, p.y, p.z, {
          // Landed and exited on the last two full frames: no pilot, and the
          // owner unchanged — a drone cannot change hands.
          pilotEosId: flying ? EOS.pike : null,
          yaw: 45 + f * 15 })],
      }));
      // Four position samples between full frames.
      for (let s = 1; s <= 3; s++) {
        const st = t + s * 0.25;
        const pp = path(f * 4 + s);
        L.push(pos(st, t, { drones: [
          { id: "0xdrone-1", x: wx(pp.x), y: wy(pp.y), z: pp.z,
            yaw: 45 + f * 15 + s },
        ] }));
      }
    }
    // The kill: the first sample carrying `dead` names the killer, and the
    // sample 1.1 s later names a second shooter — both recorded as read.
    const deathFull = 1013;
    const dp = path(24);
    L.push(full(deathFull, {
      drones: [drone("0xdrone-1", "BP_FlyingDrone_Recoverable_C",
                     dp.x, dp.y, dp.z, { pilotEosId: null })],
    }));
    L.push(pos(deathFull + 0.25, deathFull, { drones: [
      { id: "0xdrone-1", x: wx(dp.x + 200), y: wy(dp.y), z: 3400,
        yaw: 140 }] }));
    L.push(pos(deathFull + 0.5, deathFull, { drones: [
      { id: "0xdrone-1", x: wx(dp.x + 400), y: wy(dp.y), z: 2600, yaw: 150,
        dead: true, lastHitBy: EOS.vale }] }));
    L.push(pos(deathFull + 0.75, deathFull, { drones: [
      { id: "0xdrone-1", x: wx(dp.x + 500), y: wy(dp.y), z: 1500, yaw: 160,
        dead: true, lastHitBy: EOS.vale }] }));
    L.push(pos(deathFull + 1.5, deathFull, { drones: [
      { id: "0xdrone-1", x: wx(dp.x + 600), y: wy(dp.y), z: 400, yaw: 170,
        dead: true, lastHitBy: EOS.thorn }] }));
    // The full frame a second later carries the two-way reading.
    L.push(full(deathFull + 2, {
      drones: [drone("0xdrone-1", "BP_FlyingDrone_Recoverable_C",
                     dp.x + 600, dp.y, 200, {
        dead: true, health: 0, pilotEosId: null,
        lastHitByEosId: EOS.thorn })],
    }));
    // Then the pawn is freed and the list goes with it.
    L.push(full(deathFull + 4));
    return L;
  })());

// 16. The commander's drone, and the call actor the viewer must NOT draw.
scenario(
  "drone-commander-call", "A commander drone and its call actor",
  "The commander's drone in the air beside the CALL actor that spawned it. "
  + "The call actor's root position reads (0, 0, z) and means nothing, and "
  + "its life outruns the pawn's — so nothing is drawn for it at the map "
  + "origin, and the drone the viewer draws is the pawn. The pawn's class "
  + "declares no battery, so its budget is the calling action's 420 s "
  + "active window.",
  "§9 Drones (the call actor draws nothing, the commander drone's budget); "
  + "§6 the call actor's (0, 0, z)",
  (() => {
    const claim = 990, called = 1000;
    const T = () => [
      seated(1, EOS.ruby, claim, { commander: {
        actions: entriesAtClaim(claim, {
          [ACTIONS.drone.action]: { createdGameTime: called },
        }),
        categories: categories({ 2: { lastUseGameTime: called } }),
      } }),
      team(2),
    ];
    const L = [full(1005, { teams: T() })];
    for (let i = 0; i <= 5; i++) {
      const t = 1012 + i * 3;
      const cd = drone("0xdrone-cmd", "BP_FlyingDrone_C",
                       -8000 + i * 1200, 26000 + i * 500, 3000, {
        pilotEosId: EOS.ruby, ownerEosId: EOS.ruby,
        commandAction: ACTIONS.drone.action, yaw: 20 + i * 8 });
      // The commander drone's class declares no battery at all.
      delete cd.batteryLifetimeMax;
      L.push(full(t, {
        teams: T(),
        drones: [cd],
        // The call actor: (0, 0, z), a null `Action Destroyed`, and an owner
        // that is the COMMANDER — on the pawn the same-named field is the
        // deployer instead.
        commandActions: [actor("0xa-dronecall", "BP_CommandActor_Drone_C",
                               0, 0, {
          action: ACTIONS.drone.action,
          // Deliberately NOT `at(...)`: this is the literal (0, 0, z) the
          // memory read returns, which on this layer is the map's own centre,
          // 1.5 km from the pawn. Drawing it would put a drone in the middle
          // of Al Basrah — which is exactly the mistake the rule forbids.
          position: { x: 0, y: 0, z: 220 },
          ownerEosId: EOS.ruby })],
      }));
    }
    return L;
  })());

// ---- write -----------------------------------------------------------------
// Written compact on purpose. THIS FILE is the readable form of a scenario —
// a hand-built sequence you can follow line by line — and the JSON beside it
// is its output, which the viewer loads and nobody edits. Pretty-printing it
// tripled the bytes for no reader.
mkdirSync(HERE, { recursive: true });
for (const s of scenarios) {
  writeFileSync(join(HERE, `${s.name}.json`),
                JSON.stringify(s) + "\n", "utf8");
}
// The list the picker shows, without its frames: a few hundred bytes that
// can be imported eagerly, so the menu is there before any scenario is.
writeFileSync(
  join(HERE, "manifest.json"),
  JSON.stringify(scenarios.map(({ name, title, shows, rule, lines }) => ({
    name, title, shows, rule, lineCount: lines.length,
  })), null, 1) + "\n", "utf8");
console.log(`${scenarios.length} scenarios written to ${HERE}`);
for (const s of scenarios) {
  console.log(`  ${s.name}  (${s.lines.length} lines)`);
}
