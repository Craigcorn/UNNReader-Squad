"""Opening a recording must survive a flickering match start.

Three matches on the production server went completely unrecorded across two
hours: played, finished, and never written to disk. Closing a recording already
required N consecutive confirmations so one torn read could not cut a match
short — but OPENING threw its pre-open buffer away on a SINGLE non-active tick.
During a map load the reader briefly sees two worlds at once and matchState /
matchId can flicker, which reset the buffer faster than it could reach the
threshold. The recording then never opened at all, silently, for the whole
match.

These tests pin the symmetry: a transient bad tick is tolerated while waiting
to open, a sustained one still discards, and a genuinely live match always ends
up recorded.
"""
import json

from sqreader.recorder import _MATCH_END_CONFIRM_TICKS, _handle_snap


def _snap(state, match_id="match-a", *, map_name="Fallujah"):
    return {
        "timestamp": "2026-07-25T15:41:06+00:00",
        "gameState": {
            "matchState": state,
            "matchId": match_id,
            "mapName": map_name,
            "gameModeName": "RAAS",
            "layer": {"name": "Fallujah_RAAS_v1"},
        },
        "players": [],
    }


def _box():
    return {
        "current": None, "last_state": None, "inactive_ticks": 0,
        "pending_match_id": None, "pending_match_buffer": [],
        "last_tick": None, "tick_sequence_required": False,
        "missing_tick_warned": False,
    }


def _step(tmp_path, box, buf, snap):
    _handle_snap(snap=snap, raw_line=json.dumps(snap), state_box=box,
                 out_dir=tmp_path, server_id="test-server", min_ticks=0,
                 filename_buffer=buf)


def _recordings(tmp_path):
    return sorted(p.name for p in tmp_path.glob("*.sqrx"))


def test_one_flickering_tick_does_not_prevent_the_recording(tmp_path):
    """The regression: id unreadable for a single tick mid-buffer."""
    box, buf = _box(), []
    _step(tmp_path, box, buf, _snap("InProgress"))
    _step(tmp_path, box, buf, _snap("InProgress", match_id=None))   # torn read
    for _ in range(_MATCH_END_CONFIRM_TICKS):
        _step(tmp_path, box, buf, _snap("InProgress"))
    assert box["current"] is not None, "match went unrecorded after one bad tick"
    assert _recordings(tmp_path), "no .sqrx was created"


def test_alternating_flicker_still_opens(tmp_path):
    """Worst case: every other tick is unreadable during the map load.

    Under the old code the buffer was wiped on each bad tick and could never
    reach the threshold, so the whole match was lost.
    """
    box, buf = _box(), []
    for _ in range(8):
        _step(tmp_path, box, buf, _snap("InProgress"))
        _step(tmp_path, box, buf, _snap("InProgress", match_id=None))
    for _ in range(_MATCH_END_CONFIRM_TICKS):
        _step(tmp_path, box, buf, _snap("InProgress"))
    assert box["current"] is not None
    assert _recordings(tmp_path)


def test_sustained_inactivity_still_discards_the_buffer(tmp_path):
    """Tolerance must not become 'never give up' — a match that really is not
    starting should leave nothing buffered."""
    box, buf = _box(), []
    _step(tmp_path, box, buf, _snap("InProgress"))
    for _ in range(_MATCH_END_CONFIRM_TICKS + 2):
        _step(tmp_path, box, buf, _snap("WaitingPostMatch"))
    assert box["current"] is None
    assert buf == [], "stale pre-open buffer survived confirmed inactivity"
    assert not _recordings(tmp_path)


def test_buffer_from_an_abandoned_match_does_not_leak_into_the_next(tmp_path):
    """A buffered match that never opened must not contribute ticks to the
    match that follows it."""
    box, buf = _box(), []
    _step(tmp_path, box, buf, _snap("InProgress", match_id="old"))
    for _ in range(_MATCH_END_CONFIRM_TICKS + 2):
        _step(tmp_path, box, buf, _snap("WaitingPostMatch", match_id=None))
    for _ in range(_MATCH_END_CONFIRM_TICKS):
        _step(tmp_path, box, buf, _snap("InProgress", match_id="new"))
    assert box["current"] is not None
    assert box["current"].match_id == "new"


def test_a_clean_match_start_still_opens_promptly(tmp_path):
    """No flicker: behaviour must be unchanged."""
    box, buf = _box(), []
    for _ in range(_MATCH_END_CONFIRM_TICKS):
        _step(tmp_path, box, buf, _snap("InProgress"))
    assert box["current"] is not None
    assert len(_recordings(tmp_path)) == 1


def test_stall_watchdog_warns_when_a_live_match_never_records(tmp_path, caplog):
    """The silent failure needs a voice: hours of a live match with nothing
    recording must show up in the log."""
    import logging

    from sqreader.recorder import _OPEN_STALL_WARN_TICKS

    box, buf = _box(), []
    caplog.set_level(logging.WARNING)
    # mapName missing keeps it buffering; id absent keeps it from opening.
    for _ in range(_OPEN_STALL_WARN_TICKS + 2):
        _step(tmp_path, box, buf, _snap("InProgress", map_name=None))
        _step(tmp_path, box, buf, _snap("InProgress", match_id=None,
                                        map_name=None))
    assert any("no recording" in r.message or "stuck" in r.message
               for r in caplog.records), "stall was never reported"


# ------------------------------------------------- two-tier tick gaps --------

def _snap_t(state, tick, match_id="match-a"):
    s = _snap(state, match_id)
    s["tick"] = tick
    return s


def test_two_tier_tick_gaps_still_open_a_recording(tmp_path):
    """THE production failure.

    Two-tier recording builds full frames in a separate process and the reader
    takes only the worker's LATEST frame, dropping ones it did not collect in
    time. So the tick ids the recorder sees skip — routinely, under load. That
    used to wipe the pre-open buffer on every gap, so the buffer never reached
    the threshold and the match ran to completion unrecorded.
    """
    box, buf = _box(), []
    for tick in range(1, 20, 2):            # 1,3,5,... every frame is a gap
        _step(tmp_path, box, buf, _snap_t("InProgress", tick))
    assert box["current"] is not None, "gapped ticks left the match unrecorded"
    assert _recordings(tmp_path)


def test_gaps_still_do_not_close_a_recording_early(tmp_path):
    """The guard that gaps DO protect must survive: a close needs consecutive
    confirmations, so gapped end-state frames must not finalize."""
    box, buf = _box(), []
    for tick in range(1, 6):
        _step(tmp_path, box, buf, _snap_t("InProgress", tick))
    current = box["current"]
    assert current is not None
    # Non-consecutive end-state frames: each breaks the sequence, so the
    # confirmation can never accumulate.
    for tick in (10, 20, 30, 40):
        _step(tmp_path, box, buf, _snap_t("WaitingPostMatch", tick))
    assert box["current"] is current, "gapped frames closed the recording early"
