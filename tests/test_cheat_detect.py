"""Anti-cheat detectors, driven with synthetic snapshots.

Every test here is a claim about when the detector must NOT fire as much as when
it must: a false accusation is the expensive failure, not a missed one.
"""
from __future__ import annotations

import math

from sqreader.plugins import PluginManager
from sqreader.plugins.cheat_detect import CheatDetect, _angle_diff_deg

# 2 s per tick — the production rate these thresholds are scaled to.
TICK_SEC = 2.0
SPRINT_MPS = 7.8


def _player(eos="eos-1", name="Alice", *, x=0.0, y=0.0, addr="0xA",
            health=100.0, attached=False, yaw=None, weapon=None, mags=None,
            stale=False, stamina=None, stamina_max=100.0):
    sol = {
        "addr": addr, "health": health, "attached": attached,
        "position": {"x": x, "y": y, "z": 0.0},
    }
    if stale:
        sol["stale"] = True
    if yaw is not None:
        sol["yaw"] = yaw
    if stamina is not None:
        sol["stamina"] = stamina
        if stamina_max is not None:
            sol["staminaMax"] = stamina_max
    if weapon is not None:
        w = {"className": weapon}
        if mags is not None:
            w["magazines"] = list(mags)
        sol["weapon"] = w
    return {"eosId": eos, "name": name, "teamId": 1, "soldier": sol}


def _snap(players, *, tick=1, elapsed=0.0, events=(), vehicles=(),
          state="InProgress", cache_resets=0, deployables=(), projectiles=()):
    return {
        "tick": tick,
        "gameState": {"matchState": state, "matchId": "m1",
                      "elapsedSec": elapsed},
        "players": list(players),
        "vehicles": list(vehicles),
        "damageEvents": list(events),
        "deployables": list(deployables),
        "projectiles": list(projectiles),
        "counts": {"cacheResets": cache_resets},
    }


class _Sink:
    def __init__(self):
        self.rows = []

    def __call__(self, row):
        self.rows.append(row)

    def types(self):
        return [r["alert_type"] for r in self.rows]


def _run(cfg=None):
    sink = _Sink()
    mgr = PluginManager(
        {"cheat_detect": {"enabled": True, "config": cfg or {}}},
        server_id="t", emit_alert=sink)
    return mgr, sink


def _walk(mgr, *, speed_mps, ticks, eos="eos-1", start_tick=1, addr="0xA",
          tick_sec=TICK_SEC):
    """Move a player in a straight line at a fixed speed for N ticks.

    `tick_sec` is settable because the detector must not care: the same wall
    clock has to mean the same thing whether the reader samples every 2 s or
    every third of a second.
    """
    step_cm = speed_mps * tick_sec * 100.0
    for i in range(ticks):
        mgr.run_tick(
            _snap([_player(eos=eos, x=step_cm * i, addr=addr)],
                  tick=start_tick + i, elapsed=tick_sec * i),
            tick=start_tick + i, now=1000.0 + tick_sec * i)


# --------------------------------------------------------------------------
# speedhack
# --------------------------------------------------------------------------

def test_sprinting_never_flags():
    mgr, sink = _run()
    _walk(mgr, speed_mps=SPRINT_MPS, ticks=20)
    assert sink.rows == []


def test_speedhack_needs_the_full_window():
    """Sixteen seconds of it. One sample short must stay quiet."""
    secs = CheatDetect.DEFAULT_CONFIG["speed_sustained_seconds"]
    need = int(secs / TICK_SEC)
    mgr, sink = _run()
    # The first tick only anchors the position, so N+1 samples give N speeds.
    _walk(mgr, speed_mps=30.0, ticks=need)     # -> need-1 over-speed samples
    assert sink.rows == [], "fired before the window was complete"

    mgr, sink = _run()
    _walk(mgr, speed_mps=30.0, ticks=need + 1)
    assert sink.types() == ["speedhack"]


def test_the_window_is_the_same_sixteen_seconds_at_any_tick_rate():
    """The thresholds arrived as TICK counts with a note to re-scale them by
    hand after changing --hz. That note was correctly written and duly
    ignored: the first deployment to switch this on ran six times faster, where
    the inherited count meant 2.7 seconds instead of 16 - and a parachute
    landing lasts about that long. Time is measured, not counted."""
    secs = CheatDetect.DEFAULT_CONFIG["speed_sustained_seconds"]

    for tick_sec in (2.0, 1.0, 1.0 / 3.0):
        n = int(round(secs / tick_sec))
        # Just under the window: silent, however many samples that took.
        mgr, sink = _run()
        _walk(mgr, speed_mps=30.0, ticks=n - 1, tick_sec=tick_sec)
        assert sink.rows == [], f"fired early at a {tick_sec:.2f}s tick"
        # Just over it: fires, however many samples that took.
        mgr, sink = _run()
        _walk(mgr, speed_mps=30.0, ticks=n + 2, tick_sec=tick_sec)
        assert sink.types() == ["speedhack"], \
            f"never fired at a {tick_sec:.2f}s tick"


def test_a_burst_shorter_than_the_window_stays_quiet_when_sampled_fast():
    """The exact false positive the rescale exists to prevent: a few seconds
    over the limit - a parachute landing, a bail from a moving truck - read by
    a fast sampler. Under a tick count it was nine samples and fired."""
    mgr, sink = _run()
    _walk(mgr, speed_mps=30.0, ticks=12, tick_sec=1.0 / 3.0)   # ~4 s of it
    assert sink.rows == [], "a four-second burst was called a speedhack"


def test_speedhack_reports_the_speed_it_saw():
    mgr, sink = _run()
    _walk(mgr, speed_mps=30.0, ticks=12)
    d = sink.rows[0]["details"]
    assert abs(d["speedMps"] - 30.0) < 0.5
    assert d["limitMps"] == 18.0
    assert d["sustainedSeconds"] >= 16.0, \
        "the evidence has to say how long it lasted, in a unit that travels"


