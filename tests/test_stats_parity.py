"""The parity harness, tested the way it will be trusted.

Two halves, because the harness has two jobs and they fail differently.

The differ is pure — two databases in, a list of differences out — so it is
tested against hand-built pairs where the right answer is known by
construction: identical, one wrong cell, rows the archive cannot speak for,
a float on the edge of its tolerance, and a schema that drifted between the
two sides. That last one matters most: a column that exists on only one side
must be a loud finding, because the silent alternative is a harness that
quietly stops comparing the thing it was added to protect.

The end-to-end half proves the claim the harness exists to make. A synthetic
match is driven through the LIVE path — the recorder and `record_tick`, in the
same order `cli.py` calls them — and then the `.sqrx` that produced is replayed
through the real backfill into a fresh database. The differ must find nothing.
Then one recorded value is changed and it must find exactly that.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import stats_parity as sp                                # noqa: E402
from sqreader.sqrx import SqrxWriter                                  # noqa: E402

# A schema shaped like the real one, small enough to reason about. The table
# NAMES are the real ones on purpose: that puts the shipped semantic keys in
# TABLE_SPECS under test rather than a convenient copy of them.
_DDL = """
CREATE TABLE matches (
  match_id TEXT PRIMARY KEY, server_id TEXT, status TEXT,
  winner_team INTEGER, started_at INTEGER, end_state TEXT, end_reason TEXT);
CREATE TABLE player_matches (
  match_id TEXT NOT NULL, eos_id TEXT NOT NULL, name TEXT,
  kills INTEGER, score REAL, elo_change INTEGER,
  PRIMARY KEY (match_id, eos_id));
CREATE TABLE players (eos_id TEXT PRIMARY KEY, last_name TEXT);
CREATE TABLE player_elo (eos_id TEXT PRIMARY KEY, elo_rating INTEGER);
CREATE TABLE kill_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT, victim_name TEXT,
  attacker_name TEXT, ts REAL, killed INTEGER, wounded INTEGER, weapon TEXT);
CREATE TABLE vehicle_session (
  id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT, eos_id TEXT,
  entered_at INTEGER, exited_at INTEGER, vehicle_class TEXT, distance_m REAL);
