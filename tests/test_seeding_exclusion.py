"""Seeding matches are excluded at the source — and nothing else is.

A seed session is a two-player warm-up that can run for six hours and then
arrive in the archive dressed as a match: a .sqrx nobody will ever watch, a
stats row that drags every average it touches, an upload nobody asked for. The
game names the mode itself, so the decision is read rather than inferred.

The tests that matter most here are the ones about what must NOT be skipped. A
recording is not recoverable after the fact, so every ambiguous reading records:
the no-guess rule applies to skipping exactly as hard as it applies to display.
"""
from __future__ import annotations

import json
import sqlite3

from sqreader.recorder import (
    _MATCH_END_CONFIRM_TICKS,
    _handle_snap,
    finalize_recording,
    write_position_frame,
)
from sqreader.recording_lifecycle import is_excluded_match
from sqreader.stats import StatsStore

SEED_MODES = frozenset({"Seed"})


def _snap(state="InProgress", match_id="match-a", *, tick=None,
          mode="RAAS", map_name="Fallujah", layer="Fallujah_RAAS_v1"):
    gs = {
        "matchState": state,
        "matchId": match_id,
        "mapName": map_name,
        "gameModeName": mode,
        "gameModeId": f"BP_GameMode_{mode}_C",
    }
    if layer is not None:
        gs["layer"] = {"name": layer}
    snap = {"timestamp": "2026-08-28T12:00:00+00:00", "gameState": gs,
            "players": [], "teams": [], "damageEvents": []}
    if tick is not None:
        snap["tick"] = tick
    return snap


def _state_box():
    return {
        "current": None,
        "last_state": None,
        "inactive_ticks": 0,
        "pending_match_id": None,
        "pending_match_buffer": [],
        "last_tick": None,
        "tick_sequence_required": False,
        "missing_tick_warned": False,
        "excluded_match_ids": {},
    }


def _step(tmp_path, box, buf, snap, *, modes=SEED_MODES, patterns=()):
    _handle_snap(
        snap=snap,
        raw_line=json.dumps(snap),
        state_box=box,
        out_dir=tmp_path,
        server_id="test-server",
        min_ticks=0,
        filename_buffer=buf,
        excluded_game_modes=modes,
        excluded_layer_patterns=patterns,
    )


def _run(tmp_path, snap, *, ticks=None, modes=SEED_MODES, patterns=()):
    """Feed one snap enough times to get past the open confirmation."""
    box, buf = _state_box(), []
    for _ in range(ticks or _MATCH_END_CONFIRM_TICKS):
        _step(tmp_path, box, buf, snap, modes=modes, patterns=patterns)
    return box, buf


# --------------------------------------------------------------------------
# the predicate itself
# --------------------------------------------------------------------------

def test_the_games_own_mode_field_is_the_key():
    assert is_excluded_match(_snap(mode="Seed"), game_modes=SEED_MODES,
                             layer_patterns=())
    assert not is_excluded_match(_snap(mode="RAAS"), game_modes=SEED_MODES,
                                 layer_patterns=())


def test_the_mode_match_is_exact_and_case_insensitive():
    """'seed' is the same mode as 'Seed'. 'Seeding Layer' is not — a substring
    rule would have swallowed anything with the word in it."""
    assert is_excluded_match(_snap(mode="seed"), game_modes=SEED_MODES,
                             layer_patterns=())
    assert not is_excluded_match(_snap(mode="Seeding Layer"),
                                 game_modes=SEED_MODES, layer_patterns=())


def test_a_finished_recordings_sidecar_answers_the_same_question():
    """The backfill sees a .meta.json, the recorder sees a snapshot, and the
    same match must get the same answer from both."""
    meta = {"gameMode": "Seed", "mapName": "Fallujah Seed v1",
            "layerName": "Fallujah Seed v1"}
    assert is_excluded_match(meta, game_modes=SEED_MODES, layer_patterns=())
    meta["gameMode"] = "RAAS"
    assert not is_excluded_match(meta, game_modes=SEED_MODES,
                                 layer_patterns=())


def test_layer_patterns_are_the_override_hatch():
    snap = _snap(mode="Skirmish", layer="Sumari_Skirmish_v1")
    assert not is_excluded_match(snap, game_modes=SEED_MODES,
                                 layer_patterns=())
    assert is_excluded_match(snap, game_modes=SEED_MODES,
                             layer_patterns=("*skirmish*",))


def test_a_pattern_falls_back_to_the_map_name_when_no_layer_resolved():
    snap = _snap(mode="Skirmish", layer=None, map_name="Sumari Skirmish v1")
    assert is_excluded_match(snap, game_modes=SEED_MODES,
                             layer_patterns=("*Skirmish*",))


