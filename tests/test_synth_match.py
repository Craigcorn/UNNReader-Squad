"""Synthetic match ids for unlicensed servers (sqreader/synth_match.py).

The numbers in the drift tests are not invented: they come from three live
snapshots of an unlicensed server, 20 s apart, where `elapsedSec` advanced 19
per 20.000 s of `worldTimeSec`. That 5% creep is what makes a recomputed anchor
useless as an identity and is the reason continuity hangs off elapsed
monotonicity instead.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3

import pytest

from sqreader.synth_match import STATE_FILENAME, SyntheticMatchIds


def snap(*, state="InProgress", match_id="", elapsed=100, world=1000.0,
         srv=1786217763, map_name="Tallil Outskirts Skirmish v2"):
    gs = {
        "matchState": state,
        "matchId": match_id,
        "elapsedSec": elapsed,
        "worldTimeSec": world,
        "serverStartTimestamp": srv,
        "mapName": map_name,
    }
    return {"gameState": gs}


def make(tmp_path, **kw):
    kw.setdefault("community", "gm")
    kw.setdefault("server_id", "gm-1")
    return SyntheticMatchIds(tmp_path / STATE_FILENAME, **kw)


def settle(res, s, n=3):
    """Drive the confirmation window; return the id finally minted."""
    out = None
    for _ in range(n):
        out = res.apply(s)
    return out


def _rows(db, sql, params=()):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def _live_frame(tick, elapsed, world, *, state="InProgress", map_name=None,
                players=()):
    """A frame shaped like an unlicensed server's: InProgress, empty matchId.

    The timestamp advances with the tick. Stats rows take `started_at` from it,
    so a fixed one would leave consecutive matches indistinguishable by time.
    """
    ts = (_dt.datetime(2026, 8, 8, 19, 0, 0, tzinfo=_dt.timezone.utc)
          + _dt.timedelta(seconds=tick))
    return {
        "timestamp": ts.isoformat(),
        "tick": tick,
        "gameState": {
            "matchState": state,
            "matchId": "",                        # unlicensed server
            "elapsedSec": elapsed,
            "worldTimeSec": world,
            "serverStartTimestamp": 1786217763,
            "mapName": map_name or "Tallil Outskirts Skirmish v2",
            "gameModeName": "Skirmish",
            "layer": {"name": map_name or "Tallil Outskirts Skirmish v2"},
        },
        "teams": [{"id": 1, "tickets": 300}, {"id": 2, "tickets": 280}],
        "players": list(players),
        "damageEvents": [],
    }


# -- the licensed path stays untouched ---------------------------------------

def test_real_match_id_is_never_touched(tmp_path):
    res = make(tmp_path)
    s = snap(match_id="9f1c2b3a-0000-4000-8000-000000000001")
    for _ in range(5):
        assert res.apply(s) is None
    assert s["gameState"]["matchId"] == "9f1c2b3a-0000-4000-8000-000000000001"
    assert "matchIdSynthetic" not in s["gameState"]


@pytest.mark.parametrize("state", ["WaitingToStart", "WaitingPostMatch", None])
def test_only_fills_in_during_inprogress(tmp_path, state):
    res = make(tmp_path)
    s = snap(state=state)
    assert settle(res, s) is None
    assert s["gameState"]["matchId"] == ""


# -- minting ------------------------------------------------------------------

def test_mints_a_uuid_after_confirmation(tmp_path):
    import uuid as _uuid
    res = make(tmp_path)
    s = snap()
    assert res.apply(s) is None      # 1st tick: candidate only
    assert res.apply(s) is None      # 2nd tick
    mid = res.apply(s)               # 3rd tick confirms
    assert mid and _uuid.UUID(mid)
    assert s["gameState"]["matchId"] == mid
    assert s["gameState"]["matchIdSynthetic"] is True


def test_different_communities_never_collide(tmp_path):
    """match.match_id is a GLOBAL primary key in central, so two unlicensed
    clans hitting the same id would have one overwrite the other."""
    a = settle(make(tmp_path / "a", community="gm"), snap())
    b = settle(make(tmp_path / "b", community="altai"), snap())
    assert a and b and a != b


def test_different_servers_in_one_community_never_collide(tmp_path):
    a = settle(make(tmp_path / "a", server_id="s1"), snap())
    b = settle(make(tmp_path / "b", server_id="s2"), snap())
    assert a and b and a != b


def test_unenrolled_boxes_still_get_a_unique_scope(tmp_path):
    a = settle(make(tmp_path / "a", community=""), snap())
    b = settle(make(tmp_path / "b", community=""), snap())
    assert a and b and a != b


# -- continuity: the whole point ---------------------------------------------

def test_id_is_stable_across_the_elapsed_time_drift(tmp_path):
    """40 minutes at the observed 19-per-20 rate. A fixed-tolerance anchor
    would have split this match roughly twenty times."""
    res = make(tmp_path)
    first = settle(res, snap(elapsed=0, world=900.0))
    assert first
    seen = {first}
    elapsed, world = 0, 900.0
    for _ in range(120):             # 120 x 20 s = 40 min
        elapsed += 19
        world += 20.0
        seen.add(res.apply(snap(elapsed=elapsed, world=world)))
    assert seen == {first}


def test_elapsed_reset_starts_a_new_match(tmp_path):
    res = make(tmp_path)
    first = settle(res, snap(elapsed=1500, world=2400.0))
    second = settle(res, snap(elapsed=4, world=2600.0))
    assert first and second and first != second


def test_map_change_starts_a_new_match(tmp_path):
    res = make(tmp_path)
    first = settle(res, snap(map_name="Tallil Skirmish v2"))
    second = settle(res, snap(map_name="Fallujah AAS v1"))
    assert first and second and first != second


def test_squad_restart_starts_a_new_match(tmp_path):
    res = make(tmp_path)
    first = settle(res, snap(srv=1786217763))
    second = settle(res, snap(srv=1786299999))
    assert first and second and first != second


def test_one_torn_tick_does_not_split_a_match(tmp_path):
    """The recorder treats an id change as a match boundary, so a single bad
    read must not mint. Two ticks of garbage, then the real values resume."""
    res = make(tmp_path)
    first = settle(res, snap(elapsed=600, world=1500.0))
    assert first
    assert res.apply(snap(elapsed=0, world=7.0, srv=1)) is None
    assert res.apply(snap(elapsed=99999, world=3.0, map_name="?")) is None
    # A fresh frame with sane values: straight back onto the same match, with
    # no confirmation window, because the committed state was never disturbed.
    assert res.apply(snap(elapsed=602, world=1502.0)) == first


# -- restart --------------------------------------------------------------

def test_restart_mid_match_keeps_the_same_id(tmp_path):
    res = make(tmp_path)
    first = settle(res, snap(elapsed=600, world=1500.0))
    assert first
    # New process, same state file, match has moved on a little.
    again = make(tmp_path)
    assert again.apply(snap(elapsed=640, world=1542.0)) == first


def test_restart_after_the_match_ended_mints_a_new_id(tmp_path):
    res = make(tmp_path)
    first = settle(res, snap(elapsed=600, world=1500.0))
    again = make(tmp_path)
    second = settle(again, snap(elapsed=30, world=2900.0))
    assert first and second and first != second


def test_state_file_is_valid_json_with_an_install_id(tmp_path):
    res = make(tmp_path)
    settle(res, snap())
    payload = json.loads((tmp_path / STATE_FILENAME).read_text(encoding="utf-8"))
    assert payload["install_id"]
    assert payload["match"]["match_id"]


def test_corrupt_state_file_does_not_raise(tmp_path):
    (tmp_path / STATE_FILENAME).write_text("{not json", encoding="utf-8")
    res = make(tmp_path)
    assert settle(res, snap())


# -- refusing to guess --------------------------------------------------------

@pytest.mark.parametrize("field", ["elapsedSec", "worldTimeSec",
                                   "serverStartTimestamp"])
def test_unreadable_fields_stay_silent_rather_than_invent_an_id(tmp_path, field):
    res = make(tmp_path)
    s = snap()
    s["gameState"][field] = None
    assert settle(res, s) is None
    assert s["gameState"]["matchId"] == ""


# -- end to end: does an unlicensed server actually record a match? -----------

def test_unlicensed_server_produces_a_real_match_row(tmp_path):
    """The whole point. Feed StatsStore exactly what an unlicensed server looks
    like — InProgress with an empty matchId — and confirm that without the
    resolver nothing is written at all, and with it a normal match row appears.
    """
    from sqreader.stats import StatsStore

    # A real 32-hex EOS ProductUserId; `_valid_eos` rejects shorter stand-ins.
    players = [{"eosId": "0002a1b2c3d4e5f60718293a4b5c6d7e", "name": "A",
                "teamId": 1, "soldier": None,
                "stats": {"kills": 2, "deaths": 1}}]

    def frame(i):
        return _live_frame(i + 1, 100 + i * 19, 1000.0 + i * 20,
                           players=players)

    # --- today's behaviour: silence -----------------------------------------
    before = StatsStore(tmp_path / "before.db", server_id="gm-1")
    for i in range(6):
        before.record_tick(frame(i))
    before.close()
    rows = _rows(tmp_path / "before.db", "SELECT * FROM matches")
    assert rows == [], "an empty matchId must still record nothing on its own"

    # --- with the resolver in front -----------------------------------------
    res = make(tmp_path)
    after = StatsStore(tmp_path / "after.db", server_id="gm-1")
    for i in range(6):
        f = frame(i)
        res.apply(f)
        after.record_tick(f)
    after.close()
    rows = _rows(tmp_path / "after.db", "SELECT * FROM matches")
    assert len(rows) == 1, "exactly one match, not one per tick"
    assert rows[0]["map_name"] == "Tallil Outskirts Skirmish v2"
    assert rows[0]["status"] == "open"
    pm = _rows(tmp_path / "after.db", "SELECT * FROM player_matches")
    assert len(pm) == 1 and pm[0]["kills"] == 2


def test_unlicensed_server_produces_a_replay_file(tmp_path):
    """The other half: the .sqrx recorder is driven by the same field, and it
    must survive the ticks we deliberately withhold an id for while a new match
    is being confirmed."""
    import json as _json

    from sqreader.recorder import _handle_snap, finalize_recording

    res = make(tmp_path / "state")
    box = _recorder_box()
    out_dir = tmp_path / "rec"
    out_dir.mkdir()
    names: list = []

    for i in range(10):
        f = _live_frame(i + 1, 100 + i * 19, 1000.0 + i * 20)
        res.apply(f)
        _handle_snap(snap=f, raw_line=_json.dumps(f), state_box=box,
                     out_dir=out_dir, server_id="gm-1", min_ticks=0,
                     filename_buffer=names)

    assert box["current"] is not None, "a recording must be open"
    finalize_recording(box["current"], min_ticks=0, reason="test")
    written = list(out_dir.glob("*.sqrx"))
    assert len(written) == 1, f"expected one replay, got {written}"


def _recorder_box():
    return {
        "current": None, "last_state": None, "inactive_ticks": 0,
        "pending_match_id": None, "pending_match_buffer": [],
        "last_tick": None, "tick_sequence_required": False,
        "missing_tick_warned": False,
    }


def test_two_matches_in_a_row_are_kept_apart(tmp_path):
    """A full map change end to end: match A, the post-match states, then match
    B. Two ids, two stats rows with A closed, and two separate replay files.

    This is the interaction the design reasoning was weakest about. While a new
    match is being confirmed the resolver emits nothing, and `InProgress` is NOT
    in the recorder's inactive set — so those ticks land on its "ambiguous"
    branch rather than its close path. Whether the two matches actually come out
    separate is a claim worth testing rather than arguing.
    """
    import json as _json

    from sqreader.recorder import _handle_snap, finalize_recording
    from sqreader.stats import StatsStore

    players = [{"eosId": "0002a1b2c3d4e5f60718293a4b5c6d7e", "name": "A",
                "teamId": 1, "soldier": None, "stats": {"kills": 1}}]
    res = make(tmp_path / "state")
    store = StatsStore(tmp_path / "s.db", server_id="gm-1")
    box = _recorder_box()
    out_dir = tmp_path / "rec"
    out_dir.mkdir()
    names: list = []
    tick = 0

    def run(frames):
        nonlocal tick
        for elapsed, world, state, map_name in frames:
            tick += 1
            f = _live_frame(tick, elapsed, world, state=state,
                            map_name=map_name, players=players)
            res.apply(f)
            _handle_snap(snap=f, raw_line=_json.dumps(f), state_box=box,
                         out_dir=out_dir, server_id="gm-1", min_ticks=0,
                         filename_buffer=names)
            store.record_tick(f)

    a = "Tallil Outskirts Skirmish v2"
    b = "Fallujah AAS v1"
    # Match A, then the states Squad really passes through, then match B.
    run([(100 + i * 19, 1000.0 + i * 20, "InProgress", a) for i in range(8)])
    run([(0, 1160.0 + i, "WaitingPostMatch", a) for i in range(4)])
    run([(0, 1180.0 + i, "WaitingToStart", b) for i in range(4)])
    run([(i * 19, 1200.0 + i * 20, "InProgress", b) for i in range(8)])

    if box["current"] is not None:
        finalize_recording(box["current"], min_ticks=0, reason="test")
    store.close()

    rows = _rows(tmp_path / "s.db", "SELECT * FROM matches ORDER BY started_at")
    assert len(rows) == 2, f"expected two matches, got {rows}"
    assert rows[0]["match_id"] != rows[1]["match_id"]
    assert rows[0]["map_name"] == a and rows[1]["map_name"] == b
    assert rows[0]["status"] == "final", "the first match must be closed"
    assert len(list(out_dir.glob("*.sqrx"))) == 2