def test_a_single_teleport_spike_does_not_flag():
    """One bad sample is a read glitch, not a cheat. The streak is the point."""
    mgr, sink = _run()
    mgr.run_tick(_snap([_player(x=0.0)], tick=1, elapsed=0.0), tick=1)
    mgr.run_tick(_snap([_player(x=500_000.0)], tick=2, elapsed=TICK_SEC), tick=2)
    mgr.run_tick(_snap([_player(x=500_100.0)], tick=3, elapsed=TICK_SEC * 2), tick=3)
    assert sink.rows == []


def test_respawn_breaks_the_streak():
    """A new pawn means the two positions are not the same body. The soldier
    addr changing is how we know."""
    mgr, sink = _run()
    _walk(mgr, speed_mps=30.0, ticks=6, addr="0xA")
    _walk(mgr, speed_mps=30.0, ticks=6, addr="0xB", start_tick=7)
    assert sink.rows == [], "streak survived a respawn"


def test_a_vehicle_occupant_is_never_flagged():
    """A passenger's body position is replicated from the vehicle and lags it —
    a helicopter passenger routinely 'moves' far past any foot threshold."""
    mgr, sink = _run()
    veh = [{"seats": [{"occupantEosId": "eos-1"}]}]
    for i in range(20):
        mgr.run_tick(
            _snap([_player(x=60_000.0 * i)], tick=i, elapsed=TICK_SEC * i,
                  vehicles=veh),
            tick=i)
    assert sink.rows == []


def test_an_attached_soldier_is_never_flagged():
    mgr, sink = _run()
    for i in range(20):
        mgr.run_tick(
            _snap([_player(x=60_000.0 * i, attached=True)],
                  tick=i, elapsed=TICK_SEC * i),
            tick=i)
    assert sink.rows == []


def test_a_cache_reset_breaks_the_streak():
    """Positions can legitimately jump on the tick a cache reset lands."""
    mgr, sink = _run()
    step = 30.0 * TICK_SEC * 100.0
    for i in range(20):
        mgr.run_tick(
            _snap([_player(x=step * i)], tick=i, elapsed=TICK_SEC * i,
                  cache_resets=i),          # a reset every tick
            tick=i)
    assert sink.rows == []


def test_a_stale_soldier_is_not_measured():
    mgr, sink = _run()
    step = 30.0 * TICK_SEC * 100.0
    for i in range(20):
        mgr.run_tick(
            _snap([_player(x=step * i, stale=True)],
                  tick=i, elapsed=TICK_SEC * i),
            tick=i)
    assert sink.rows == []


def test_cooldown_suppresses_a_repeat():
    mgr, sink = _run()
    _walk(mgr, speed_mps=30.0, ticks=30)   # long enough to fire many times over
    assert sink.types().count("speedhack") == 1


# --------------------------------------------------------------------------
# remote melee
# --------------------------------------------------------------------------

def _melee(attacker="Alice", victim="Bob", weapon="BP_Knife_C", ts=100.0):
    return {"attacker": attacker, "victim": victim, "causerWeapon": weapon,
            "killed": 1, "wounded": 0, "ts": ts, "attackerEosId": "eos-1"}


def test_a_knife_kill_across_the_map_flags():
    mgr, sink = _run()
    mgr.run_tick(_snap(
        [_player(eos="eos-1", name="Alice", x=0.0),
         _player(eos="eos-2", name="Bob", x=20_000.0, addr="0xB")],   # 200 m
        events=[_melee()]), tick=1)
    assert sink.types() == ["remote_melee"]
    assert sink.rows[0]["details"]["distanceM"] == 200.0


def test_a_normal_knife_kill_does_not_flag():
    mgr, sink = _run()
    mgr.run_tick(_snap(
        [_player(eos="eos-1", name="Alice", x=0.0),
         _player(eos="eos-2", name="Bob", x=200.0, addr="0xB")],      # 2 m
        events=[_melee()]), tick=1)
    assert sink.rows == []


def test_a_knife_kill_inside_the_drift_budget_does_not_flag():
    """25 m is the position drift a sprinting player can accumulate inside one
    2 s tick. Anything under it is not evidence."""
    mgr, sink = _run()
    mgr.run_tick(_snap(
        [_player(eos="eos-1", name="Alice", x=0.0),
         _player(eos="eos-2", name="Bob", x=2_400.0, addr="0xB")],    # 24 m
        events=[_melee()]), tick=1)
    assert sink.rows == []


def test_a_rifle_kill_at_range_is_not_a_remote_melee():
    mgr, sink = _run()
    mgr.run_tick(_snap(
        [_player(eos="eos-1", name="Alice", x=0.0),
         _player(eos="eos-2", name="Bob", x=30_000.0, addr="0xB")],
        events=[_melee(weapon="BP_M4A1_C")]), tick=1)
    assert sink.rows == []


def test_an_unplaceable_victim_is_not_accused():
    """No position for one side means no distance. No distance, no accusation."""
    mgr, sink = _run()
    bob = _player(eos="eos-2", name="Bob", x=30_000.0, addr="0xB")
    bob["soldier"] = None                     # dead, pawn already gone
    mgr.run_tick(_snap([_player(eos="eos-1", name="Alice"), bob],
                       events=[_melee()]), tick=1)
    assert sink.rows == []


def test_an_event_whose_id_matches_nobody_is_resolved_by_name():
    """The kill feed's ids come from the server LOG and the snapshot's from
    memory. On a real four-match archive those were different namespaces
    entirely — 32-hex EOS ids against UUIDs — and the id branch short-circuited
    the name lookup, so every damage-event detector here resolved 0 of 789
    events and sat silently inert."""
    mgr, sink = _run()
    ev = _melee()
    ev["attackerEosId"] = "00025f4fea4f4aa1a920a4cfb5163f10"   # not in memory
    mgr.run_tick(_snap(
        [_player(eos="b31cc43e-44f7-47c9-9057-d283e7eaad0b", name="Alice",
                 x=0.0),
         _player(eos="eos-2", name="Bob", x=20_000.0, addr="0xB")],
        events=[ev]), tick=1)
    assert sink.types() == ["remote_melee"]
    assert sink.rows[0]["eos_id"] == "b31cc43e-44f7-47c9-9057-d283e7eaad0b"


