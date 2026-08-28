"""Prove that the recordings alone reproduce the stats the agent computed live.

The whole SquidHub design rests on one property: a `.sqrx` file is the complete
record of a match, so the platform can compute every statistic from uploaded
files and the agent can go record-only. Until now that property was believed.
This is how it gets measured.

    python -m scripts.stats_parity --recordings-dir DIR --live-db PATH

What it does:

1. **Snapshots the live DB first.** The writer may be running, so the file on
   disk is not a consistent read. A `sqlite3.Connection.backup()` copy is, and
   everything downstream diffs the copy.
2. **Replays the whole archive** into a fresh DB through the REAL backfill code
   path (`sqreader stats-backfill`, as a subprocess, exactly as an operator or
   SquidHub ingest would run it). Nothing about the stats rules is reimplemented
   here — one implementation is the entire point.
3. **Diffs the two databases**, table by table, row by SEMANTIC key, with the
   columns discovered from the live DB's own schema. New columns are therefore
   covered the day they land, with no change to this file: the harness has to be
   immune to the enrichment churn it exists to protect.

Two things it deliberately does NOT do:

* **It does not compare per match.** ELO is order-dependent — replaying one
  match into an empty DB cannot reproduce a rating that was earned against
  earlier ones. So the archive is replayed whole, in chronological order, and
  the result is compared wholesale.
* **It does not fail on rows the archive cannot reproduce.** A live DB holds
  matches recorded before the recorder was deployed, and matches whose
  recordings retention swept. Those are counted and listed as *unscoped*, never
  as failures. Everything with a recording present is in scope — `unverified`
  matches included, because that path is a suspect, not an exemption.

Exit status is 0 when every in-scope row matches and non-zero when any does not,
so this doubles as the regression gate for changes to `stats.py`, `elo.py`,
`recorder.py`, `possample.py`, or the wire format.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqreader.recording_lifecycle import (                      # noqa: E402
    INACTIVE_MATCH_STATES, is_excluded_match,
)
from sqreader.sqrx import SqrxReader                            # noqa: E402

# --------------------------------------------------------------------------
# What identifies a row
#
# Autoincrement ids are never keys: they number the order rows were written,
# not the thing they describe, so two databases that agree perfectly would
# still disagree on every one of them. Each table is instead keyed by the
# columns that identify the real-world event behind the row.
# --------------------------------------------------------------------------

# How a table's rows are scoped to the part of history the archive can speak
# for. See `Scope` below for what each one means.
SCOPE_MATCH = "match"       # the row names a match; scope follows that match
SCOPE_PLAYER = "player"     # the row aggregates one player across their matches
SCOPE_ELO = "elo"           # a rating: depends on every match ever rated


@dataclass(frozen=True)
class TableSpec:
    key: tuple[str, ...]
    scope: str
    scope_col: str
    why: str


TABLE_SPECS: dict[str, TableSpec] = {
    "matches": TableSpec(
        key=("match_id",), scope=SCOPE_MATCH, scope_col="match_id",
        why="the game's own match id"),
    "player_matches": TableSpec(
        key=("match_id", "eos_id"), scope=SCOPE_MATCH, scope_col="match_id",
        why="one player in one match — the table's own primary key"),
    "players": TableSpec(
        key=("eos_id",), scope=SCOPE_PLAYER, scope_col="eos_id",
        why="one account — the table's own primary key"),
    "player_elo": TableSpec(
        key=("eos_id",), scope=SCOPE_ELO, scope_col="eos_id",
        why="one account — the table's own primary key"),
    "kill_events": TableSpec(
        key=("match_id", "victim_name", "attacker_name", "ts", "killed",
             "wounded"),
        scope=SCOPE_MATCH, scope_col="match_id",
        why="the columns the live dedupe index already treats as identity "
            "(uq_ke_dedupe) — the same rule that stops the writer storing one "
            "kill twice"),
    "vehicle_session": TableSpec(
        key=("match_id", "eos_id", "entered_at"),
        scope=SCOPE_MATCH, scope_col="match_id",
        # Chosen, and worth writing down: a player occupies at most one seat at
        # a time, and a session is only ever written when it spanned more than
        # one second (`_close_vehicle_session` drops the one-tick taps), so no
        # two of a player's sessions in a match can share an entry second.
        # `vehicle_class` and `seat` are deliberately OUT of the key: they are
        # values to be compared, and folding them into identity would turn one
        # wrong seat number into a missing row plus an extra row instead of a
        # single legible diff.
        why="a player's stint, identified by when they got in"),
}

# Tables that are compared by construction — with the reason stated in the
# report rather than silently skipped.
EXCLUDED_TABLES: dict[str, str] = {
    "plugin_alerts":
        "plugins run live, against the running server, on the serve thread. "
        "Backfill does not run them, so the replay can only ever hold zero "
        "alerts. Excluded by design, not by tolerance.",
    "sqlite_sequence":
        "SQLite's own AUTOINCREMENT bookkeeping. It counts rows written, not "
        "anything observed about a match.",
}

# Float columns and how close counts as equal.
#
# The default is EXACT, and that is a deliberate claim, not laziness: the replay
# feeds the same values through the same arithmetic in the same order as the
# live writer did, so a float that differs means the ORDER changed — which is a
# finding worth a person's attention, not noise worth absorbing. Any entry added
# here must carry the reason its column genuinely cannot be exact.
FLOAT_TOLERANCES: dict[tuple[str, str], tuple[float, str]] = {}

DEFAULT_FLOAT_TOLERANCE = 0.0
DEFAULT_FLOAT_REASON = (
    "exact: the replay runs the same accumulation over the same values in the "
    "same order, so any difference is a change in that order")


# The catalog of legitimate differences.
#
# Every difference the harness meets gets exactly one of two fates: it is fixed,
# or it is written down here with the reason. There is no third bucket and no
# quietly widened tolerance. Each entry below is narrow on purpose — it forgives
# one column under one stated condition — and the report counts how many rows it
# actually forgave, so an exclusion cannot grow without somebody seeing it.

@dataclass(frozen=True)
class ColumnExclusion:
    reason: str
    # (live_row, replay_row) -> whether the reason applies to THIS pair. None
    # means it always does.
    when: Optional[Callable[[dict, dict], bool]] = None


def _both_endings_unobserved(live: dict, replay: dict) -> bool:
    return (live.get("end_state") == "unverified"
            and replay.get("end_state") == "unverified")


EXCLUDED_COLUMNS: dict[tuple[str, str], ColumnExclusion] = {
    ("matches", "end_reason"): ColumnExclusion(
        reason="only when BOTH sides agree the ending was never observed. The "
               "reason then describes what happened to the AGENT, not to the "
               "match — 'left open by a hard kill' is an event a recording "
               "cannot contain, and the replay says 'superseded' because the "
               "archive simply moved on. Deliberately conditional: when either "
               "side did observe an ending the string carries which frame the "
               "ticket counts came from, and that stays under comparison.",
        when=_both_endings_unobserved),
}


# Columns whose value depends on having WATCHED the match end. A recording made
# before the recorder wrote its closing frames cannot reproduce any of them —
# the evidence is not in the file. This is a fact about specific files, not a
# tolerance: it applies only to matches the harness can show have no ending
# recorded, and it disappears on its own as the archive turns over.
UNRECORDED_ENDING_COLUMNS: dict[str, tuple[str, ...]] = {
    "matches": ("end_state", "end_reason", "winner_team"),
    "player_matches": ("elo_change",),
}
UNRECORDED_ENDING_REASON = (
    "the archive holds no frame showing this match end, so the replay cannot "
    "confirm an ending the live writer watched happen. Recordings written "
    "before the recorder started keeping its closing frames are all like this; "
    "the exclusion expires by itself the moment a match is recorded with them")


# --------------------------------------------------------------------------
# Scope — which rows the archive is allowed to be asked about
# --------------------------------------------------------------------------

@dataclass
class Scope:
    """Which rows the archive can be held to, and why the rest cannot.

    `unscoped_matches` maps a match id to the sentence that explains it. It is
    never an error list: a live DB legitimately holds history the archive does
    not cover.
    """
    in_scope_matches: set[str] = field(default_factory=set)
    unscoped_matches: dict[str, str] = field(default_factory=dict)
    in_scope_players: set[str] = field(default_factory=set)
    unscoped_players: dict[str, str] = field(default_factory=dict)
    elo_reason: Optional[str] = None   # non-None means ratings are unscoped
    # In-scope matches whose ENDING is not in the archive. Everything about how
    # they were played is still compared; only the columns that describe the
    # ending are forgiven. See UNRECORDED_ENDING_COLUMNS.
    endings_unrecorded: set[str] = field(default_factory=set)

    def forgives(self, table: str, column: str, spec: "TableSpec",
                 live_row: dict, replay_row: dict) -> Optional[str]:
        """The written reason this column may differ on this row, or None."""
        if (spec.scope == SCOPE_MATCH
                and column in UNRECORDED_ENDING_COLUMNS.get(table, ())
                and str(live_row.get(spec.scope_col)) in self.endings_unrecorded):
            return UNRECORDED_ENDING_REASON
        rule = EXCLUDED_COLUMNS.get((table, column))
        if rule is not None and (rule.when is None
                                 or rule.when(live_row, replay_row)):
            return rule.reason
        return None

    def row_in_scope(self, spec: TableSpec, value: Any) -> tuple[bool, str]:
        """Whether a row with this scope-column value is in scope, and if not,
        the written reason."""
        if spec.scope == SCOPE_MATCH:
            if value in self.in_scope_matches:
                return (True, "")
            return (False, self.unscoped_matches.get(
                str(value), "no recording in the archive for this match"))
        if spec.scope == SCOPE_PLAYER:
            if value in self.in_scope_players:
                return (True, "")
            return (False, self.unscoped_players.get(
                str(value), "this account's history reaches outside the archive"))
        if spec.scope == SCOPE_ELO:
            if self.elo_reason:
                return (False, self.elo_reason)
            if value in self.in_scope_players:
                return (True, "")
            return (False, self.unscoped_players.get(
                str(value), "this account's history reaches outside the archive"))
        return (True, "")


# --------------------------------------------------------------------------
# The archive: which matches it can speak for
# --------------------------------------------------------------------------

@dataclass
class ArchiveIndex:
    eligible: dict[str, list[str]] = field(default_factory=dict)
    still_writing: dict[str, list[str]] = field(default_factory=dict)
    seeding_excluded: dict[str, list[str]] = field(default_factory=dict)
    unidentified: list[str] = field(default_factory=list)
    # match id -> whether any of its recordings shows the match ending.
    endings_recorded: dict[str, bool] = field(default_factory=dict)
    # How many files had to be read end to end to answer that.
    scanned: int = 0

    @property
    def file_count(self) -> int:
        return (sum(len(v) for v in self.eligible.values())
                + sum(len(v) for v in self.still_writing.values())
                + sum(len(v) for v in self.seeding_excluded.values())
                + len(self.unidentified))


def _sidecar(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.with_suffix(".meta.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _match_id_of(path: Path, meta: Optional[dict]) -> Optional[str]:
    """The match this recording belongs to.

    The sidecar names it, which costs nothing to read. When the sidecar is
    missing or was written before the id resolved, fall back to the stream
    itself — the frames carry it, and a recording whose match cannot be named
    would otherwise silently widen the unscoped set.
    """
    if meta:
        mid = meta.get("matchId")
        if isinstance(mid, str) and mid:
            return mid
    try:
        with SqrxReader(path) as r:
            for n, line in enumerate(r):
                if n > 64:            # a full frame arrives long before this
                    break
                try:
                    snap = json.loads(line)
                except ValueError:
                    continue
                gs = snap.get("gameState") if isinstance(snap, dict) else None
                mid = (gs or {}).get("matchId")
                if isinstance(mid, str) and mid:
                    return mid
    except Exception:
        return None
    return None


def _records_the_ending(path: Path, meta: Optional[dict]) -> tuple[bool, bool]:
    """Whether this recording could show its match ending, and whether the file
    had to be read to find out.

    The sidecar answers it for nothing when the recorder wrote one: `endFrames`
    exists on every recording made since the closing frames started being kept,
    so its mere PRESENCE says the file holds whatever evidence there was — zero
    included, which honestly means "the agent never saw this match end either".

    Only an older sidecar (or a missing one) needs the file opened, and then the
    question is simply whether any frame carries a state that means "not
    playing". That is the same evidence the stats writer confirms an ending
    from, asked of the archive instead of the live reader.
    """
    if meta is not None and "endFrames" in meta:
        return (True, False)
    try:
        with SqrxReader(path) as r:
            for line in r:
                try:
                    snap = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(snap, dict) or snap.get("t") == "pos":
                    continue
                state = (snap.get("gameState") or {}).get("matchState")
                if state in INACTIVE_MATCH_STATES:
                    return (True, True)
    except Exception:
        return (False, True)
    return (False, True)


def index_archive(rec_dir: Path, *, skip_newer_than: float,
                  seeding_modes: frozenset[str],
                  seeding_layers: tuple[str, ...],
                  now: Optional[float] = None) -> ArchiveIndex:
    """Sort every recording into what the replay will and will not consume.

    The eligibility rules are `cmd_stats_backfill`'s, applied here for the same
    reason the end-of-match policy is imported rather than restated: if this
    file guessed differently from the backfill it drives, a recording the
    backfill dropped would be scored as a missing row instead of a skipped file.
    """
    idx = ArchiveIndex()
    now = time.time() if now is None else now
    for path in sorted(rec_dir.glob("*.sqrx")):
        meta = _sidecar(path)
        mid = _match_id_of(path, meta)
        if mid is None:
            idx.unidentified.append(path.name)
            continue
        try:
            age = now - path.stat().st_mtime
        except OSError:
            age = 0.0
        if age < skip_newer_than:
            idx.still_writing.setdefault(mid, []).append(path.name)
            continue
        if (seeding_modes or seeding_layers) and meta is not None \
                and is_excluded_match(meta, game_modes=seeding_modes,
                                      layer_patterns=seeding_layers):
            idx.seeding_excluded.setdefault(mid, []).append(path.name)
            continue
        idx.eligible.setdefault(mid, []).append(path.name)
        ends, scanned = _records_the_ending(path, meta)
        idx.scanned += 1 if scanned else 0
        idx.endings_recorded[mid] = idx.endings_recorded.get(mid, False) or ends
    return idx


def build_scope(live: sqlite3.Connection, idx: ArchiveIndex) -> Scope:
    """Decide what the archive can be held to, from the live DB and the index."""
    scope = Scope()
    live_matches = {
        str(r[0]): r[1] for r in
        live.execute("SELECT match_id, status FROM matches")}

    for mid in set(live_matches) | set(idx.eligible):
        status = live_matches.get(mid)
        if mid not in idx.eligible:
            if mid in idx.seeding_excluded:
                scope.unscoped_matches[mid] = (
                    "its recording is excluded from backfill by the seeding "
                    "policy in effect ("
                    + ", ".join(idx.seeding_excluded[mid]) + ")")
            elif mid in idx.still_writing:
                scope.unscoped_matches[mid] = (
                    "its recording is still being written")
            else:
                scope.unscoped_matches[mid] = (
                    "no recording in the archive — recorded before the "
                    "recorder was deployed, or swept by retention")
            continue
        if mid in idx.still_writing:
            # Half the match is on disk and half is still arriving. Comparing
            # a finished row against a partial replay would measure the copy,
            # not the engine.
            scope.unscoped_matches[mid] = (
                "the archive holds only part of it — another recording for the "
                "same match is still being written")
            continue
        if status == "open":
            # The live row is still accumulating. Whatever the archive holds,
            # it cannot hold the rest of a match that has not ended.
            scope.unscoped_matches[mid] = (
                "the live row is still open — the match has not ended yet")
            continue
        scope.in_scope_matches.add(mid)
        if not idx.endings_recorded.get(mid, False):
            scope.endings_unrecorded.add(mid)

    # A player row aggregates every match that account ever played. It can only
    # be reproduced when all of them are in scope.
    for eos, mid in live.execute(
            "SELECT eos_id, match_id FROM player_matches"):
        if str(mid) not in scope.in_scope_matches:
            scope.unscoped_players.setdefault(
                str(eos),
                "played at least one match the archive cannot reproduce")
    known = {str(r[0]) for r in live.execute("SELECT eos_id FROM players")}
    known |= {str(r[0]) for r in live.execute(
        "SELECT DISTINCT eos_id FROM player_matches")}
    scope.in_scope_players = known - set(scope.unscoped_players)

    # Ratings are the one thing that cannot be scoped per row. A rating moves
    # against the OTHER team's average, so one unreproducible match perturbs
    # every player who met anyone who played it, and then everyone they met.
    # Either the archive covers the whole rated history or it covers none of it.
    blind = len(scope.unscoped_matches) + len(scope.endings_unrecorded)
    if blind:
        scope.elo_reason = (
            f"{blind} match(es) are either unscoped or have no recorded "
            "ending, and an unended match is never rated. A rating is computed "
            "against the opposing team's average, so one unreproducible match "
            "perturbs every rating downstream of it")
    return scope


# --------------------------------------------------------------------------
# The differ (pure: two connections in, a report out)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Diff:
    table: str
    kind: str                 # value | missing | extra | schema | duplicate
    key: tuple
    column: Optional[str]
    live: Any
    replay: Any
    note: str = ""


@dataclass
class TableReport:
    table: str
    live_rows: int = 0
    replay_rows: int = 0
    scoped_rows: int = 0
    unscoped_rows: int = 0
    compared_columns: list[str] = field(default_factory=list)
    skipped_columns: dict[str, str] = field(default_factory=dict)
    # (column, the written reason) -> how many values that reason forgave. Kept
    # per reason, not per column, so two rules that can both reach the same
    # column are never credited with each other's rows: an exclusion that
    # forgives nothing costs nothing, and one that starts forgiving hundreds
    # says so in its own line.
    forgiven: dict[tuple[str, str], int] = field(default_factory=dict)
    diffs: list[Diff] = field(default_factory=list)
    excluded_reason: Optional[str] = None


@dataclass
class ParityReport:
    tables: list[TableReport] = field(default_factory=list)
    scope: Scope = field(default_factory=Scope)
    notes: list[str] = field(default_factory=list)

    @property
    def diffs(self) -> list[Diff]:
        return [d for t in self.tables for d in t.diffs]

    @property
    def ok(self) -> bool:
        return not self.diffs


def table_names(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


def table_columns(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return list(conn.execute(f"PRAGMA table_info({_ident(table)})"))


def _ident(name: str) -> str:
    """A SQLite identifier we are willing to interpolate.

    Table and column names come from `sqlite_master` / `PRAGMA table_info` on a
    database we opened, never from a user string, but the quoting is here so
    that stays true by construction rather than by memory.
    """
    return '"' + str(name).replace('"', '""') + '"'


def _surrogate_columns(cols: list[sqlite3.Row],
                       key: tuple[str, ...]) -> dict[str, str]:
    """Columns that number rows rather than describe them.

    A lone INTEGER PRIMARY KEY is SQLite's rowid under another name: it records
    the order rows were inserted. Two databases built from the same events in
    the same order would still disagree the moment one of them ever inserted
    anything else. Detected from the schema so a future table gets the same
    treatment without being listed anywhere.
    """
    pk = [c for c in cols if c["pk"]]
    if len(pk) != 1:
        return {}
    only = pk[0]
    if str(only["type"]).upper() != "INTEGER" or only["name"] in key:
        return {}
    return {str(only["name"]):
            "surrogate row id (INTEGER PRIMARY KEY) — it numbers writes, not "
            "observations"}


def _tolerance(table: str, column: str) -> tuple[float, str]:
    return FLOAT_TOLERANCES.get(
        (table, column), (DEFAULT_FLOAT_TOLERANCE, DEFAULT_FLOAT_REASON))


def values_equal(table: str, column: str, a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) or isinstance(b, float):
        try:
            tol, _ = _tolerance(table, column)
            return abs(float(a) - float(b)) <= tol
        except (TypeError, ValueError):
            return False
    return bool(a == b)


def _load_rows(conn: sqlite3.Connection, table: str, columns: list[str],
               key: tuple[str, ...]) -> tuple[dict[tuple, dict], list[tuple]]:
    """Rows by semantic key, plus any key that turned up more than once."""
    select = ", ".join(_ident(c) for c in columns)
    rows: dict[tuple, dict] = {}
    dupes: list[tuple] = []
    for r in conn.execute(f"SELECT {select} FROM {_ident(table)}"):
        row = {c: r[i] for i, c in enumerate(columns)}
        k = tuple(row.get(c) for c in key)
        if k in rows:
            dupes.append(k)
            continue
        rows[k] = row
    return rows, dupes


def diff_databases(live: sqlite3.Connection, replay: sqlite3.Connection,
                   scope: Scope) -> ParityReport:
    """Compare two stats databases row by semantic key, column by column.

    The columns come from the LIVE database's schema, which is what makes this
    survive the enrichment work it exists to guard: a column added to
    `player_matches` next month is compared the day it appears, with no edit
    here. A column that exists on only one side is reported loudly rather than
    quietly ignored, because schema drift between the live writer and the
    replay path is exactly the kind of thing that would otherwise hide a gap.
    """
    report = ParityReport(scope=scope)
    live_tables = set(table_names(live))
    replay_tables = set(table_names(replay))

    for table in sorted(live_tables | replay_tables):
        tr = TableReport(table=table)
        report.tables.append(tr)

        if table in EXCLUDED_TABLES:
            tr.excluded_reason = EXCLUDED_TABLES[table]
            if table in live_tables:
                tr.live_rows = _count(live, table)
            if table in replay_tables:
                tr.replay_rows = _count(replay, table)
            continue

        if table not in live_tables or table not in replay_tables:
            side = "replay" if table in replay_tables else "live"
            tr.diffs.append(Diff(
                table=table, kind="schema", key=(), column=None,
                live=table in live_tables, replay=table in replay_tables,
                note=f"table exists only in the {side} database"))
            continue

        spec = TABLE_SPECS.get(table)
        if spec is None:
            # Not a silent skip: a table nobody taught this harness to identify
            # rows in is a gap in the proof, and it should cost a red run until
            # somebody writes down what a row means.
            tr.live_rows = _count(live, table)
            tr.replay_rows = _count(replay, table)
            tr.diffs.append(Diff(
                table=table, kind="schema", key=(), column=None,
                live=tr.live_rows, replay=tr.replay_rows,
                note="no semantic key is defined for this table — add one to "
                     "TABLE_SPECS (or a reason to EXCLUDED_TABLES) so its rows "
                     "can be compared"))
            continue

        live_cols = table_columns(live, table)
        live_names = [str(c["name"]) for c in live_cols]
        replay_names = [str(c["name"]) for c in table_columns(replay, table)]
        for name in sorted(set(live_names) ^ set(replay_names)):
            side = "live" if name in live_names else "replay"
            tr.diffs.append(Diff(
                table=table, kind="schema", key=(), column=name,
                live=name in live_names, replay=name in replay_names,
                note=f"column exists only in the {side} database"))

        tr.skipped_columns = _surrogate_columns(live_cols, spec.key)
        missing_key = [c for c in spec.key if c not in live_names]
        if missing_key:
            tr.diffs.append(Diff(
                table=table, kind="schema", key=(), column=",".join(missing_key),
                live=None, replay=None,
                note="the semantic key names columns this table does not have"))
            continue

        shared = [c for c in live_names
                  if c in replay_names and c not in tr.skipped_columns]
        tr.compared_columns = shared
        fetch = sorted(set(shared) | set(spec.key))

        live_rows, live_dupes = _load_rows(live, table, fetch, spec.key)
        replay_rows, replay_dupes = _load_rows(replay, table, fetch, spec.key)
        tr.live_rows = len(live_rows) + len(live_dupes)
        tr.replay_rows = len(replay_rows) + len(replay_dupes)
        for k in sorted(set(live_dupes) | set(replay_dupes), key=_sort_key):
            tr.diffs.append(Diff(
                table=table, kind="duplicate", key=k, column=None,
                live=live_dupes.count(k) + 1 if k in live_dupes else 1,
                replay=replay_dupes.count(k) + 1 if k in replay_dupes else 1,
                note="two rows share one semantic key — the key does not "
                     "identify a row"))

        for k in sorted(set(live_rows) | set(replay_rows), key=_sort_key):
            row = live_rows.get(k) or replay_rows.get(k) or {}
            ok, why = scope.row_in_scope(spec, row.get(spec.scope_col))
            if not ok:
                tr.unscoped_rows += 1
                continue
            tr.scoped_rows += 1
            if k not in replay_rows:
                tr.diffs.append(Diff(
                    table=table, kind="missing", key=k, column=None,
                    live="row present", replay=None,
                    note="the live DB has this row and the replay does not"))
                continue
            if k not in live_rows:
                tr.diffs.append(Diff(
                    table=table, kind="extra", key=k, column=None,
                    live=None, replay="row present",
                    note="the replay produced a row the live DB never wrote"))
                continue
            lrow, rrow = live_rows[k], replay_rows[k]
            for col in shared:
                if values_equal(table, col, lrow.get(col), rrow.get(col)):
                    continue
                forgiven = scope.forgives(table, col, spec, lrow, rrow)
                if forgiven is not None:
                    tr.forgiven[(col, forgiven)] = \
                        tr.forgiven.get((col, forgiven), 0) + 1
                    continue
                tr.diffs.append(Diff(
                    table=table, kind="value", key=k, column=col,
                    live=lrow.get(col), replay=rrow.get(col)))
    return report


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {_ident(table)}").fetchone()[0])


def _sort_key(k: tuple) -> tuple:
    """Order keys for the report without tripping over NULLs and mixed types."""
    return tuple((v is None, str(type(v)), str(v)) for v in k)


# --------------------------------------------------------------------------
# Driving the real code path
# --------------------------------------------------------------------------

def snapshot_db(src: Path, dst: Path) -> Path:
    """A consistent copy of a database that may be being written right now.

    Copying the file with the filesystem would catch the writer mid-transaction
    — with WAL on, it would also leave the -wal behind. `backup()` reads through
    SQLite, so what lands in `dst` is a database, not a moment.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return dst


