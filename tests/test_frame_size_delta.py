"""The storage measurement, checked against arithmetic done another way.

The tool exists to answer "what do these keys cost a recording?", and the only
way that answer is worth anything is if the two sides differ by the keys and
nothing else. So the fixture is a real .sqrx written by `SqrxWriter`, and every
number the tool reports is recomputed here from the same lines by hand — raw
bytes in the recorder's own encoding, compressed bytes as one zstd frame per
line at the level the writer defaults to.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import zstandard as zstd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import frame_size_delta as fsd                     # noqa: E402
from sqreader.sqrx import SqrxWriter                            # noqa: E402

KEYS = ["teams[].commander", "players[].soldier.medical", "commandActions",
        "gameState.commanderRules"]

FULL_A = {
    "tick": 1,
    "gameState": {"worldTimeSec": 120.5,
                  "commanderRules": {"enabled": True, "votingTimeSec": 60}},
    "teams": [
        {"id": 1, "commander": {"enabled": True, "vote": {"inProgress": False}}},
        {"id": 2, "commander": None},
    ],
    "players": [
        {"eosId": "a" * 32, "soldier": {"health": 100,
                                        "medical": {"item": "BP_Dressing_C"}}},
        {"eosId": "b" * 32, "soldier": {"health": 42}},
    ],
    "commandActions": [{"id": "0x1", "class": "BP_CommandActor_UAV_MQ9_C"}],
}
FULL_B = {
    "tick": 2,
    "gameState": {"worldTimeSec": 121.5},
    "teams": [{"id": 1}, {"id": 2}],
    "players": [{"eosId": "a" * 32, "soldier": {"health": 100}}],
}
POS = {"t": "pos", "tick": 2, "fullTick": 1,
       "players": [{"id": "a" * 32, "x": 1.0, "y": 2.0, "z": 3.0}],
       "commandActions": [{"id": "0x1"}]}

LINES = [FULL_A, FULL_B, POS]


def _recording(tmp_path: Path) -> Path:
    out = tmp_path / "fixture.sqrx"
    with SqrxWriter(out, server_id="test-box") as w:
        for obj in LINES:
            w.write_line(json.dumps(obj, ensure_ascii=False))
    return out


def _by_hand(obj: dict) -> tuple[int, int, int, int]:
    """(raw with, raw without, zstd with, zstd without) for one line."""
    cctx = zstd.ZstdCompressor(level=fsd.RECORDER_ZSTD_LEVEL)
    stripped = json.loads(json.dumps(obj))
    fsd.strip_keys(stripped, KEYS)
    a = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
    b = (json.dumps(stripped, ensure_ascii=False) + "\n").encode("utf-8")
    return len(a), len(b), len(cctx.compress(a)), len(cctx.compress(b))


def test_the_level_is_the_recorders_own_not_a_choice():
    """A recorder that changes compression level must move this measurement
    with it, so the level is read from the writer rather than restated."""
    assert fsd.RECORDER_ZSTD_LEVEL == (
        inspect.signature(SqrxWriter.__init__).parameters["level"].default)


def test_full_frames_and_position_lines_are_measured_apart(tmp_path):
    rep = fsd.measure(_recording(tmp_path), KEYS)
    assert rep.full.lines == 2 and rep.pos.lines == 1 and rep.other.lines == 0

    for group, objs in ((rep.full, [FULL_A, FULL_B]), (rep.pos, [POS])):
        hand = [_by_hand(o) for o in objs]
        assert group.raw_with == sum(h[0] for h in hand)
        assert group.raw_without == sum(h[1] for h in hand)
        assert group.comp_with == sum(h[2] for h in hand)
        assert group.comp_without == sum(h[3] for h in hand)
        assert group.raw_delta == group.raw_with - group.raw_without
        assert group.comp_delta == group.comp_with - group.comp_without

    # The keys are really gone on the stripped side and really there on the
    # other: the deltas are positive wherever a key was carried.
    assert rep.full.raw_delta > 0 and rep.pos.raw_delta > 0
    assert rep.on_disk == Path(rep.path).stat().st_size


def test_a_frame_without_the_keys_costs_nothing(tmp_path):
    """FULL_B carries none of them — its two sides must be byte-identical,
    which is also what a recording made before the keys existed reports."""
    rep = fsd.measure(_recording(tmp_path), KEYS)
    a_raw, a_wo, _, _ = _by_hand(FULL_A)
    b_raw, b_wo, _, _ = _by_hand(FULL_B)
    assert b_raw == b_wo
    assert rep.full.raw_delta == a_raw - a_wo


def test_no_keys_strips_nothing(tmp_path):
    rep = fsd.measure(_recording(tmp_path), [])
    assert rep.full.raw_delta == 0 and rep.pos.raw_delta == 0
    assert rep.full.comp_delta == 0 and rep.pos.comp_delta == 0


def test_key_paths_descend_lists_and_tolerate_a_shape_they_do_not_fit():
    obj = {"teams": [{"id": 1, "commander": {"x": 1}}, {"id": 2}],
           "players": [{"soldier": {"medical": {"item": "x"}, "health": 3}},
                       {"soldier": None},
                       {}],
           "vehicles": [{"turrets": [{"weapons": [1], "seat": 0}]}],
           "commandActions": [1, 2],
           "gameState": {"commanderRules": {"enabled": True}, "worldTimeSec": 1}}
    fsd.strip_keys(obj, KEYS + ["vehicles[].turrets[].weapons",
                                # neither of these matches anything here
                                "teams[].commander.missing", "drones"])
    assert obj == {
        "teams": [{"id": 1}, {"id": 2}],
        "players": [{"soldier": {"health": 3}}, {"soldier": None}, {}],
        "vehicles": [{"turrets": [{"seat": 0}]}],
        "gameState": {"worldTimeSec": 1},
    }


def test_a_list_named_without_brackets_is_removed_whole():
    obj = {"drones": [{"id": "0x1"}], "keep": 1}
    fsd.strip_keys(obj, ["drones"])
    assert obj == {"keep": 1}


def test_render_names_the_level_and_the_keys(tmp_path):
    text = fsd.render_text(fsd.measure(_recording(tmp_path), KEYS))
    assert f"zstd level {fsd.RECORDER_ZSTD_LEVEL}" in text
    assert "teams[].commander" in text
    assert "full frames" in text and "position" in text


def test_cli_needs_at_least_one_key(tmp_path, capsys):
    assert fsd.main([str(_recording(tmp_path))]) == 2
    assert fsd.main([str(_recording(tmp_path)), "commandActions"]) == 0
    assert "commandActions" in capsys.readouterr().out