def test_a_causer_naming_a_projectile_is_not_the_gun_being_held():
    """Explosives name their projectile and vehicle weapons name the vehicle.
    Neither is the gun whose magazines we are reading, so neither may be
    checked against them."""
    mgr, sink = _run()
    for i, ts in enumerate([0.0, 10.0, 20.0, 30.0, 40.0]):
        mgr.run_tick(_snap(
            [_player(eos="eos-1", name="Alice",
                     weapon="BP_AK74GP25_EXPS_UGL_HE_10Rnds_C", mags=[1]),
             _player(eos="eos-2", name="Bob", x=5000.0, addr="0xB")],
            tick=i + 1, events=[_hit(ts, "BP_40MM_VOG_Proj2_C")]),
            tick=i + 1, now=ts)
    assert sink.rows == []


def test_the_same_event_seen_twice_only_counts_once():
    mgr, sink = _run()
    players = [_player(eos="eos-1", name="Alice", x=0.0),
               _player(eos="eos-2", name="Bob", x=20_000.0, addr="0xB")]
    mgr.run_tick(_snap(players, tick=1, events=[_melee()]), tick=1)
    mgr.run_tick(_snap(players, tick=2, events=[_melee()]), tick=2)  # redelivered
    assert len(sink.rows) == 1


# --------------------------------------------------------------------------
# infinite ammo
# --------------------------------------------------------------------------

def _hit(ts, weapon="BP_M4A1_C"):
    return {"attacker": "Alice", "victim": "Bob", "causerWeapon": weapon,
            "killed": 0, "wounded": 1, "ts": ts, "attackerEosId": "eos-1"}


def _fire(mgr, *, mags, times, weapon="BP_M4A1_C"):
    for i, ts in enumerate(times):
        mgr.run_tick(_snap(
            [_player(eos="eos-1", name="Alice", weapon=weapon,
                     mags=mags[i] if isinstance(mags[0], list) else mags),
             _player(eos="eos-2", name="Bob", x=5000.0, addr="0xB")],
            tick=i + 1, events=[_hit(ts, weapon)]),
            tick=i + 1, now=ts)


def test_ammo_that_never_drops_under_sustained_fire_flags():
    """Sustained fire: 10 s apart, so each event lands inside the 15 s staleness
    window and the suspicion accumulates past the 30 s minimum."""
    mgr, sink = _run()
    _fire(mgr, mags=[30] * 5, times=[0.0, 10.0, 20.0, 30.0, 40.0])
    assert sink.types() == ["infinite_ammo"]
    d = sink.rows[0]["details"]
    assert d["damageEvents"] >= 3
    assert d["windowSec"] >= 30.0


def test_fire_slower_than_the_staleness_window_never_accumulates():
    """A documented blind spot, not an accident.

    Events further apart than `inf_ammo_stale_seconds` (15 s) reset the window
    every time, so a cheater who fires only every 20 s is never caught. That is
    the deliberate price of the staleness gate, which exists because "fired a
    burst, then resupplied in main" is indistinguishable from infinite ammo
    until you notice they stopped shooting. Tighten at your peril: this is the
    trade that keeps the false-positive rate near zero.
    """
    mgr, sink = _run()
    _fire(mgr, mags=[30] * 5, times=[0.0, 20.0, 40.0, 60.0, 80.0])
    assert sink.rows == []


def test_reloading_resets_the_suspicion():
    mgr, sink = _run()
    _fire(mgr, mags=[[30, 30, 30], [30, 30, 20], [30, 30, 10], [30, 20, 10]],
          times=[0.0, 10.0, 20.0, 30.0])
    assert sink.rows == []


def test_three_shots_inside_the_minimum_window_do_not_flag():
    """The window exists so a reload has room to land before we call it."""
    mgr, sink = _run()
    _fire(mgr, mags=[30, 30, 30], times=[0.0, 2.0, 4.0])   # 4 s < 30 s
    assert sink.rows == []


def test_a_stale_burst_then_a_pause_does_not_flag():
    """Fire a burst, resupply in main, come back. Looks identical to a cheater
    until you notice the gap — which is what the staleness window is for."""
    mgr, sink = _run()
    _fire(mgr, mags=[30, 30, 30], times=[0.0, 5.0, 100.0])
    assert sink.rows == []


def test_a_weapon_swap_is_not_evidence():
    """The ammo we can see belongs to the gun they hold NOW, not the one the
    event was about."""
    mgr, sink = _run()
    for i, ts in enumerate([0.0, 20.0, 40.0]):
        mgr.run_tick(_snap(
            [_player(eos="eos-1", name="Alice", weapon="BP_Pistol_C", mags=[8]),
             _player(eos="eos-2", name="Bob", x=5000.0, addr="0xB")],
            tick=i + 1, events=[_hit(ts, "BP_M4A1_C")]),   # rifle event
            tick=i + 1, now=ts)
    assert sink.rows == []


# --------------------------------------------------------------------------
# magic bullet (experimental, off by default)
# --------------------------------------------------------------------------

def test_magic_bullet_is_off_by_default():
    assert CheatDetect.DEFAULT_CONFIG["detect_magic_bullet"] is False


def test_magic_bullet_fires_only_when_explicitly_enabled():
    cfg = {"detect_magic_bullet": True, "magic_bullet_consecutive": 2,
           "detect_speedhack": False}
    mgr, sink = _run(cfg)
    for i in range(3):
        ts = 100.0 + i
        mgr.run_tick(_snap(
            # Alice faces +X (yaw 0); Bob is behind her at -X.
            [_player(eos="eos-1", name="Alice", x=0.0, yaw=0.0),
             _player(eos="eos-2", name="Bob", x=-10_000.0, addr="0xB")],
            tick=i + 1, events=[_hit(ts)]),
            tick=i + 1, now=ts)
    assert sink.types() == ["magic_bullet"]
    assert sink.rows[0]["confidence"] < 0.5      # yaw is tick-sampled — say so
    assert "caveat" in sink.rows[0]["details"]


