"""Security lifecycle tests for per-match .sqrx metadata."""
import json

from sqreader.recorder import (
    _MATCH_END_CONFIRM_TICKS,
    _handle_snap,
    finalize_recording,
)


def _snap(state: str, match_id: str = "match-a", tick=None) -> dict:
    snap = {
        "timestamp": "2026-07-14T12:00:00+00:00",
        "gameState": {
            "matchState": state,
            "matchId": match_id,
            "mapName": "Fallujah",
            "gameModeName": "RAAS",
            "layer": {"name": "Fallujah_RAAS_v1"},
        },
        "players": [],
    }
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
    }


def _step(tmp_path, state_box, filename_buffer, snap):
    _handle_snap(
        snap=snap,
        raw_line=json.dumps(snap),
        state_box=state_box,
        out_dir=tmp_path,
        server_id="test-server",
        min_ticks=0,
        filename_buffer=filename_buffer,
    )


def test_recording_sidecar_is_active_until_writer_is_finalized(tmp_path):
    state_box = _state_box()
    filename_buffer = []
    active = _snap("InProgress")

    for _ in range(_MATCH_END_CONFIRM_TICKS):
        _step(tmp_path, state_box, filename_buffer, active)

    current = state_box["current"]
    assert current is not None
    sidecar = current.path.with_suffix(".meta.json")
    live_meta = json.loads(sidecar.read_text(encoding="utf-8"))
    assert live_meta["matchId"] == "match-a"
    assert live_meta["recordingState"] == "active"
    assert live_meta["inProgress"] is True

    # Neither one post-match frame nor ambiguous/unknown state proves an end.
    ended = _snap("WaitingPostMatch")
    _step(tmp_path, state_box, filename_buffer, ended)
    assert state_box["current"] is current

    missing_id = _snap("InProgress", match_id=None)
    _step(tmp_path, state_box, filename_buffer, missing_id)
    assert state_box["current"] is current

    _step(tmp_path, state_box, filename_buffer, _snap("FutureUnknownState"))
    assert state_box["current"] is current

    for _ in range(_MATCH_END_CONFIRM_TICKS):
        _step(tmp_path, state_box, filename_buffer, ended)

    assert state_box["current"] is None
    final_meta = json.loads(sidecar.read_text(encoding="utf-8"))
    assert final_meta["recordingState"] == "finalized"
    assert final_meta["inProgress"] is False
    assert final_meta["ticks"] == _MATCH_END_CONFIRM_TICKS


def test_one_spurious_match_id_does_not_split_or_expose_recording(tmp_path):
    state_box = _state_box()
    filename_buffer = []
    active_a = _snap("InProgress", "match-a")
    for _ in range(_MATCH_END_CONFIRM_TICKS):
        _step(tmp_path, state_box, filename_buffer, active_a)

    current = state_box["current"]
    assert current is not None
    _step(tmp_path, state_box, filename_buffer,
          _snap("InProgress", "match-b"))
    assert state_box["current"] is current
    assert len(list(tmp_path.glob("*.sqrx"))) == 1

    # A malformed frame breaks the B candidate; returning A continues the
    # original writer and never creates an orphan B file.
    _step(tmp_path, state_box, filename_buffer,
          _snap("InProgress", match_id=None))
    _step(tmp_path, state_box, filename_buffer, active_a)
    assert state_box["current"] is current
    assert current.tick_count == _MATCH_END_CONFIRM_TICKS + 1
    assert len(list(tmp_path.glob("*.sqrx"))) == 1

    finalize_recording(current, reason="test cleanup")


def test_uncertain_shutdown_close_stays_unverified(tmp_path):
    state_box = _state_box()
    filename_buffer = []
    active = _snap("InProgress")
    for _ in range(_MATCH_END_CONFIRM_TICKS):
        _step(tmp_path, state_box, filename_buffer, active)

    current = state_box["current"]
    sidecar = current.path.with_suffix(".meta.json")
    finalize_recording(current, reason="serve shutdown")

    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    assert meta["recordingState"] == "unverified"
    assert meta["inProgress"] is True


def test_dropped_sse_frames_cannot_fake_consecutive_match_ids(tmp_path):
    state_box = _state_box()
    filename_buffer = []
    for tick in (1, 2, 3):
        _step(tmp_path, state_box, filename_buffer,
              _snap("InProgress", "match-a", tick=tick))

    current = state_box["current"]
    assert current is not None

    # Model a slow SSE subscriber receiving B at 5/7/9 while the A reset
    # frames at 4/6/8 were dropped by its bounded queue. Tick gaps must reset
    # the B candidate each time instead of falsely confirming a transition.
    for tick in (5, 7, 9):
        _step(tmp_path, state_box, filename_buffer,
              _snap("InProgress", "match-b", tick=tick))
    assert state_box["current"] is current
    assert len(list(tmp_path.glob("*.sqrx"))) == 1

    _step(tmp_path, state_box, filename_buffer,
          _snap("InProgress", "match-a", tick=10))
    assert state_box["current"] is current
    assert current.tick_count == 4

    finalize_recording(current, reason="test cleanup")


