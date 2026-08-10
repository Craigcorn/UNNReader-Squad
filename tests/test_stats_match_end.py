"""When StatsStore may believe a match ended — and what it may claim if it did.

Written after finding the site publishing results nobody observed. `winner_team`
is derived from the two ticket counts, and those are whatever was last read, so
a match that merely STOPPED being watched was stored with a mid-match snapshot
as its final score and a winner inferred from it.

The regression at the bottom is built from the real failure: match b0318b0a,
whose replay holds 8023 frames, every one `InProgress`, with no terminal state
anywhere — stored as "273-203, team 1 won".
"""
from __future__ import annotations

import sqlite3

from conftest import end_match

from sqreader.recording_lifecycle import MATCH_TRANSITION_CONFIRM_TICKS
from sqreader.stats import StatsStore


def _snap(state="InProgress", *, mid="M1", t1=300, t2=280, tick=1,
          ts="2026-07-15T12:00:00+00:00"):
    gs = {"matchState": state, "mapName": "Narva", "gameModeName": "RAAS"}
    if mid is not None:
        gs["matchId"] = mid
    teams = []
    if t1 is not None:
        teams = [{"id": 1, "tickets": t1, "factionId": "USA"},
                 {"id": 2, "tickets": t2, "factionId": "RUS"}]
    return {"timestamp": ts, "tick": tick, "gameState": gs,
            "teams": teams, "players": [], "damageEvents": []}