# --------------------------------------------------------------------------
# angle maths
# --------------------------------------------------------------------------

def test_angle_zero_when_facing_the_target():
    assert _angle_diff_deg(0.0, 1.0, 0.0) == 0.0


def test_angle_180_when_facing_away():
    assert _angle_diff_deg(0.0, -1.0, 0.0) == 180.0


def test_angle_90_when_perpendicular():
    assert math.isclose(_angle_diff_deg(0.0, 0.0, 1.0), 90.0)


def test_angle_wraps_the_short_way_around():
    # Facing 350 degrees, target at 10 degrees: 20 apart, not 340.
    assert math.isclose(_angle_diff_deg(350.0, math.cos(math.radians(10)),
                                        math.sin(math.radians(10))), 20.0,
                        abs_tol=1e-6)


# --------------------------------------------------------------------------
# stamina hack (experimental, off by default)
# --------------------------------------------------------------------------

STAMINA_ON = {"detect_stamina_hack": True, "detect_speedhack": False}


def _sprint(mgr, *, ticks, stamina, speed_mps=6.5, tick_sec=TICK_SEC,
            eos="eos-1", addr="0xA", start_tick=1, vehicles=()):
    """Run at `speed_mps` with a stamina bar given per tick (value or list)."""
    step_cm = speed_mps * tick_sec * 100.0
    for i in range(ticks):
        s = stamina[i] if isinstance(stamina, list) else stamina
        mgr.run_tick(
            _snap([_player(eos=eos, x=step_cm * i, addr=addr, stamina=s)],
                  tick=start_tick + i, elapsed=tick_sec * i,
                  vehicles=vehicles),
            tick=start_tick + i, now=1000.0 + tick_sec * i)


def test_stamina_hack_is_off_by_default():
    assert CheatDetect.DEFAULT_CONFIG["detect_stamina_hack"] is False


def test_sprinting_on_a_full_bar_flags():
    mgr, sink = _run(STAMINA_ON)
    _sprint(mgr, ticks=14, stamina=100.0)         # 26 s of it
    assert sink.types() == ["stamina_hack"]
    d = sink.rows[0]["details"]
    assert d["staminaFraction"] == 1.0
    assert d["sustainedSeconds"] >= 20.0
    assert d["speedMps"] >= 5.5


def test_sprinting_that_drains_the_bar_never_flags():
    """The ordinary case: a player sprints and the bar goes down. Neither half
    of the signal is suspicious on its own — only the pair is."""
    mgr, sink = _run(STAMINA_ON)
    bar = [max(0.0, 100.0 - 8.0 * i) for i in range(20)]
    _sprint(mgr, ticks=20, stamina=bar)
    assert sink.rows == []


def test_walking_on_a_full_bar_never_flags():
    """Below sprint speed the bar is SUPPOSED to stay full."""
    mgr, sink = _run(STAMINA_ON)
    _sprint(mgr, ticks=20, stamina=100.0, speed_mps=3.0)
    assert sink.rows == []


def test_the_stamina_window_is_the_same_twenty_seconds_at_any_tick_rate():
    secs = CheatDetect.DEFAULT_CONFIG["stamina_sustained_seconds"]
    for tick_sec in (2.0, 1.0 / 3.0):
        n = int(round(secs / tick_sec))
        mgr, sink = _run(STAMINA_ON)
        _sprint(mgr, ticks=n - 1, stamina=100.0, tick_sec=tick_sec)
        assert sink.rows == [], f"fired early at a {tick_sec:.2f}s tick"
        mgr, sink = _run(STAMINA_ON)
        _sprint(mgr, ticks=n + 2, stamina=100.0, tick_sec=tick_sec)
        assert sink.types() == ["stamina_hack"], \
            f"never fired at a {tick_sec:.2f}s tick"


def test_an_unreadable_stamina_bar_is_not_assumed_full():
    mgr, sink = _run(STAMINA_ON)
    step = 6.5 * TICK_SEC * 100.0
    for i in range(20):
        mgr.run_tick(_snap([_player(x=step * i)],      # no stamina field
                           tick=i + 1, elapsed=TICK_SEC * i),
                     tick=i + 1, now=1000.0 + TICK_SEC * i)
    assert sink.rows == []


def test_a_zero_stamina_max_is_not_divided_by():
    mgr, sink = _run(STAMINA_ON)
    step = 6.5 * TICK_SEC * 100.0
    for i in range(20):
        mgr.run_tick(
            _snap([_player(x=step * i, stamina=0.0, stamina_max=0.0)],
                  tick=i + 1, elapsed=TICK_SEC * i),
            tick=i + 1, now=1000.0 + TICK_SEC * i)
    assert sink.rows == []


def test_moving_faster_than_a_sprint_is_not_a_stamina_question():
    """A stamina cheat lets you sprint indefinitely; it does not make you
    faster than a sprint. Above the cap the explanation is a vehicle seat we
    failed to resolve, a parachute, or a position leak — speedhack's business,
    with speedhack's tuning history behind it. On a real archive this
    detector's only alert was the same player, in the same moment, that
    speedhack was already reporting."""
    mgr, sink = _run(STAMINA_ON)
    _sprint(mgr, ticks=20, stamina=100.0, speed_mps=18.4)
    assert sink.rows == []


def test_a_vehicle_occupant_never_flags_stamina():
    mgr, sink = _run(STAMINA_ON)
    _sprint(mgr, ticks=20, stamina=100.0,
            vehicles=[{"seats": [{"occupantEosId": "eos-1"}]}])
    assert sink.rows == []


def test_respawn_breaks_the_stamina_streak():
    mgr, sink = _run(STAMINA_ON)
    _sprint(mgr, ticks=8, stamina=100.0, addr="0xA")
    _sprint(mgr, ticks=8, stamina=100.0, addr="0xB", start_tick=9)
    assert sink.rows == []


def test_the_stamina_cooldown_suppresses_a_repeat():
    mgr, sink = _run(STAMINA_ON)
    _sprint(mgr, ticks=40, stamina=100.0)
    assert sink.types().count("stamina_hack") == 1