def run_backfill(recordings_dir: Path, out_db: Path, *, server_id: str,
                 skip_newer_than: float, config_path: Optional[Path] = None,
                 quiet: bool = True) -> None:
    """Replay the archive through `sqreader stats-backfill`.

    A subprocess, not an import, and that is the point: this is the same
    command an operator runs and the same one SquidHub ingest will run, so the
    thing being measured is the shipped path — including its config resolution,
    its file ordering and its end-of-run finalize — rather than a convenient
    in-process approximation of it.
    """
    repo = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    if config_path is not None:
        env["SQREADER_CONFIG"] = str(config_path)
    argv = [sys.executable, "-m", "sqreader", "stats-backfill",
            "--recordings-dir", str(recordings_dir),
            "--stats-db", str(out_db),
            "--server-id", server_id,
            "--skip-newer-than", str(skip_newer_than)]
    proc = subprocess.run(argv, cwd=str(repo), env=env, text=True,
                          capture_output=True)
    if not quiet:
        sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(
            f"stats-backfill failed (exit {proc.returncode}) — the replay "
            f"never happened, so there is nothing to compare")


def _open_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def default_server_id(live: sqlite3.Connection) -> Optional[str]:
    ids = [str(r[0]) for r in live.execute(
        "SELECT DISTINCT server_id FROM matches WHERE server_id IS NOT NULL")]
    return ids[0] if len(ids) == 1 else None