def test_an_unreadable_tick_records():
    """Fail open. A torn read that leaves every name null is not evidence of a
    seed session, and a recording is not recoverable after the fact."""
    torn = {"gameState": {"matchState": "InProgress", "matchId": "m",
                          "mapName": None, "gameModeName": None,
                          "gameModeId": None}}
    assert not is_excluded_match(torn, game_modes=SEED_MODES,
                                 layer_patterns=("*seed*",))
    assert not is_excluded_match({}, game_modes=SEED_MODES,
                                 layer_patterns=("*",))
    assert not is_excluded_match(None, game_modes=SEED_MODES,
                                 layer_patterns=("*",))


def test_configuring_nothing_excludes_nothing():
    assert not is_excluded_match(_snap(mode="Seed"), game_modes=frozenset(),
                                 layer_patterns=())


# --------------------------------------------------------------------------
# recorder
# --------------------------------------------------------------------------

def test_a_seed_match_opens_no_recording(tmp_path):
    box, _ = _run(tmp_path, _snap(mode="Seed"))
    assert box["current"] is None
    assert list(tmp_path.glob("*.sqrx")) == []
    assert list(tmp_path.glob("*.meta.json")) == []


def test_a_competitive_match_still_records(tmp_path):
    box, _ = _run(tmp_path, _snap(mode="RAAS"))
    assert box["current"] is not None
    finalize_recording(box["current"], reason="test cleanup")


def test_a_configured_layer_pattern_skips_the_recording(tmp_path):
    box, _ = _run(tmp_path, _snap(mode="Skirmish", layer="Sumari_Skirmish_v1"),
                  patterns=("*Skirmish*",))
    assert box["current"] is None
    assert list(tmp_path.glob("*.sqrx")) == []


def test_a_torn_tick_records_rather_than_risking_the_match(tmp_path):
    snap = _snap(mode="RAAS")
    snap["gameState"]["gameModeName"] = None
    snap["gameState"]["gameModeId"] = None
    snap["gameState"]["mapName"] = None
    snap["gameState"]["layer"] = {"name": None}
    box, _ = _run(tmp_path, snap, ticks=10)   # buffered until the name cap
    assert box["current"] is not None
    finalize_recording(box["current"], reason="test cleanup")


def test_position_frames_go_nowhere_for_an_excluded_match(tmp_path):
    """The 4 Hz sampler is a side channel that appends to whatever writer is
    open. No writer, no frames — asserted rather than assumed."""
    box, _ = _run(tmp_path, _snap(mode="Seed"))
    assert write_position_frame(box, json.dumps({"t": "pos"}) + "\n") == 0
    assert list(tmp_path.glob("*.sqrx")) == []


def test_the_decision_survives_a_mid_match_flap(tmp_path):
    """A seed match whose mode momentarily reads RAAS must not suddenly start
    recording from the middle."""
    box, buf = _run(tmp_path, _snap(mode="Seed"))
    for _ in range(_MATCH_END_CONFIRM_TICKS * 3):
        _step(tmp_path, box, buf, _snap(mode="RAAS"))    # same matchId
    assert box["current"] is None
    assert list(tmp_path.glob("*.sqrx")) == []


def test_a_transient_seed_reading_never_closes_a_live_recording(tmp_path):
    """The dangerous direction. A real match already recording must not be cut
    in half by one tick whose mode field read 'Seed'."""
    box, buf = _run(tmp_path, _snap(mode="RAAS"))
    current = box["current"]
    assert current is not None
    before = current.tick_count
    for _ in range(_MATCH_END_CONFIRM_TICKS * 2):
        _step(tmp_path, box, buf, _snap(mode="Seed"))    # same matchId
    assert box["current"] is current
    assert current.tick_count == before + _MATCH_END_CONFIRM_TICKS * 2
    finalize_recording(current, reason="test cleanup")


def test_a_seed_match_after_a_real_one_closes_the_real_one(tmp_path):
    """The seed session supersedes the match without going idle first — the
    recording that was open still has to be finalized."""
    box, buf = _run(tmp_path, _snap(mode="RAAS", match_id="real"))
    current = box["current"]
    assert current is not None
    for _ in range(_MATCH_END_CONFIRM_TICKS * 2):
        _step(tmp_path, box, buf, _snap(mode="Seed", match_id="seed"))
    assert box["current"] is None
    metas = list(tmp_path.glob("*.meta.json"))
    assert len(metas) == 1
    meta = json.loads(metas[0].read_text(encoding="utf-8"))
    assert meta["matchId"] == "real"
    assert meta["gameMode"] == "RAAS"