# --------------------------------------------------------------------------
# fire_no_ammo (experimental, off by default)
# --------------------------------------------------------------------------

FIRE_ON = {"detect_fire_no_ammo": True, "detect_speedhack": False,
           "detect_infinite_ammo": False, "detect_remote_melee": False}


def _shoot(mgr, *, mags, ticks, tick_sec=2.0, weapon="BP_M4A1_C",
           causer=None, projectiles=None, start_tick=1):
    """Fire once per tick, with the magazine pool given per tick."""
    for i in range(ticks):
        m = mags[i] if isinstance(mags[0], list) else mags
        ts = tick_sec * i
        ev = [_hit(1000.0 + ts, causer or weapon)]
        mgr.run_tick(_snap(
            [_player(eos="eos-1", name="Alice", weapon=weapon, mags=m),
             _player(eos="eos-2", name="Bob", x=5000.0, addr="0xB")],
            tick=start_tick + i, elapsed=ts, events=ev,
            projectiles=(projectiles[i] if projectiles else ())),
            tick=start_tick + i, now=1000.0 + ts)


def test_fire_no_ammo_is_off_by_default():
    assert CheatDetect.DEFAULT_CONFIG["detect_fire_no_ammo"] is False


def test_shots_landing_against_a_frozen_ledger_flag():
    mgr, sink = _run(FIRE_ON)
    _shoot(mgr, mags=[30], ticks=6)                       # 10 s of shots
    assert sink.types() == ["fire_no_ammo"]
    d = sink.rows[0]["details"]
    assert d["weapon"] == "BP_M4A1_C"
    assert d["verifiedShots"] >= 4
    assert d["windowSec"] >= 10.0
    assert d["ammoUnchangedAt"] == 30


def test_a_ledger_that_falls_is_an_ordinary_player():
    mgr, sink = _run(FIRE_ON)
    _shoot(mgr, mags=[[30], [28], [25], [22], [19], [16]], ticks=6)
    assert sink.rows == []


def test_a_resupply_resets_the_observation():
    """An ammo sum that RISES is a crate, not a cheat."""
    mgr, sink = _run(FIRE_ON)
    _shoot(mgr, mags=[[30], [30], [60], [60], [60], [60]], ticks=6)
    assert sink.rows == []


def test_an_event_naming_another_weapon_is_not_a_verified_shot():
    """An explosive names its projectile and a vehicle weapon names the
    vehicle. Neither is the gun whose magazines we are reading, and pairing
    them would need a lookup table this project will not build."""
    mgr, sink = _run(FIRE_ON)
    _shoot(mgr, mags=[30], ticks=8, weapon="BP_M4A1_C",
           causer="BP_40MM_Proj2_C")
    assert sink.rows == []


def test_the_fire_window_is_the_same_ten_seconds_at_any_tick_rate():
    for tick_sec in (2.0, 1.0 / 3.0):
        need = int(round(10.0 / tick_sec)) + 1
        mgr, sink = _run(FIRE_ON)
        _shoot(mgr, mags=[30], ticks=need - 1, tick_sec=tick_sec)
        assert sink.rows == [], f"fired early at a {tick_sec:.2f}s tick"
        mgr, sink = _run(FIRE_ON)
        _shoot(mgr, mags=[30], ticks=need + 1, tick_sec=tick_sec)
        assert sink.types() == ["fire_no_ammo"], \
            f"never fired at a {tick_sec:.2f}s tick"


def _proj(pid, firer="Alice"):
    return {"id": pid, "classShort": "BP_40MM_Proj2_C", "firer": firer}


def test_a_projectile_with_a_verified_firer_counts_as_a_shot():
    """No damage event anywhere — a launcher fired at a treeline still puts
    rounds in the air, and the firer link is read from memory."""
    mgr, sink = _run(FIRE_ON)
    for i in range(7):
        ts = 2.0 * i
        mgr.run_tick(_snap(
            [_player(eos="eos-1", name="Alice", weapon="BP_M320_C",
                     mags=[1, 1, 1])],
            tick=i + 1, elapsed=ts, projectiles=[_proj(f"0x{i:x}")]),
            tick=i + 1, now=1000.0 + ts)
    assert sink.types() == ["fire_no_ammo"]


def test_the_projectiles_already_in_the_air_at_attach_are_not_new():
    """A reader restarting mid-match sees a sky full of rounds. None of them
    were fired on its first tick."""
    mgr, sink = _run(FIRE_ON)
    world = [_proj(f"0x{i:x}") for i in range(50)]
    for i in range(8):
        ts = 2.0 * i
        mgr.run_tick(_snap(
            [_player(eos="eos-1", name="Alice", weapon="BP_M320_C",
                     mags=[1])],
            tick=i + 1, elapsed=ts, projectiles=world),
            tick=i + 1, now=1000.0 + ts)
    assert sink.rows == []


def test_an_unattributed_projectile_accuses_nobody():
    mgr, sink = _run(FIRE_ON)
    for i in range(8):
        ts = 2.0 * i
        mgr.run_tick(_snap(
            [_player(eos="eos-1", name="Alice", weapon="BP_M320_C",
                     mags=[1])],
            tick=i + 1, elapsed=ts,
            projectiles=[_proj(f"0x{i:x}", firer=None)]),
            tick=i + 1, now=1000.0 + ts)
    assert sink.rows == []


def test_a_bleed_out_kill_is_not_a_shot():
    """The log emits a second event when a downed player dies, credited to
    whatever downed them — which may have been minutes earlier and cost a
    round then, not now. Four of those with the ammo sitting still is a
    marksman waiting, not a cheat, and it was this detector's entire false
    positive rate on a real archive."""
    mgr, sink = _run(FIRE_ON)
    for i in range(8):
        ts = 2.0 * i
        ev = _hit(1000.0 + ts)
        ev["wounded"], ev["killed"] = 0, 1        # Die(), not Wound()
        ev["victim"] = f"victim-{i}"
        mgr.run_tick(_snap(
            [_player(eos="eos-1", name="Alice", weapon="BP_M4A1_C",
                     mags=[30])],
            tick=i + 1, elapsed=ts, events=[ev]),
            tick=i + 1, now=1000.0 + ts)
    assert sink.rows == []