# --------------------------------------------------------------------------
# Report rendering
# --------------------------------------------------------------------------

def _fmt(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, str):
        return repr(v) if len(v) <= 60 else repr(v[:57] + "...")
    return str(v)


def render_text(report: ParityReport, *, max_diffs: int = 40) -> str:
    out: list[str] = []
    w = out.append
    w("stats parity report")
    w("=" * 72)
    for note in report.notes:
        w(f"  {note}")
    w("")

    scope = report.scope
    w("scope")
    w(f"  matches in scope   : {len(scope.in_scope_matches)}")
    w(f"  matches unscoped   : {len(scope.unscoped_matches)}")
    for mid, why in sorted(scope.unscoped_matches.items()):
        w(f"      {mid}  —  {why}")
    if scope.unscoped_players:
        w(f"  accounts unscoped  : {len(scope.unscoped_players)} "
          f"(they played an unscoped match)")
    if scope.endings_unrecorded:
        w(f"  endings not in the archive : {len(scope.endings_unrecorded)} "
          f"in-scope match(es)")
        for line in _wrap(UNRECORDED_ENDING_REASON, 62):
            w(f"      {line}")
        for mid in sorted(scope.endings_unrecorded):
            w(f"      {mid}")
    if scope.elo_reason:
        w(f"  ratings unscoped   : {scope.elo_reason}")
    w("")

    w("excluded by design")
    for table, why in sorted(EXCLUDED_TABLES.items()):
        w(f"  {table}")
        for line in _wrap(why, 66):
            w(f"      {line}")
    for (table, col), rule in sorted(EXCLUDED_COLUMNS.items()):
        n = _forgiven_by(report, rule.reason, table=table, column=col)
        w(f"  {table}.{col} — forgave {n} value(s)")
        for line in _wrap(rule.reason, 66):
            w(f"      {line}")
    ending_rows = _forgiven_by(report, UNRECORDED_ENDING_REASON)
    if ending_rows:
        w(f"  end-of-match columns — forgave {ending_rows} value(s) on "
          f"{len(scope.endings_unrecorded)} match(es) with no recorded ending: "
          + ", ".join(f"{t}.{c}" for t, cols in
                      sorted(UNRECORDED_ENDING_COLUMNS.items()) for c in cols))
    w("")

    w("tables")
    w(f"  {'table':<18}{'live':>8}{'replay':>8}{'scoped':>8}"
      f"{'unscoped':>10}{'diffs':>7}   columns")
    for tr in report.tables:
        if tr.excluded_reason:
            w(f"  {tr.table:<18}{tr.live_rows:>8}{tr.replay_rows:>8}"
              f"{'-':>8}{'-':>10}{'-':>7}   excluded")
            continue
        w(f"  {tr.table:<18}{tr.live_rows:>8}{tr.replay_rows:>8}"
          f"{tr.scoped_rows:>8}{tr.unscoped_rows:>10}{len(tr.diffs):>7}"
          f"   {len(tr.compared_columns)}")
    w("")

    skipped = {(tr.table, c): why
               for tr in report.tables for c, why in tr.skipped_columns.items()}
    if skipped:
        w("columns not compared")
        for (table, col), why in sorted(skipped.items()):
            w(f"  {table}.{col} — {why}")
        w("")

    diffs = report.diffs
    if not diffs:
        w("VERDICT: parity. Every in-scope row matches, column for column.")
        return "\n".join(out) + "\n"

    w(f"diffs ({len(diffs)})")
    shown = 0
    for tr in report.tables:
        if not tr.diffs:
            continue
        w(f"  [{tr.table}]")
        for d in tr.diffs:
            if shown >= max_diffs:
                w(f"      ... and {len(diffs) - shown} more")
                shown = len(diffs)
                break
            key = ", ".join(_fmt(v) for v in d.key)
            head = f"      {d.kind:<9} ({key})" if key else f"      {d.kind:<9}"
            if d.column:
                head += f" {d.column}"
            w(head)
            w(f"          live={_fmt(d.live)}  replay={_fmt(d.replay)}")
            if d.note:
                w(f"          {d.note}")
            shown += 1
        if shown >= max_diffs:
            break
    w("")
    w(f"VERDICT: {len(diffs)} in-scope difference(s). Each one is either an "
      f"engine bug to fix or an exclusion to write down.")
    return "\n".join(out) + "\n"


