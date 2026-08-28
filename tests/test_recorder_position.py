"""Two-tier recorder: position frames append to the open writer without touching
full-frame counts, are dropped when no recording is open, and are excluded from
`ticks`/`peakPlayers` on a disk re-scan (backward-compatible with full-only .sqrx)."""
import json
from datetime import datetime, timezone

from sqreader.recorder import (
    RecordingState, _write_line, extract_metadata, write_position_frame,
)
from sqreader.sqrx import SqrxWriter


def _full(tick, players, ts, mapname="Yehorivka"):
    return json.dumps({"tick": tick, "timestamp": ts,
                       "players": [{"name": f"p{i}"} for i in range(players)],
                       "gameState": {"mapName": mapname, "matchId": "m1",
                                     "matchState": "InProgress"}}) + "\n"


def _pos(tick, ts, n=3):
    return json.dumps({"t": "pos", "tick": tick, "timestamp": ts,
                       "players": [{"id": f"p{i}", "x": 1.0, "y": 2.0}
                                   for i in range(n)]}) + "\n"


def _state(tmp_path):
    path = tmp_path / "rec.sqrx"
    w = SqrxWriter(str(path), "sq")
    st = RecordingState(match_id="m1", writer=w, path=path,
                        started_at=datetime.now(timezone.utc))
    return st, path


def test_position_frame_dropped_without_recording():
    assert write_position_frame({"current": None}, _pos(1, "t")) == 0


def test_position_frame_appends_but_not_tick_count(tmp_path):
    st, _ = _state(tmp_path)
    _write_line(st, json.loads(_full(1, 50, "2026-01-01T00:00:00+00:00")),
                _full(1, 50, "2026-01-01T00:00:00+00:00"))
    n = write_position_frame({"current": st}, _pos(2, "2026-01-01T00:00:00.25+00:00"))
    assert n > 0
    assert st.tick_count == 1                # full frames only
    assert st.position_count == 1            # pos counted separately
    assert st.peak_players == 50             # untouched by pos frame
    st.writer.close()


def test_extract_metadata_skips_position_frames(tmp_path):
    st, path = _state(tmp_path)
    st.writer.write_line(_full(1, 50, "2026-01-01T00:00:00+00:00"))
    st.writer.write_line(_pos(2, "2026-01-01T00:00:00.25+00:00"))
    st.writer.write_line(_pos(3, "2026-01-01T00:00:00.50+00:00"))
    st.writer.write_line(_pos(4, "2026-01-01T00:00:00.75+00:00"))
    st.writer.write_line(_full(5, 62, "2026-01-01T00:00:01+00:00"))
    st.writer.close()
    meta = extract_metadata(path)
    assert meta["ticks"] == 2                # full frames
    assert meta["positionFrames"] == 3
    assert meta["totalFrames"] == 5
    assert meta["peakPlayers"] == 62         # pos frames' rosters ignored
    assert meta["mapName"] == "Yehorivka"


def test_extract_metadata_old_sqrx_has_no_position_frames(tmp_path):
    st, path = _state(tmp_path)
    st.writer.write_line(_full(1, 40, "2026-01-01T00:00:00+00:00"))
    st.writer.write_line(_full(2, 44, "2026-01-01T00:00:01+00:00"))
    st.writer.close()
    meta = extract_metadata(path)
    assert meta["ticks"] == 2
    assert meta["positionFrames"] == 0
    assert meta["totalFrames"] == 2


# ---------------------------------------------------------------------------
# ... and they must not reach the stats writer either
#
# `record_tick` takes one SNAPSHOT per tick. A position frame is not one: it
# carries no game state, so it reads as an ambiguous tick, and an ambiguous
# tick breaks any end-of-match confirmation in progress. On a two-tier
# recording the position frames sit between the confirming ticks, so the run
# could never complete and every replayed match finalized as `unverified`.
# ---------------------------------------------------------------------------

def _two_tier_recording(rec_dir, *, name="m1"):
    """A short two-tier match, ending the way a real one does: confirming
    frames with 4 Hz position frames interleaved between them."""
    import sqreader.recording_lifecycle as rl
    from sqreader.recorder import _build_meta

    eos = "a" * 32
    def full(tick, state, t1, t2):
        return {"tick": tick, "timestamp": f"2026-01-01T00:00:{tick:02d}+00:00",
                "gameState": ({"mapName": "Yehorivka", "matchId": name,
                               "matchState": state, "gameModeName": "RAAS"}
                              if state == "InProgress" else
                              {"mapName": "Yehorivka", "matchState": state,
                               "gameModeName": "RAAS"}),
                "teams": [{"id": 1, "tickets": t1, "factionId": "USA"},
                          {"id": 2, "tickets": t2, "factionId": "RUS"}],
                "players": [{"eosId": eos, "name": "Alice", "teamId": 1,
                             "stats": {"kills": 3, "deaths": 1}}],
                "damageEvents": []}

    snaps = [full(t, "InProgress", 300, 280) for t in range(1, 6)]
    snaps += [full(t, "WaitingPostMatch", 210, 0)
              for t in range(6, 6 + rl.MATCH_TRANSITION_CONFIRM_TICKS)]

    path = rec_dir / f"{name}.sqrx"
    w = SqrxWriter(str(path), "t")
    for s in snaps:
        w.write_line(json.dumps(s) + "\n")
        for k in range(3):       # the 4 Hz sampler, between every full frame
            w.write_line(_pos(s["tick"] * 10 + k, s["timestamp"]))
    w.close()
    meta = _build_meta(sqrx_path=path, server_id="t", created_ms=0,
                       first_snap=snaps[0], last_snap=snaps[-1],
                       ticks=len(snaps), peak_players=1)
    path.with_suffix(".meta.json").write_text(json.dumps(meta),
                                              encoding="utf-8")
    return path


def test_backfill_does_not_feed_position_frames_to_the_stats_writer(
        tmp_path, monkeypatch):
    import argparse
    import sqlite3

    from sqreader import cli, config

    monkeypatch.setattr(config, "_cache", dict(config.DEFAULTS))
    rec_dir = tmp_path / "rec"
    rec_dir.mkdir()
    _two_tier_recording(rec_dir)

    db = tmp_path / "s.db"
    assert cli.cmd_stats_backfill(argparse.Namespace(
        recordings_dir=rec_dir, stats_db=db, server_id="t",
        skip_newer_than=0.0, limit=0, push=False)) == 0

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM matches").fetchone())
    conn.close()
    # The ending is in the file and the replay could see it, because the
    # position frames between the confirming frames were ignored.
    assert row["end_state"] == "finalized"
    assert row["winner_team"] == 1
    assert row["tick_count"] == 5           # frames of play, not total frames