def test_a_stale_soldier_read_is_not_a_frozen_ledger():
    """A stale block repeats last tick's magazines, which is exactly what an
    ammo cheat looks like. A stale read does not weaken the signal, it
    manufactures it."""
    mgr, sink = _run(FIRE_ON)
    for i in range(8):
        ts = 2.0 * i
        p = _player(eos="eos-1", name="Alice", weapon="BP_M4A1_C", mags=[30],
                    stale=True)
        mgr.run_tick(_snap([p], tick=i + 1, elapsed=ts,
                           events=[_hit(1000.0 + ts)]),
                     tick=i + 1, now=1000.0 + ts)
    assert sink.rows == []


def test_the_fire_cooldown_suppresses_a_repeat():
    mgr, sink = _run(FIRE_ON)
    _shoot(mgr, mags=[30], ticks=40)
    assert sink.types().count("fire_no_ammo") == 1


# --------------------------------------------------------------------------
# no_reload (experimental, off by default)
# --------------------------------------------------------------------------

NORELOAD_ON = {"detect_no_reload": True, "detect_speedhack": False,
               "detect_infinite_ammo": False}


def _burn(mgr, *, mags, tick_sec=2.0, weapon="BP_M4A1_C", start_tick=1,
          vehicles=(), addr="0xA", cache_resets=0):
    """Feed a magazine pool tick by tick and let the ledger read the drops."""
    for i, m in enumerate(mags):
        ts = tick_sec * i
        mgr.run_tick(_snap(
            [_player(eos="eos-1", name="Alice", weapon=weapon, mags=m,
                     addr=addr)],
            tick=start_tick + i, elapsed=ts, vehicles=vehicles,
            cache_resets=cache_resets),
            tick=start_tick + i, now=1000.0 + ts)


def _pool(total, cap=30):
    """A magazine list holding `total` rounds in `cap`-sized magazines."""
    out = []
    left = total
    while left > cap:
        out.append(cap)
        left -= cap
    out.append(max(0, left))
    return out or [0]


def test_no_reload_is_off_by_default():
    assert CheatDetect.DEFAULT_CONFIG["detect_no_reload"] is False


def test_a_rifle_burning_more_than_reloading_allows_flags():
    """~450 rounds in 30 s against a ceiling of ~219 (x1.5 = ~328)."""
    mgr, sink = _run(NORELOAD_ON)
    mags = [_pool(600 - 30 * i) for i in range(16)]     # 30 rounds per 2 s
    _burn(mgr, mags=mags)
    assert sink.types() == ["no_reload"]
    d = sink.rows[0]["details"]
    assert d["path"] == "rolling window"
    assert d["capacityEstimate"] == 30
    assert d["roundsConsumed"] >= d["threshold"]


def test_a_fast_but_human_rifle_player_does_not_flag():
    """~150 rounds in 30 s — a mag-dumping player at robot speed. This is the
    number a flat 120-round threshold would have accused."""
    mgr, sink = _run(NORELOAD_ON)
    mags = [_pool(600 - 10 * i) for i in range(16)]     # 10 rounds per 2 s
    _burn(mgr, mags=mags)
    assert sink.rows == []


def test_the_no_reload_window_is_the_same_thirty_seconds_at_any_tick_rate():
    for tick_sec in (2.0, 1.0 / 3.0):
        n = int(round(30.0 / tick_sec)) + 1
        rate = 15.0 * tick_sec                       # 15 rounds/s of fire
        mgr, sink = _run(NORELOAD_ON)
        mags = [_pool(int(2000 - rate * i)) for i in range(n)]
        _burn(mgr, mags=mags, tick_sec=tick_sec)
        assert sink.types() == ["no_reload"], \
            f"never fired at a {tick_sec:.2f}s tick"
        # A tenth of that rate is ordinary play at either sampling rate.
        mgr, sink = _run(NORELOAD_ON)
        mags = [_pool(int(2000 - rate * i / 10)) for i in range(n)]
        _burn(mgr, mags=mags, tick_sec=tick_sec)
        assert sink.rows == [], f"false positive at a {tick_sec:.2f}s tick"


def test_a_belt_fed_gun_is_deliberately_out_of_reach():
    """300 rounds from a 200-round box is under the ceiling, and that is the
    accepted trade: belt weapons reload so rarely that removing the timer
    barely helps, and the alternative is accusing good machine-gunners."""
    mgr, sink = _run(NORELOAD_ON)
    mags = [[max(0, 200 - 20 * i), 200, 200] for i in range(16)]
    _burn(mgr, mags=mags, weapon="BP_M240_C")
    assert sink.rows == []


def test_two_launcher_shots_closer_than_a_reload_flag():
    """The single-shot path. Capacity 1, so the windowed model is blind: what
    is impossible here is two rounds gone inside one tick interval."""
    mgr, sink = _run(NORELOAD_ON)
    _burn(mgr, mags=[[1, 1, 1, 1], [1, 1, 1, 1], [1, 1], [1, 1], [0, 0]],
          weapon="BP_M320_HE_C")
    assert sink.types() == ["no_reload"]
    d = sink.rows[0]["details"]
    assert d["path"] == "single-shot spacing"
    assert d["capacityEstimate"] == 1
    assert d["strikes"] == 2


def test_one_launcher_round_at_a_time_never_flags():
    mgr, sink = _run(NORELOAD_ON)
    _burn(mgr, mags=[[1, 1, 1, 1], [1, 1, 1], [1, 1], [1], [0]],
          weapon="BP_M320_HE_C")
    assert sink.rows == []