# --------------------------------------------------------------------------
# stats
# --------------------------------------------------------------------------

def _store(tmp_path, **kw):
    return StatsStore(tmp_path / "s.db", server_id="t", elo=False,
                      seeding_game_modes=("Seed",), **kw)


def _matches(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "s.db"))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM matches")]
    finally:
        conn.close()


def test_a_seed_match_opens_no_stats_row(tmp_path):
    s = _store(tmp_path)
    try:
        for i in range(5):
            s.record_tick(_snap(mode="Seed", tick=i))
    finally:
        s.close()
    assert _matches(tmp_path) == []


def test_a_competitive_match_still_gets_a_stats_row(tmp_path):
    s = _store(tmp_path)
    try:
        s.record_tick(_snap(mode="RAAS", tick=1))
    finally:
        s.close()
    rows = _matches(tmp_path)
    assert [r["match_id"] for r in rows] == ["match-a"]


def test_stats_stickiness_holds_in_both_directions(tmp_path):
    """A seed match that flaps to RAAS stays out; a RAAS match that flaps to
    Seed stays in. One decision per match, taken where the row is created."""
    s = _store(tmp_path)
    try:
        s.record_tick(_snap(mode="Seed", match_id="seed", tick=1))
        s.record_tick(_snap(mode="RAAS", match_id="seed", tick=2))
        s.record_tick(_snap(mode="RAAS", match_id="real", tick=3))
        s.record_tick(_snap(mode="Seed", match_id="real", tick=4))
    finally:
        s.close()
    assert [r["match_id"] for r in _matches(tmp_path)] == ["real"]


def test_a_layer_pattern_skips_the_stats_row(tmp_path):
    s = StatsStore(tmp_path / "s.db", server_id="t", elo=False,
                   seeding_game_modes=(),
                   seeding_layer_patterns=("*Skirmish*",))
    try:
        s.record_tick(_snap(mode="Skirmish", layer="Sumari_Skirmish_v1",
                            tick=1))
    finally:
        s.close()
    assert _matches(tmp_path) == []


def test_an_unconfigured_store_records_a_seed_match(tmp_path):
    """The default in code is 'exclude nothing'; the default in CONFIG is
    ["Seed"]. A caller that forgets to wire it up loses no data."""
    s = StatsStore(tmp_path / "s.db", server_id="t", elo=False)
    try:
        s.record_tick(_snap(mode="Seed", tick=1))
    finally:
        s.close()
    assert [r["match_id"] for r in _matches(tmp_path)] == ["match-a"]


# --------------------------------------------------------------------------
# backfill — the archive predates the exclusion
# --------------------------------------------------------------------------

def _write_recording(rec_dir, name, snaps):
    from sqreader.recorder import _build_meta
    from sqreader.sqrx import SqrxWriter

    path = rec_dir / f"{name}.sqrx"
    w = SqrxWriter(path, server_id="t")
    for s in snaps:
        w.write_line(json.dumps(s) + "\n")
    w.close()
    meta = _build_meta(sqrx_path=path, server_id="t", created_ms=0,
                       first_snap=snaps[0], last_snap=snaps[-1],
                       ticks=len(snaps), peak_players=0)
    path.with_suffix(".meta.json").write_text(json.dumps(meta),
                                              encoding="utf-8")
    return path


def test_backfill_skips_a_seed_recording_and_keeps_the_real_one(
        tmp_path, monkeypatch):
    """The six-hour Fallujah seed file is already in the archive, and the
    ingest path runs through here too."""
    import argparse

    from sqreader import cli, config

    monkeypatch.setattr(config, "_cache", dict(config.DEFAULTS))
    rec_dir = tmp_path / "rec"
    rec_dir.mkdir()
    _write_recording(rec_dir, "seed", [
        _snap(mode="Seed", match_id="seed", tick=i, map_name="Fallujah Seed v1",
              layer="Fallujah Seed v1") for i in range(1, 4)])
    _write_recording(rec_dir, "real", [
        _snap(mode="RAAS", match_id="real", tick=i) for i in range(1, 4)])

    args = argparse.Namespace(
        recordings_dir=rec_dir, stats_db=tmp_path / "s.db", server_id="t",
        skip_newer_than=0.0, limit=0, push=False)
    assert cli.cmd_stats_backfill(args) == 0
    assert [r["match_id"] for r in _matches(tmp_path)] == ["real"]