def _forgiven_by(report: ParityReport, reason: str, *,
                 table: Optional[str] = None,
                 column: Optional[str] = None) -> int:
    """How many values one written reason actually forgave."""
    return sum(
        n for t in report.tables if table in (None, t.table)
        for (col, why), n in t.forgiven.items()
        if why == reason and column in (None, col))


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for word in words:
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}" if cur else word
    if cur:
        lines.append(cur)
    return lines


def report_json(report: ParityReport) -> dict:
    return {
        "ok": report.ok,
        "notes": report.notes,
        "scope": {
            "matchesInScope": sorted(report.scope.in_scope_matches),
            "matchesUnscoped": report.scope.unscoped_matches,
            "accountsUnscoped": report.scope.unscoped_players,
            "ratingsUnscopedReason": report.scope.elo_reason,
            "endingsUnrecorded": sorted(report.scope.endings_unrecorded),
            "endingsUnrecordedReason": UNRECORDED_ENDING_REASON,
        },
        "excludedTables": EXCLUDED_TABLES,
        "excludedColumns": {f"{t}.{c}": rule.reason
                            for (t, c), rule in EXCLUDED_COLUMNS.items()},
        "floatTolerances": {
            f"{t}.{c}": {"tolerance": tol, "reason": why}
            for (t, c), (tol, why) in FLOAT_TOLERANCES.items()},
        "defaultFloatTolerance": {
            "tolerance": DEFAULT_FLOAT_TOLERANCE,
            "reason": DEFAULT_FLOAT_REASON},
        "tables": [
            {"table": t.table, "liveRows": t.live_rows,
             "replayRows": t.replay_rows, "scopedRows": t.scoped_rows,
             "unscopedRows": t.unscoped_rows,
             "comparedColumns": t.compared_columns,
             "skippedColumns": t.skipped_columns,
             "forgiven": [{"column": c, "values": n, "reason": why}
                          for (c, why), n in t.forgiven.items()],
             "excludedReason": t.excluded_reason,
             "diffs": [{"kind": d.kind, "key": [_json_safe(v) for v in d.key],
                        "column": d.column, "live": _json_safe(d.live),
                        "replay": _json_safe(d.replay), "note": d.note}
                       for d in t.diffs]}
            for t in report.tables],
    }