def test_a_paced_launcher_cheater_is_caught_by_the_rate_window():
    """One rocket per tick: never two in one interval, so spacing is blind —
    the exact miss Craig predicted at 1 Hz. Against the rolling ceiling of
    1 + 30/3 = 11 rounds per 30 s, a round every tick crosses within half a
    minute at either tick rate."""
    for tick_sec in (2.0, 1.0):
        n = int(round(32.0 / tick_sec))
        mgr, sink = _run(NORELOAD_ON)
        mags = [[1] * max(0, n - i) for i in range(n)]
        _burn(mgr, mags=mags, weapon="BP_M320_HE_C", tick_sec=tick_sec)
        assert sink.types() == ["no_reload"], \
            f"paced cheater slipped a {tick_sec:.0f}s tick"
        d = sink.rows[0]["details"]
        assert d["path"] == "single-shot rate"
        assert d["roundsConsumed"] > d["ceiling"]


def test_a_launcher_at_real_reload_pace_never_flags():
    """A round every 6 s — brisk, honest play against a real 5-8 s reload —
    lands at ~6 rounds per 30 s window, far under the ceiling of 11. The 3 s
    reload floor doubles as the margin; no multiplier is stacked on it."""
    mgr, sink = _run(NORELOAD_ON)
    mags, total = [], 12
    for i in range(18):                     # 36 s at 2 s ticks
        if i and i % 3 == 0:
            total -= 1
        mags.append([1] * total)
    _burn(mgr, mags=mags, weapon="BP_M320_HE_C")
    assert sink.rows == []


def test_a_pistol_emptying_two_magazines_in_one_breath_flags():
    """Midcap instant rule: class capacity 8 with a matured census, then 16
    rounds inside one 2 s interval, twice — each needing a mid-interval
    reload plus continued fire in under 3 s."""
    mgr, sink = _run(NORELOAD_ON)
    pools = [[8, 8, 8, 8, 8, 8]] * 12       # census matures; no firing
    pools += [[8, 8, 8, 8], [8, 8, 8, 8], [8, 8]]
    _burn(mgr, mags=pools, weapon="BP_Makarov_C")
    assert sink.types() == ["no_reload"]
    d = sink.rows[0]["details"]
    assert d["path"] == "double-magazine interval"
    assert d["classCapacity"] == 8


def test_the_class_census_disarms_midcap_for_rifles():
    """Bob's full M4 teaches the census the class holds 30. Alice, same
    class, reads low on ammo ([5, 3, 2]) and then burns all 10 in one tick —
    ordinary automatic fire, but 10 >= 2x her own low reading. The class-wide
    census is what keeps her from being judged as a pistol."""
    mgr, sink = _run(NORELOAD_ON)
    for i in range(14):
        alice = [5, 3, 2] if i < 12 else [0, 0, 0]
        mgr.run_tick(_snap(
            [_player(eos="eos-1", name="Alice", weapon="BP_M4A1_C",
                     mags=alice, addr="0xA"),
             _player(eos="eos-2", name="Bob", weapon="BP_M4A1_C",
                     mags=[30, 30, 30], addr="0xB")],
            tick=1 + i, elapsed=2.0 * i, vehicles=(), cache_resets=0),
            tick=1 + i, now=1000.0 + 2.0 * i)
    assert sink.rows == []


def test_a_slower_sampler_widens_the_launcher_allowance():
    """dt is game-clock seconds, so at a 7 s interval two shots really could
    have fit — and the rule says so instead of accusing."""
    mgr, sink = _run(NORELOAD_ON)
    _burn(mgr, mags=[[1, 1, 1, 1], [1, 1], [0, 0]], weapon="BP_M320_HE_C",
          tick_sec=7.0)
    assert sink.rows == []


def test_a_pistol_firing_twice_in_two_seconds_is_not_impossible():
    """The spacing rule says every shot mandates a reload, and that is true of
    a one-round magazine and nothing else. A Makarov holds eight or nine, a
    Mosin six, the QLZ-87 drum six or seven — all of them fire consecutive
    rounds with no reload at all, and all three produced false accusations
    when the threshold sat at a capacity of 10."""
    for weapon, cap in (("BP_Makarov_C", 8), ("BP_Mosin_M1891_C", 6),
                        ("BP_QLZ87_AGL_HEDP_IronSights_C", 6)):
        mgr, sink = _run(NORELOAD_ON)
        _burn(mgr, weapon=weapon,
              mags=[[cap, cap], [cap - 2, cap], [cap - 5, cap],
                    [cap - 6, cap], [0, cap]])
        assert sink.rows == [], f"{weapon} was called impossible"


def test_a_bayonet_can_never_arm_the_detector():
    """Bayonets and binoculars present as [0] magazines in real data."""
    mgr, sink = _run(NORELOAD_ON)
    _burn(mgr, mags=[[0]] * 10, weapon="BP_AK74Bayonet_C")
    assert sink.rows == []


def test_a_weapon_swap_is_not_consumption():
    """The visible pool becomes a different gun's the moment they swap."""
    mgr, sink = _run(NORELOAD_ON)
    for i in range(10):
        ts = 2.0 * i
        weapon = "BP_M4A1_C" if i % 2 == 0 else "BP_M9_C"
        mags = [30, 30, 30, 30] if i % 2 == 0 else [15]
        mgr.run_tick(_snap(
            [_player(eos="eos-1", name="Alice", weapon=weapon, mags=mags)],
            tick=i + 1, elapsed=ts), tick=i + 1, now=1000.0 + ts)
    assert sink.rows == []


def test_a_respawn_is_not_consumption():
    mgr, sink = _run(NORELOAD_ON)
    _burn(mgr, mags=[_pool(600)] * 2, addr="0xA")
    _burn(mgr, mags=[_pool(30)] * 2, addr="0xB", start_tick=3)
    assert sink.rows == []


def test_a_cache_reset_is_not_consumption():
    mgr, sink = _run(NORELOAD_ON)
    mags = [_pool(600 - 30 * i) for i in range(16)]
    for i, m in enumerate(mags):
        ts = 2.0 * i
        mgr.run_tick(_snap(
            [_player(eos="eos-1", name="Alice", weapon="BP_M4A1_C", mags=m)],
            tick=i + 1, elapsed=ts, cache_resets=i),      # a reset every tick
            tick=i + 1, now=1000.0 + ts)
    assert sink.rows == []