"""

EOS_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
EOS_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _db(rows: dict[str, list[tuple]] | None = None,
        extra_ddl: str = "") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    if extra_ddl:
        conn.executescript(extra_ddl)
    for table, tuples in (rows or {}).items():
        for t in tuples:
            conn.execute(
                f"INSERT INTO {table} VALUES ({','.join('?' * len(t))})", t)
    conn.commit()
    return conn


def _pair(rows: dict[str, list[tuple]]):
    return _db(rows), _db(rows)


def _scope(matches=("M1",), players=(EOS_A,), elo_reason=None,
           unscoped_matches=None, endings_unrecorded=()):
    return sp.Scope(
        in_scope_matches=set(matches),
        unscoped_matches=dict(unscoped_matches or {}),
        in_scope_players=set(players),
        elo_reason=elo_reason,
        endings_unrecorded=set(endings_unrecorded))


_BASE = {
    "matches": [("M1", "srv", "final", 1, 100, "finalized",
                 "confirmed matchState='WaitingPostMatch'")],
    "player_matches": [("M1", EOS_A, "Alice", 7, 12.5, 4)],
    "players": [(EOS_A, "Alice")],
}


# -- the differ ---------------------------------------------------------------

def test_identical_databases_report_no_differences():
    live, replay = _pair(_BASE)
    report = sp.diff_databases(live, replay, _scope())
    assert report.ok
    assert report.diffs == []


def test_one_wrong_cell_is_one_finding():
    live, replay = _pair(_BASE)
    replay.execute("UPDATE player_matches SET kills=8")
    report = sp.diff_databases(live, replay, _scope())
    assert len(report.diffs) == 1
    d = report.diffs[0]
    assert (d.table, d.column, d.kind) == ("player_matches", "kills", "value")
    assert (d.live, d.replay) == (7, 8)


def test_rows_the_archive_cannot_reproduce_are_counted_not_failed():
    """A live DB legitimately holds matches recorded before the recorder was
    deployed. Those rows are history, not regressions."""
    live = _db({
        "matches": _BASE["matches"] + [("OLD", "srv", "final", 2, 50,
                                        "finalized", "confirmed")],
        "player_matches": _BASE["player_matches"] + [
            ("OLD", EOS_B, "Bob", 3, 4.0, -2)],
    })
    replay = _db({"matches": _BASE["matches"],
                  "player_matches": _BASE["player_matches"]})
    scope = _scope(unscoped_matches={"OLD": "no recording in the archive"})
    report = sp.diff_databases(live, replay, scope)
    assert report.ok
    matches = next(t for t in report.tables if t.table == "matches")
    assert (matches.scoped_rows, matches.unscoped_rows) == (1, 1)


def test_a_missing_in_scope_row_is_a_finding():
    live = _db(_BASE)
    replay = _db({"matches": _BASE["matches"], "players": _BASE["players"]})
    report = sp.diff_databases(live, replay, _scope())
    assert [d.kind for d in report.diffs] == ["missing"]
    assert report.diffs[0].table == "player_matches"


def test_a_row_only_the_replay_produced_is_a_finding():
    live = _db({"matches": _BASE["matches"], "players": _BASE["players"]})
    replay = _db(_BASE)
    report = sp.diff_databases(live, replay, _scope())
    assert [d.kind for d in report.diffs] == ["extra"]


def test_floats_are_exact_unless_a_tolerance_says_otherwise(monkeypatch):
    live, replay = _pair(_BASE)
    replay.execute("UPDATE player_matches SET score=12.5000001")
    assert len(sp.diff_databases(live, replay, _scope()).diffs) == 1

    monkeypatch.setitem(sp.FLOAT_TOLERANCES, ("player_matches", "score"),
                        (1e-6, "test tolerance"))
    assert sp.diff_databases(live, replay, _scope()).ok


def test_a_float_just_outside_its_tolerance_still_fails(monkeypatch):
    live, replay = _pair(_BASE)
    monkeypatch.setitem(sp.FLOAT_TOLERANCES, ("player_matches", "score"),
                        (0.01, "test tolerance"))
    replay.execute("UPDATE player_matches SET score=12.5 + 0.02")
    diffs = sp.diff_databases(live, replay, _scope()).diffs
    assert [(d.column, d.kind) for d in diffs] == [("score", "value")]


def test_a_null_never_quietly_equals_a_number():
    live, replay = _pair(_BASE)
    replay.execute("UPDATE matches SET winner_team=NULL")
    diffs = sp.diff_databases(live, replay, _scope()).diffs
    assert [(d.column, d.live, d.replay) for d in diffs] == [
        ("winner_team", 1, None)]


def test_a_column_on_only_one_side_is_a_loud_finding_not_a_crash():
    """Schema drift between the live writer and the replay path is exactly the
    thing that would otherwise hide a gap, so it fails the run."""
    live, replay = _pair(_BASE)
    live.execute("ALTER TABLE player_matches ADD COLUMN medic_score REAL")
    report = sp.diff_databases(live, replay, _scope())
    schema = [d for d in report.diffs if d.kind == "schema"]
    assert [(d.table, d.column) for d in schema] == [
        ("player_matches", "medic_score")]
    # and the rest of the table is still compared
    pm = next(t for t in report.tables if t.table == "player_matches")
    assert "kills" in pm.compared_columns
    assert "medic_score" not in pm.compared_columns


def test_a_table_on_only_one_side_is_a_loud_finding():
    live, replay = _pair(_BASE)
    live.executescript("CREATE TABLE squad_scores (match_id TEXT)")
    report = sp.diff_databases(live, replay, _scope())
    assert [(d.table, d.kind) for d in report.diffs] == [
        ("squad_scores", "schema")]


def test_a_table_with_no_semantic_key_is_reported_rather_than_skipped():
    """A new table nobody taught the harness to identify rows in is a hole in
    the proof. It costs a red run until someone writes down what a row means."""
    extra = "CREATE TABLE squad_scores (match_id TEXT, squad TEXT, pts INTEGER)"
    live = _db(_BASE, extra_ddl=extra)
    replay = _db(_BASE, extra_ddl=extra)
    live.execute("INSERT INTO squad_scores VALUES ('M1','Alpha',3)")
    report = sp.diff_databases(live, replay, _scope())
    assert [(d.table, d.kind) for d in report.diffs] == [
        ("squad_scores", "schema")]
    assert "TABLE_SPECS" in report.diffs[0].note


def test_autoincrement_ids_are_never_compared():
    """kill_events.id numbers writes, not kills. Two databases holding the same
    three kills in the same order may still number them differently."""
    ev = ("M1", "Bob", "Alice", 1000.0, 1, 0, "BP_L85A2_C")
    live = _db(_BASE)
    replay = _db(_BASE)
    live.execute("INSERT INTO kill_events VALUES (?,?,?,?,?,?,?,?)", (5, *ev))
    replay.execute("INSERT INTO kill_events VALUES (?,?,?,?,?,?,?,?)", (1, *ev))
    report = sp.diff_databases(live, replay, _scope())
    assert report.ok
    ke = next(t for t in report.tables if t.table == "kill_events")
    assert "id" in ke.skipped_columns


def test_two_rows_under_one_key_are_reported():
    """If a semantic key can hold two rows it is not identity, and the whole
    comparison downstream of it is guesswork."""
    live = _db(_BASE)
    replay = _db(_BASE)
    for conn in (live, replay):
        conn.execute("INSERT INTO vehicle_session "
                     "(match_id, eos_id, entered_at, exited_at, vehicle_class) "
                     "VALUES ('M1', ?, 100, 120, 'BTR')", (EOS_A,))
    live.execute("INSERT INTO vehicle_session "
                 "(match_id, eos_id, entered_at, exited_at, vehicle_class) "
                 "VALUES ('M1', ?, 100, 130, 'MRAP')", (EOS_A,))
    report = sp.diff_databases(live, replay, _scope())
    assert any(d.kind == "duplicate" for d in report.diffs)


# -- the catalog of legitimate differences ------------------------------------

def test_a_match_with_no_recorded_ending_forgives_only_the_ending():
    """A recording made before the closing frames were kept cannot show the
    match ending. What the match forgives is the ending — not the match."""
    live, replay = _pair(_BASE)
    replay.execute("UPDATE matches SET winner_team=NULL, end_state='unverified'")
    replay.execute("UPDATE player_matches SET elo_change=NULL, kills=9")

    scope = _scope(endings_unrecorded={"M1"})
    diffs = sp.diff_databases(live, replay, scope).diffs
    assert [(d.table, d.column) for d in diffs] == [("player_matches", "kills")]

    # Same rows, a match whose ending IS in the archive: nothing is forgiven.
    assert len(sp.diff_databases(live, replay, _scope()).diffs) == 4


def test_the_reason_a_shutdown_gives_is_forgiven_only_when_nobody_saw_an_ending():
    """`end_reason` is provenance about the writer, and on an unobserved ending
    it describes the agent rather than the match. When an ending WAS observed it
    names the frame the tickets came from, and that stays compared."""
    live, replay = _pair(_BASE)
    live.execute("UPDATE matches SET end_state='unverified', "
                 "end_reason='startup sweep: left open by a hard kill'")
    replay.execute("UPDATE matches SET end_state='unverified', "
                   "end_reason='superseded: matchId changed'")
    assert sp.diff_databases(live, replay, _scope()).ok

    replay.execute("UPDATE matches SET end_state='finalized', "
                   "end_reason='confirmed; tickets=last-active-frame'")
    diffs = sp.diff_databases(live, replay, _scope()).diffs
    assert {d.column for d in diffs} == {"end_state", "end_reason"}


def test_the_report_says_how_many_values_each_exclusion_forgave():
    """An exclusion that quietly starts forgiving hundreds of rows is how a
    harness stops being one."""
    live, replay = _pair(_BASE)
    replay.execute("UPDATE matches SET winner_team=NULL, end_state='unverified'")
    text = sp.render_text(sp.diff_databases(
        live, replay, _scope(endings_unrecorded={"M1"})))
    assert "forgave 2 value(s) on 1 match(es)" in text
    assert "matches.end_reason — forgave 0 value(s)" in text


def test_ratings_are_unscoped_whenever_any_match_is():
    """A rating moves against the other team's average, so one unreproducible
    match perturbs every rating downstream of it. There is no per-row rescue."""
    live = _db(_BASE)
    replay = _db(_BASE)
    live.execute("INSERT INTO player_elo VALUES (?, 1042)", (EOS_A,))
    replay.execute("INSERT INTO player_elo VALUES (?, 1000)", (EOS_A,))
    poisoned = _scope(elo_reason="one match is unscoped")
    assert sp.diff_databases(live, replay, poisoned).ok
    assert not sp.diff_databases(live, replay, _scope()).ok


# -- scope --------------------------------------------------------------------

def _live_scope_db(tmp_path: Path) -> sqlite3.Connection:
    conn = _db({
        "matches": [("HAVE", "srv", "final", 1, 100, "finalized", ""),
                    ("GONE", "srv", "final", 2, 50, "finalized", ""),
                    ("OPEN", "srv", "open", None, 200, None, None)],
        "player_matches": [("HAVE", EOS_A, "Alice", 1, 1.0, 3),
                           ("GONE", EOS_B, "Bob", 2, 2.0, -1)],
        "players": [(EOS_A, "Alice"), (EOS_B, "Bob")],
    })
    return conn


def test_scope_names_a_reason_for_every_match_it_will_not_hold_the_archive_to(tmp_path):
    live = _live_scope_db(tmp_path)
    idx = sp.ArchiveIndex(eligible={"HAVE": ["a.sqrx"], "OPEN": ["b.sqrx"]})
    scope = sp.build_scope(live, idx)
    assert scope.in_scope_matches == {"HAVE"}
    assert set(scope.unscoped_matches) == {"GONE", "OPEN"}
    assert "no recording" in scope.unscoped_matches["GONE"]
    assert "still open" in scope.unscoped_matches["OPEN"]
    # Bob only ever played the match with no recording, so his aggregate row
    # cannot be reproduced either; Alice's can.
    assert scope.in_scope_players == {EOS_A}
    assert EOS_B in scope.unscoped_players
    assert scope.elo_reason


def test_a_half_written_match_is_unscoped_not_compared(tmp_path):
    """Half the match is on disk and half is still arriving. Comparing then
    measures the copy, not the engine."""
    live = _live_scope_db(tmp_path)
    idx = sp.ArchiveIndex(eligible={"HAVE": ["a.sqrx"]},
                          still_writing={"HAVE": ["a2.sqrx"]})
    scope = sp.build_scope(live, idx)
    assert scope.in_scope_matches == set()
    assert "still being written" in scope.unscoped_matches["HAVE"]


def test_the_archive_index_applies_the_same_rules_the_backfill_does(tmp_path):
    """Eligibility is the backfill's, not a second opinion: a file the backfill
    skips must be a skipped file here, never a missing row."""
    rec = tmp_path / "recordings"
    rec.mkdir()
    for name, mode in (("live.sqrx", "RAAS"), ("seed.sqrx", "Seed")):
        path = rec / name
        with SqrxWriter(path, server_id="t") as w:
            w.write_line(json.dumps({
                "timestamp": "2026-07-15T12:00:00+00:00", "tick": 1,
                "gameState": {"matchState": "InProgress",
                              "matchId": name[:-5], "gameModeName": mode}}))
        path.with_suffix(".meta.json").write_text(
            json.dumps({"matchId": name[:-5], "gameMode": mode}),
            encoding="utf-8")

    idx = sp.index_archive(rec, skip_newer_than=0.0,
                           seeding_modes=frozenset({"Seed"}),
                           seeding_layers=())
    assert set(idx.eligible) == {"live"}
    assert set(idx.seeding_excluded) == {"seed"}

    # And with the exclusion off — the test box's configuration — both replay.
    idx = sp.index_archive(rec, skip_newer_than=0.0,
                           seeding_modes=frozenset(), seeding_layers=())
    assert set(idx.eligible) == {"live", "seed"}


def _recording(rec_dir: Path, name: str, states: list[str],
               meta: dict | None = None) -> Path:
    path = rec_dir / f"{name}.sqrx"
    with SqrxWriter(path, server_id="t") as w:
        for i, state in enumerate(states, 1):
            w.write_line(json.dumps({
                "tick": i, "timestamp": f"2026-07-15T12:00:{i:02d}+00:00",
                "gameState": {"matchState": state, "matchId": name,
                              "gameModeName": "RAAS"}}))
    if meta is not None:
        path.with_suffix(".meta.json").write_text(
            json.dumps({"matchId": name, **meta}), encoding="utf-8")
    return path


def test_the_index_can_tell_whether_a_recording_holds_its_ending(tmp_path):
    """Recordings written before the closing frames were kept cannot show a
    match ending, and the ones written since can. The harness has to know which
    it is looking at, or it forgives the wrong thing."""
    rec = tmp_path / "recordings"
    rec.mkdir()
    _recording(rec, "old", ["InProgress"] * 3, meta={})
    _recording(rec, "new", ["InProgress", "WaitingPostMatch"], meta={})
    # A sidecar that carries the count answers without opening the file at all.
    _recording(rec, "modern", ["InProgress"], meta={"endFrames": 0})

    idx = sp.index_archive(rec, skip_newer_than=0.0,
                           seeding_modes=frozenset(), seeding_layers=())
    assert idx.endings_recorded == {"old": False, "new": True, "modern": True}
    assert idx.scanned == 2                # "modern" was answered by its sidecar


def test_a_recording_still_being_written_is_not_replayed(tmp_path):
    rec = tmp_path / "recordings"
    rec.mkdir()
    with SqrxWriter(rec / "now.sqrx", server_id="t") as w:
        w.write_line(json.dumps({
            "gameState": {"matchState": "InProgress", "matchId": "NOW"}}))
    idx = sp.index_archive(rec, skip_newer_than=600.0,
                           seeding_modes=frozenset(), seeding_layers=())
    assert set(idx.still_writing) == {"NOW"}
    assert idx.eligible == {}


def test_the_report_says_what_it_excluded_and_why():
    """An exclusion nobody can read is a silent skip wearing a hat."""
    live, replay = _pair(_BASE)
    text = sp.render_text(sp.diff_databases(live, replay, _scope()))
    assert "plugin_alerts" in text
    assert "backfill does not run them" in text.lower()
    assert "VERDICT: parity" in text


def test_the_report_survives_a_round_trip_through_json():
    live, replay = _pair(_BASE)
    replay.execute("UPDATE player_matches SET kills=8")
    blob = json.loads(json.dumps(
        sp.report_json(sp.diff_databases(live, replay, _scope()))))
    assert blob["ok"] is False
    diffs = [d for t in blob["tables"] for d in t["diffs"]]
    assert diffs[0]["column"] == "kills"


def test_the_snapshot_is_a_copy_not_a_read_of_the_live_file(tmp_path):
    """The live writer may be mid-transaction; a filesystem copy would catch it
    there. `backup()` reads through SQLite, so what lands is a database."""
    src = tmp_path / "live.db"
    conn = sqlite3.connect(str(src))
    conn.executescript(_DDL)
    conn.execute("INSERT INTO matches VALUES ('M1','srv','final',1,100,'f','')")
    conn.commit()
    dst = sp.snapshot_db(src, tmp_path / "work" / "snap.db")
    assert dst.exists()
    copy = sqlite3.connect(str(dst))
    assert copy.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1
    # Still a live, writable original.
    conn.execute("INSERT INTO matches VALUES ('M2','srv','final',2,200,'f','')")
    conn.commit()
    assert copy.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1
    conn.close()
    copy.close()


def test_the_module_runs_as_a_command(tmp_path):
    """`python -m scripts.stats_parity --help` is how anyone will meet it."""
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.stats_parity", "--help"],
        cwd=str(repo), capture_output=True, text=True)
    assert proc.returncode == 0
    assert "--recordings-dir" in proc.stdout
