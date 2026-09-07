// The scenario picker: every commander rule, playable in the browser with no
// server, no live match and no recording.
//
// Reached by putting `?scenario` in the url — bare for the list,
// `?scenario=<name>` to open one. It is a review surface, not a product
// surface: it mounts only when that parameter is there, and the frames it
// loads go through the SAME reconstructor and the SAME render path a real
// .sqrx does, so what the reviewer sees is what a recording would draw.

import { useEffect, useState } from "react";
import {
  loadScenarioLines, SCENARIOS, type ScenarioMeta,
} from "../state/__fixtures__/commander";
import { ReplayReconstructor } from "../state/replayReconstruct";
import { useViewerStore } from "../state/viewerStore";
import type { Snapshot } from "../state/types";

/** Is the scenario entry asked for at all? Read once per render from the url,
 *  so nothing about it is in the store and no build ships a dead route. */
export function scenarioParam(): string | null {
  const url = new URL(window.location.href);
  if (!url.searchParams.has("scenario")) return null;
  return url.searchParams.get("scenario") ?? "";
}

export function Scenarios() {
  const [name, setName] = useState<string>(() => scenarioParam() ?? "");
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(true);
  const setReplay = useViewerStore((s) => s.setReplay);
  const setMode = useViewerStore((s) => s.setMode);
  const setStatus = useViewerStore((s) => s.setStatus);
  const ingestLive = useViewerStore((s) => s.ingestLive);

  useEffect(() => {
    if (!name) return;
    let cancelled = false;
    setError(null);
    loadScenarioLines(name).then((lines) => {
      if (cancelled) return;
      if (!lines) { setError(`no scenario named "${name}"`); return; }
      // The same two-tier fold a real recording goes through: full frames
      // pass, position lines are spliced onto the last full one. A scenario
      // that carries 4 Hz lines therefore exercises the reconstructor too.
      const recon = new ReplayReconstructor();
      const frames: Snapshot[] = [];
      for (const line of lines) {
        const snap = recon.push(line);
        if (snap) frames.push(snap);
      }
      if (!frames.length) { setError(`"${name}" has no frames`); return; }
      // `id` stays null on purpose: the replay loader fetches whenever it is
      // set, and there is nothing here to fetch.
      setMode("replay");
      setReplay((r) => ({
        ...r, id: null, frames, currentIdx: 0, playing: false,
        speed: 1, baseWallMs: 0, baseSnapMs: 0,
      }));
      ingestLive(frames[0]!);
      ingestLive(frames[0]!);
      setStatus("replay");
    });
    return () => { cancelled = true; };
  }, [name, setMode, setReplay, setStatus, ingestLive]);

  const pick = (n: string) => {
    const url = new URL(window.location.href);
    if (n) url.searchParams.set("scenario", n);
    else url.searchParams.set("scenario", "");
    window.history.replaceState({}, "", url.toString());
    setName(n);
  };

  const current: ScenarioMeta | undefined = SCENARIOS.find((s) => s.name === name);

  return (
    <div id="scenarios" className={open ? "" : "shut"}>
      <header>
        <h2>Viewer scenarios</h2>
        <button onClick={() => setOpen((o) => !o)}
                title={open ? "collapse" : "expand"}>{open ? "–" : "+"}</button>
      </header>
      {open && (
        <div className="scn-body">
          {error && <div className="scn-error">{error}</div>}
          {current && (
            <div className="scn-current">
              <div className="scn-shows">{current.shows}</div>
              <div className="scn-rule">{current.rule}</div>
            </div>
          )}
          {!name && !error && (
            <div className="scn-hint">
              pick one — each is a small recording that demonstrates one rule
              of the capture spec, with no server behind it
            </div>
          )}
          <ul className="scn-list">
            {SCENARIOS.map((s) => (
              <li key={s.name}>
                <button className={s.name === name ? "on" : ""}
                        onClick={() => pick(s.name)}
                        title={s.shows}>
                  <span className="scn-title">{s.title}</span>
                  <span className="scn-name">{s.name}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