def _json_safe(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Replay a recordings archive and diff the result against "
                    "the live stats DB.")
    ap.add_argument("--recordings-dir", type=Path, required=True,
                    help="directory of .sqrx recordings (the archive)")
    ap.add_argument("--live-db", type=Path, required=True,
                    help="the live player-stats DB; snapshotted, never read "
                         "in place")
    ap.add_argument("--work-dir", type=Path, default=None,
                    help="where the snapshot and the replay DB are built "
                         "(default: a temp dir, removed on exit)")
    ap.add_argument("--config", type=Path, default=None,
                    help="sqreader.config.json the replay should run under — "
                         "use the one the live server runs, so the seeding "
                         "policy matches")
    ap.add_argument("--server-id", default=None,
                    help="server id to stamp on replayed matches (default: "
                         "the one the live DB already uses)")
    ap.add_argument("--skip-newer-than", type=float, default=120.0,
                    help="treat recordings modified within N seconds as still "
                         "being written (default 120, same as backfill)")
    ap.add_argument("--out", type=Path, default=None,
                    help="write the text report here as well as to stdout")
    ap.add_argument("--json", type=Path, default=None,
                    help="write the machine-readable report here")
    ap.add_argument("--max-diffs", type=int, default=40,
                    help="how many individual diffs to print (default 40)")
    ap.add_argument("--keep", action="store_true",
                    help="keep the work dir (snapshot + replay DB) for "
                         "poking at afterwards")
    ap.add_argument("--verbose", action="store_true",
                    help="show the backfill's own progress output")
    args = ap.parse_args(argv)

    if args.config is not None:
        # Set before anything reads config: `sqreader.config` caches on first
        # use, and the seeding policy decided here has to be the same one the
        # backfill subprocess decides.
        os.environ["SQREADER_CONFIG"] = str(args.config)
    from sqreader import config                                   # noqa: E402

    if not args.recordings_dir.is_dir():
        print(f"no such recordings dir: {args.recordings_dir}", file=sys.stderr)
        return 2
    if not args.live_db.is_file():
        print(f"no such live DB: {args.live_db}", file=sys.stderr)
        return 2

    tmp = None
    if args.work_dir is None:
        tmp = tempfile.TemporaryDirectory(prefix="stats_parity_")
        work = Path(tmp.name)
    else:
        work = args.work_dir
        work.mkdir(parents=True, exist_ok=True)
    try:
        return _run(args, work, config)
    finally:
        if tmp is not None and not args.keep:
            tmp.cleanup()
        elif tmp is not None:
            print(f"work dir kept at {work}", file=sys.stderr)


