"""What doctor may conclude from the ODK stats-collector fields.

The old rule was "some player must be carrying a value, or the offsets have
drifted". It is wrong in both directions, and the wrong direction that costs
time is the loud one: these counters are event-gated, so a server in warmup —
or a whole round where nobody destroyed a FOB — reported drift and sent
somebody re-deriving offsets that were fine all along.

Confirmed against a live Squad 10.5.3 server with a player online building a
FOB: fobsBuilt 1, suppliesDelivered 3000, defenses 32. The offsets resolve.
"""
from __future__ import annotations

from sqreader.cli import COLLECTOR_FIELD_SOURCE, _collector_field_verdicts

FIELDS = tuple(COLLECTOR_FIELD_SOURCE)


def _player(name="Alice", **stats):
    return {"name": name, "eosId": f"eos-{name}", "stats": dict(stats)}


def _verdicts(players):
    return {f: v for f, (v, _d) in _collector_field_verdicts(players).items()}


def test_an_empty_server_is_unverifiable_not_broken():
    """Nothing to read a counter from is not the same as a counter that will
    not read."""
    assert set(_verdicts([]).values()) == {"skip"}


def test_a_verified_zero_is_a_real_answer():
    """The entry exists and we read it. That is exactly what the check is for,
    and the old rule accepted it only by accident."""
    v = _verdicts([_player(**{f: 0 for f in FIELDS})])
    assert set(v.values()) == {"ok"}


def test_the_detail_says_a_zero_was_verified():
    _f, detail = _collector_field_verdicts(
        [_player(captures=0, defenses=0)])["captures"]
    assert "verified zero" in detail


def test_a_field_nobody_has_scored_is_skipped():
    """An idle round produces no captures and no destroyed FOBs. Squad has not
    created the entry, so there is nothing to read and nothing to conclude."""
    assert _verdicts([_player(), _player("Bob")]) == {f: "skip" for f in FIELDS}


def test_real_activity_reads_as_ok_and_reports_the_value():
    """The live 10.5.3 diagnostic, as a fixture."""
    verdicts = _collector_field_verdicts(
        [_player(fobsBuilt=1, suppliesDelivered=3000, defenses=32, captures=0),
         _player("Bob")])
    assert verdicts["fobsBuilt"][0] == "ok"
    assert verdicts["suppliesDelivered"][0] == "ok"
    assert verdicts["defenses"][0] == "ok"
    assert "3000" in verdicts["suppliesDelivered"][1]
    # captures read a verified zero — present, therefore fine.
    assert verdicts["captures"][0] == "ok"
    # Nobody damaged a vehicle; that is not a fault.
    assert verdicts["vehicleDamage"][0] == "skip"


def test_one_field_reading_while_its_sibling_does_not_is_drift():
    """The only thing this check can honestly assert. Both fields come out of
    a single array entry, so if one arrived and the other did not, that
    struct's layout is wrong."""
    verdicts = _collector_field_verdicts([_player(fobsBuilt=4)])
    assert verdicts["fobsBuilt"][0] == "ok"
    assert verdicts["suppliesDelivered"][0] == "fail"
    assert "fobsBuilt" in verdicts["suppliesDelivered"][1]
    # A different collector's silence is still just silence.
    assert verdicts["captures"][0] == "skip"


def test_a_non_numeric_value_is_not_a_reading():
    assert _verdicts([_player(captures=None, defenses=None)]) == {
        f: "skip" for f in FIELDS}


def test_every_field_has_exactly_one_sibling():
    """The drift rule depends on it; a third field in a group would make
    `next()` pick an arbitrary one."""
    for collector in set(COLLECTOR_FIELD_SOURCE.values()):
        group = [f for f, c in COLLECTOR_FIELD_SOURCE.items() if c == collector]
        assert len(group) == 2, f"{collector} has {group}"