def test_a_missing_magazine_list_is_not_measured():
    mgr, sink = _run(NORELOAD_ON)
    for i in range(16):
        ts = 2.0 * i
        mgr.run_tick(_snap(
            [_player(eos="eos-1", name="Alice", weapon="BP_M4A1_C")],
            tick=i + 1, elapsed=ts), tick=i + 1, now=1000.0 + ts)
    assert sink.rows == []


def test_the_no_reload_cooldown_suppresses_a_repeat():
    mgr, sink = _run(NORELOAD_ON)
    mags = [_pool(3000 - 60 * i) for i in range(48)]
    _burn(mgr, mags=mags)
    assert sink.types().count("no_reload") == 1


# --------------------------------------------------------------------------
# remote mine (experimental, off by default)
# --------------------------------------------------------------------------

MINE_ON = {"detect_remote_mine": True, "detect_speedhack": False}


def _mine(mid="0x1", *, x=0.0, cls="BP_Deployable_TM62Mine_C",
          placer="Alice", placer_eos="eos-1"):
    d = {"id": mid, "classShort": cls, "placer": placer,
         "placerEosId": placer_eos}
    if x is not None:
        d["position"] = {"x": x, "y": 0.0, "z": 0.0}
    return d


def _place(mgr, *, first=(), then=(), cache_resets=(0, 0)):
    for i, deps in enumerate((first, then)):
        mgr.run_tick(_snap([_player(eos="eos-1", name="Alice", x=0.0)],
                           tick=i + 1, elapsed=2.0 * i, deployables=deps,
                           cache_resets=cache_resets[i]),
                     tick=i + 1, now=1000.0 + 2.0 * i)


def test_remote_mine_is_off_by_default():
    assert CheatDetect.DEFAULT_CONFIG["detect_remote_mine"] is False


def test_a_mine_appearing_across_the_map_flags():
    mgr, sink = _run(MINE_ON)
    _place(mgr, first=(), then=[_mine(x=30_000.0)])       # 300 m away
    assert sink.types() == ["remote_mine"]
    d = sink.rows[0]["details"]
    assert d["distanceM"] == 300.0
    assert d["deployable"] == "BP_Deployable_TM62Mine_C"


def test_a_mine_placed_at_arms_length_does_not_flag():
    mgr, sink = _run(MINE_ON)
    _place(mgr, first=(), then=[_mine(x=150.0)])          # 1.5 m
    assert sink.rows == []


def test_a_mine_inside_the_drift_budget_does_not_flag():
    mgr, sink = _run(MINE_ON)
    _place(mgr, first=(), then=[_mine(x=4_900.0)])        # 49 m
    assert sink.rows == []


def test_the_world_a_restarting_reader_finds_is_not_newly_placed():
    """Every mine on the map is new to US on the first tick and old to the
    world. Three matches' worth of mass accusations live behind this."""
    mgr, sink = _run(MINE_ON)
    field = [_mine(f"0x{i:x}", x=30_000.0 + i) for i in range(40)]
    _place(mgr, first=field, then=field)
    assert sink.rows == []


def test_a_cache_reset_is_not_a_placement():
    mgr, sink = _run(MINE_ON)
    field = [_mine(f"0x{i:x}", x=30_000.0 + i) for i in range(40)]
    _place(mgr, first=(), then=field, cache_resets=(0, 1))
    assert sink.rows == []


def test_a_mine_with_no_resolvable_placer_accuses_nobody():
    mgr, sink = _run(MINE_ON)
    _place(mgr, first=(),
           then=[_mine(x=30_000.0, placer=None, placer_eos=None)])
    assert sink.rows == []


def test_a_mine_with_no_position_accuses_nobody():
    mgr, sink = _run(MINE_ON)
    _place(mgr, first=(), then=[_mine(x=None)])
    assert sink.rows == []


def test_a_hab_is_not_a_mine():
    mgr, sink = _run(MINE_ON)
    _place(mgr, first=(), then=[_mine(x=30_000.0, cls="US_Hab_Forest_C")])
    assert sink.rows == []


def test_the_mine_cooldown_suppresses_a_repeat():
    mgr, sink = _run(MINE_ON)
    mgr.run_tick(_snap([_player(eos="eos-1", name="Alice")], tick=1,
                       elapsed=0.0), tick=1, now=1000.0)
    for i in range(2, 8):
        mgr.run_tick(_snap(
            [_player(eos="eos-1", name="Alice")], tick=i, elapsed=2.0 * i,
            deployables=[_mine(f"0x{i:x}", x=30_000.0)]),
            tick=i, now=1000.0 + 2.0 * i)
    assert sink.types().count("remote_mine") == 1


# --------------------------------------------------------------------------
# every new detector is inert until it is switched on
# --------------------------------------------------------------------------

def test_the_new_detectors_are_all_silent_by_default():
    """One sequence that would trip every one of them, run with the shipped
    config. A detector is an accusation generator; none of these has been
    measured against a real archive yet."""
    mgr, sink = _run()
    for i in range(20):
        ts = 2.0 * i
        mgr.run_tick(_snap(
            [_player(eos="eos-1", name="Alice", x=6.5 * ts * 100.0,
                     stamina=100.0, weapon="BP_M4A1_C", mags=_pool(600 - 30 * i))],
            tick=i + 1, elapsed=ts,
            events=[_hit(1000.0 + ts, "BP_M4A1_C")],
            deployables=[_mine(f"0x{i:x}", x=30_000.0)],
            projectiles=[_proj(f"0xp{i:x}")]),
            tick=i + 1, now=1000.0 + ts)
    assert [t for t in sink.types()
            if t in ("stamina_hack", "fire_no_ammo", "no_reload",
                     "remote_mine")] == []


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------

def test_state_is_dropped_between_matches():
    """Everything teleports on a map change. Reset rather than reason about it."""
    mgr, sink = _run()
    _walk(mgr, speed_mps=30.0, ticks=6)
    mgr.run_tick(_snap([], tick=99, state="WaitingPostMatch"), tick=99)
    _walk(mgr, speed_mps=30.0, ticks=6, start_tick=100)
    assert sink.rows == []