def _run(args: argparse.Namespace, work: Path, config: Any) -> int:
    snapshot = snapshot_db(args.live_db, work / "live_snapshot.db")
    live = _open_ro(snapshot)
    try:
        server_id = args.server_id or default_server_id(live) \
            or str(config.get("server_id"))

        seeding_modes = frozenset(
            s for s in (config.get("seeding_game_modes") or ()) if isinstance(s, str))
        seeding_layers = tuple(
            s for s in (config.get("seeding_layer_patterns") or ()) if isinstance(s, str))

        idx = index_archive(args.recordings_dir,
                            skip_newer_than=args.skip_newer_than,
                            seeding_modes=seeding_modes,
                            seeding_layers=seeding_layers)
        replay_db = work / "replay.db"
        if replay_db.exists():
            replay_db.unlink()
        run_backfill(args.recordings_dir, replay_db, server_id=server_id,
                     skip_newer_than=args.skip_newer_than,
                     config_path=args.config, quiet=not args.verbose)
        if not replay_db.exists():
            print("the backfill produced no database", file=sys.stderr)
            return 2

        replay = _open_ro(replay_db)
        try:
            scope = build_scope(live, idx)
            report = diff_databases(live, replay, scope)
        finally:
            replay.close()
    finally:
        live.close()

    eligible_files = sum(len(v) for v in idx.eligible.values())
    report.notes = [
        f"live DB   : {args.live_db} (snapshotted via the backup API)",
        f"archive   : {args.recordings_dir} — {idx.file_count} recording(s), "
        f"{eligible_files} replayed",
        f"server id : {server_id}",
        f"seeding   : modes={sorted(seeding_modes) or '-'} "
        f"layers={list(seeding_layers) or '-'}",
    ]
    if idx.scanned:
        report.notes.append(
            f"scanned {idx.scanned} recording(s) end to end for the frames "
            f"that show a match ending — their sidecars predate `endFrames`")
    if idx.unidentified:
        report.notes.append(
            f"WARNING: {len(idx.unidentified)} recording(s) name no match id: "
            + ", ".join(idx.unidentified[:5]))

    text = render_text(report, max_diffs=args.max_diffs)
    print(text, end="")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report_json(report), indent=2, ensure_ascii=False),
            encoding="utf-8")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