def _row(db, mid="M1"):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute("SELECT * FROM matches WHERE match_id=?", (mid,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def _store(tmp_path):
    return StatsStore(tmp_path / "s.db", server_id="t", elo=False)


# -- when the end may be believed --------------------------------------------

def test_a_single_inactive_tick_does_not_end_the_match(tmp_path):
    """The whole defect in one test: one torn read used to finalize a match."""
    s = _store(tmp_path)
    try:
        s.record_tick(_snap())
        s.record_tick(_snap("WaitingPostMatch"))
    finally:
        s.close()
    assert _row(tmp_path / "s.db")["status"] == "open"


def test_confirmed_inactive_run_ends_the_match(tmp_path):
    s = _store(tmp_path)
    try:
        s.record_tick(_snap())
        end_match(s, _snap("WaitingPostMatch"))
    finally:
        s.close()
    row = _row(tmp_path / "s.db")
    assert row["status"] == "final"
    assert row["end_state"] == "finalized"


def test_an_unknown_state_never_confirms(tmp_path):
    """Fail closed: a future or torn state string is not evidence of an ending,
    no matter how many times it repeats."""
    s = _store(tmp_path)
    try:
        s.record_tick(_snap())
        for _ in range(10):
            s.record_tick(_snap("SomeFutureState"))
    finally:
        s.close()
    assert _row(tmp_path / "s.db")["status"] == "open"


def test_a_torn_tick_breaks_the_confirmation_run(tmp_path):
    """Two inactive ticks, an InProgress tick, two more: no run of three."""
    s = _store(tmp_path)
    try:
        s.record_tick(_snap())
        s.record_tick(_snap("WaitingPostMatch"))
        s.record_tick(_snap("WaitingPostMatch"))
        s.record_tick(_snap())
        s.record_tick(_snap("WaitingPostMatch"))
        s.record_tick(_snap("WaitingPostMatch"))
    finally:
        s.close()
    assert _row(tmp_path / "s.db")["status"] == "open"


def test_a_missing_match_id_is_ambiguous_not_an_ending(tmp_path):
    """The unlicensed-server case: `synth_match` yields no id for a few ticks
    when continuity breaks. That is silence, not a result."""
    s = _store(tmp_path)
    try:
        s.record_tick(_snap())
        for _ in range(5):
            s.record_tick(_snap(mid=None))
    finally:
        s.close()
    assert _row(tmp_path / "s.db")["status"] == "open"


# -- what may be claimed ------------------------------------------------------

def test_final_score_comes_from_the_terminal_frame(tmp_path):
    s = _store(tmp_path)
    try:
        s.record_tick(_snap(t1=300, t2=280))
        end_match(s, _snap("WaitingPostMatch", t1=12, t2=0))
    finally:
        s.close()
    row = _row(tmp_path / "s.db")
    assert (row["team1_tickets"], row["team2_tickets"]) == (12, 0)
    assert row["winner_team"] == 1
    assert "terminal-frame" in row["end_reason"]


def test_an_observed_draw_is_a_real_draw(tmp_path):
    s = _store(tmp_path)
    try:
        s.record_tick(_snap(t1=100, t2=100))
        end_match(s, _snap("WaitingPostMatch", t1=100, t2=100))
    finally:
        s.close()
    assert _row(tmp_path / "s.db")["winner_team"] == 0


def test_terminal_frame_without_tickets_falls_back_and_says_so(tmp_path):
    s = _store(tmp_path)
    try:
        s.record_tick(_snap(t1=250, t2=30))
        end_match(s, _snap("WaitingPostMatch", t1=None))
    finally:
        s.close()
    row = _row(tmp_path / "s.db")
    assert (row["team1_tickets"], row["team2_tickets"]) == (250, 30)
    assert "last-active-frame" in row["end_reason"]


# -- the production regression ------------------------------------------------

def test_a_match_that_merely_stops_claims_no_winner(tmp_path):
    """b0318b0a's exact shape: InProgress throughout, no terminal state, then
    the agent goes away. Stored as 273-203 with team 1 declared the winner."""
    s = _store(tmp_path)
    try:
        for i, t2 in enumerate((233, 220, 210, 203)):
            s.record_tick(_snap(t1=273, t2=t2, tick=i + 1))
        s.finalize_open_match(reason="serve shutdown")
    finally:
        s.close()
    row = _row(tmp_path / "s.db")
    assert row["status"] == "final"
    assert row["end_state"] == "unverified"
    assert row["winner_team"] is None, "a winner was invented from a mid-match score"
    # The tickets stay: they are the evidence of where the match had got to.
    assert (row["team1_tickets"], row["team2_tickets"]) == (273, 203)


def test_startup_sweep_marks_unverified_and_drops_any_winner(tmp_path):
    """A row left open by a hard kill is swept to final. It never observed an
    ending, so it may not carry a result."""
    db = tmp_path / "s.db"
    s = StatsStore(db, server_id="t", elo=False)
    s.record_tick(_snap(t1=300, t2=290))
    s.conn.execute("UPDATE matches SET winner_team=1")
    s.conn.commit()
    s.close()

    StatsStore(db, server_id="t", elo=False).close()   # sweeps on open
    row = _row(db)
    assert row["status"] == "final"
    assert row["end_state"] == "unverified"
    assert row["winner_team"] is None
    assert "hard kill" in row["end_reason"]


def test_a_new_match_id_finalizes_the_old_one_as_unverified(tmp_path):
    """Seeing a different id means the previous match is over, but we never
    watched it end — so it gets no result."""
    s = _store(tmp_path)
    try:
        s.record_tick(_snap(mid="M1", t1=300, t2=280))
        s.record_tick(_snap(mid="M2", t1=500, t2=500))
    finally:
        s.close()
    old = _row(tmp_path / "s.db", "M1")
    assert old["end_state"] == "unverified" and old["winner_team"] is None
    assert _row(tmp_path / "s.db", "M2")["status"] == "open"


# -- ticket sanity ------------------------------------------------------------

def test_out_of_range_tickets_are_not_stored(tmp_path):
    s = _store(tmp_path)
    try:
        s.record_tick(_snap(t1=300, t2=280))
        s.record_tick(_snap(t1=99999, t2=-5))
        end_match(s, _snap("WaitingPostMatch", t1=99999, t2=-5))
    finally:
        s.close()
    row = _row(tmp_path / "s.db")
    # The junk never displaces the last good read.
    assert (row["team1_tickets"], row["team2_tickets"]) == (300, 280)


def test_bundle_carries_the_end_state(tmp_path):
    """A future column-list refactor in build_match_bundle would silently drop
    provenance on the way to central."""
    from sqreader.ingest_client import build_match_bundle
    s = _store(tmp_path)
    try:
        s.record_tick(_snap())
        end_match(s, _snap("WaitingPostMatch", t1=10, t2=0))
    finally:
        s.close()
    b = build_match_bundle(tmp_path / "s.db", "M1")
    assert b["match"]["end_state"] == "finalized"
    assert b["match"]["end_reason"]


def test_confirm_threshold_is_the_recorders(tmp_path):
    """Not a knob. If these two ever diverge again, the looser one decides what
    the site publishes."""
    from sqreader import recorder
    assert recorder._MATCH_END_CONFIRM_TICKS == MATCH_TRANSITION_CONFIRM_TICKS


# -- ELO must not be fed a result nobody saw ----------------------------------

def test_an_unverified_match_is_never_rated(tmp_path):
    """ELO moves a permanent running total that nothing can reverse. Rating a
    fabricated outcome is what forced a full rebuild."""
    db = tmp_path / "s.db"
    s = StatsStore(db, server_id="t", elo=True, elo_min_players=1,
                   elo_min_duration=0, elo_min_playtime=0)
    try:
        for i in range(3):
            s.record_tick({**_snap(tick=i + 1),
                           "players": [
                               {"eosId": "0002a1b2c3d4e5f60718293a4b5c6d7e",
                                "name": "A", "teamId": 1, "soldier": None,
                                "stats": {"kills": 5, "deaths": 1}},
                               {"eosId": "0002ffffffffffffffffffffffffffff",
                                "name": "B", "teamId": 2, "soldier": None,
                                "stats": {"kills": 1, "deaths": 5}}]})
        s.finalize_open_match(reason="serve shutdown")
    finally:
        s.close()
    conn = sqlite3.connect(str(db))
    try:
        rated = conn.execute(
            "SELECT COUNT(*) FROM player_matches WHERE elo_change IS NOT NULL"
        ).fetchone()[0]
        ratings = conn.execute("SELECT COUNT(*) FROM player_elo").fetchone()[0]
    finally:
        conn.close()
    assert rated == 0, "an unobserved result reached ELO"
    # elo_change stays NULL rather than 0, so a later repair can still rate it.
    assert ratings == 0


def test_unverified_matches_are_absent_from_the_match_list(tmp_path):
    from sqreader.stats import list_matches
    s = _store(tmp_path)
    try:
        s.record_tick(_snap(mid="GOOD"))
        end_match(s, _snap("WaitingPostMatch", mid="GOOD", t1=10, t2=0))
        s.record_tick(_snap(mid="BAD"))
        s.finalize_open_match(reason="serve shutdown")
    finally:
        s.close()
    ids = {m["match_id"] for m in list_matches(tmp_path / "s.db")}
    assert ids == {"GOOD"}