# ---------------------------------------------------------------------------
# The end of the match has to be IN the file
#
# A finished recording used to stop on the last frame of play. The frames that
# proved the match had ended were counted and dropped, so nothing in the
# archive said the match was over — the sidecar asserted it, the stream could
# not show it. Replaying such a file reproduces `unverified`, no winner and no
# rating, while the live writer watching the same frames recorded all three.
# ---------------------------------------------------------------------------

def _frames(path) -> list[dict]:
    from sqreader.sqrx import SqrxReader
    with SqrxReader(path) as r:
        return [json.loads(line) for line in r]


def test_a_finished_recording_carries_the_frames_that_ended_the_match(tmp_path):
    state_box = _state_box()
    filename_buffer = []
    for tick in range(1, 5):
        _step(tmp_path, state_box, filename_buffer,
              _snap("InProgress", tick=tick))
    current = state_box["current"]

    for tick in range(5, 5 + _MATCH_END_CONFIRM_TICKS):
        _step(tmp_path, state_box, filename_buffer,
              _snap("WaitingPostMatch", tick=tick))
    assert state_box["current"] is None

    states = [(f.get("gameState") or {}).get("matchState")
              for f in _frames(current.path)]
    assert states[-_MATCH_END_CONFIRM_TICKS:] == \
        ["WaitingPostMatch"] * _MATCH_END_CONFIRM_TICKS
    # ... and in the order they arrived: the first one is where the terminal
    # ticket counts come from.
    assert states.count("InProgress") == 4


def test_the_end_frames_do_not_count_as_play(tmp_path):
    """`ticks`, `durationSec` and `peakPlayers` describe the match that was
    played — and the stats row measures its duration the same way."""
    state_box = _state_box()
    filename_buffer = []
    for tick in range(1, 5):
        _step(tmp_path, state_box, filename_buffer,
              _snap("InProgress", tick=tick))
    current = state_box["current"]
    sidecar = current.path.with_suffix(".meta.json")
    for tick in range(5, 5 + _MATCH_END_CONFIRM_TICKS):
        _step(tmp_path, state_box, filename_buffer,
              _snap("WaitingPostMatch", tick=tick))

    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    assert meta["ticks"] == 4
    assert meta["endFrames"] == _MATCH_END_CONFIRM_TICKS
    assert meta["totalFrames"] == 4 + _MATCH_END_CONFIRM_TICKS
    assert meta["recordingState"] == "finalized"
    # The sidecar still names the match, not the teardown after it.
    assert meta["mapName"] == "Fallujah"
    assert meta["matchId"] == "match-a"


def test_rescanning_a_recording_counts_the_end_frames_the_same_way(tmp_path):
    """The self-heal path and the finalize path must not disagree about what a
    frame is, or a lost sidecar would rewrite the match's own duration."""
    from sqreader.recorder import extract_metadata

    state_box = _state_box()
    filename_buffer = []
    for tick in range(1, 5):
        _step(tmp_path, state_box, filename_buffer,
              _snap("InProgress", tick=tick))
    current = state_box["current"]
    sidecar = current.path.with_suffix(".meta.json")
    for tick in range(5, 5 + _MATCH_END_CONFIRM_TICKS):
        _step(tmp_path, state_box, filename_buffer,
              _snap("WaitingPostMatch", tick=tick))

    written = json.loads(sidecar.read_text(encoding="utf-8"))
    rescanned = extract_metadata(current.path)
    for field in ("ticks", "endFrames", "totalFrames", "durationSec",
                  "peakPlayers", "mapName"):
        assert rescanned[field] == written[field], field


def test_play_resuming_discards_the_frames_that_looked_like_an_ending(tmp_path):
    """Two post-match ticks and then play again is not an ending, and the file
    must not suggest it was."""
    state_box = _state_box()
    filename_buffer = []
    for tick in range(1, 5):
        _step(tmp_path, state_box, filename_buffer,
              _snap("InProgress", tick=tick))
    current = state_box["current"]
    for tick in (5, 6):
        _step(tmp_path, state_box, filename_buffer,
              _snap("WaitingPostMatch", tick=tick))
    _step(tmp_path, state_box, filename_buffer, _snap("InProgress", tick=7))
    assert state_box["current"] is current
    assert current.end_frames == []

    finalize_recording(current, reason="test cleanup")
    states = [(f.get("gameState") or {}).get("matchState")
              for f in _frames(current.path)]
    assert "WaitingPostMatch" not in states


def test_a_shutdown_writes_only_what_it_saw(tmp_path):
    """An uncertain close flushes however many confirming frames arrived — one
    is not three, and a replay must be able to tell."""
    state_box = _state_box()
    filename_buffer = []
    for tick in range(1, 5):
        _step(tmp_path, state_box, filename_buffer,
              _snap("InProgress", tick=tick))
    current = state_box["current"]
    _step(tmp_path, state_box, filename_buffer,
          _snap("WaitingPostMatch", tick=5))
    assert state_box["current"] is current      # not confirmed yet

    finalize_recording(current, reason="serve shutdown")
    meta = json.loads(current.path.with_suffix(".meta.json").read_text(
        encoding="utf-8"))
    assert meta["recordingState"] == "unverified"
    assert meta["endFrames"] == 1
    states = [(f.get("gameState") or {}).get("matchState")
              for f in _frames(current.path)]
    assert states.count("WaitingPostMatch") == 1
