"""Pure-logic tests for the Squad-log damage/wound/die correlation. No I/O,
no live process — feeds real log-line shapes through DamageLogParser and
checks the events it emits."""
from sqreader.squad.logtail import (
    DamageLogParser, _norm_weapon, find_squad_log, resolve_event_names,
)

TS = "[2026.07.12-18.31.24:082][892]"


def actual(victim, attacker, eos, weapon, dmg="50.0"):
    return (f"{TS}LogSquad: Player: {victim} ActualDamage={dmg} from {attacker} "
            f"(Online IDs: EOS: {eos} steam: 76561190000000000 | "
            f"Player Controller ID: BP_PlayerController_C_1)caused by {weapon}")


def wound(victim, ctrl="BP_PlayerController_C_1", eos="000200"):
    return (f"{TS}LogSquadTrace: [DedicatedServer]Wound(): Player: {victim} "
            f"KillingDamage=50.0 from {ctrl} (Online IDs: EOS: {eos} steam: 1)")


def die(victim, ctrl="nullptr", eos="INVALID", caused=None):
    line = (f"{TS}LogSquadTrace: [DedicatedServer]Die(): Player:{victim} "
            f"KillingDamage=100.0 from {ctrl} (Online IDs: EOS: {eos})")
    if caused is not None:
        line += f" caused by {caused}"
    return line


def revive(reviver, victim, reos="0002re", veos="0002vi"):
    return (f"{TS}LogSquad: {reviver} (Online IDs: EOS: {reos} steam: 1) "
            f"has revived {victim} (Online IDs: EOS: {veos} steam: 2)")


def stamped(line, sec):
    """Re-stamp a helper line `sec` seconds after the fixture timestamp, for the
    tests that have to outlive the 330 s correlation TTL."""
    m, s = divmod(24 + sec, 60)
    return line.replace(TS, f"[2026.07.12-18.{31 + m:02d}.{s:02d}:082][892]")


def only(evs, **kw):
    hits = [e for e in evs if all(e.get(k) == v for k, v in kw.items())]
    return hits


def test_weapon_instance_id_stripped():
    assert _norm_weapon("BP_M4_SimonOffense_T800_C_2007009837") == "BP_M4_SimonOffense_T800_C"
    assert _norm_weapon("BP_Hydra70_Proj2_C_2006977119") == "BP_Hydra70_Proj2_C"
    assert _norm_weapon("nullptr") == "nullptr"


def test_wound_carries_attacker_and_weapon():
    p = DamageLogParser()
    p.feed(actual("VictimA", "KillerB", "0002ab", "BP_AK74_C_99"))
    p.feed(wound("VictimA"))
    evs = p.drain()
    w = only(evs, wounded=True)
    assert len(w) == 1
    assert w[0]["victim"] == "VictimA"
    assert w[0]["attacker"] == "KillerB"
    assert w[0]["causerWeapon"] == "BP_AK74_C"


def test_incap_held_past_three_minutes_still_attributed():
    # Squad allows a downed player 300 s before the forced give-up, and
    # players hoping for a medic routinely use most of it. The correlation
    # TTL used to be 180 s — so a give-up at 4m17s came out unattributed,
    # verified on a real recording ("?" in the feed while the game credited
    # the wounder). The bystander death 200 s in matters: the GC pass runs on
    # every processed death, which is what purged the wound on a busy server.
    def stamp(line, sec):
        m, s = divmod(24 + sec, 60)
        return line.replace(TS, f"[2026.07.12-18.{31 + m:02d}.{s:02d}:082][892]")
    p = DamageLogParser()
    p.feed(stamp(actual("VictimA", "KillerB", "0002ab", "BP_AK74_C_1"), 0))
    p.feed(stamp(wound("VictimA"), 1))
    p.feed(stamp(die("Bystander", ctrl="nullptr"), 200))
    p.feed(stamp(die("VictimA", ctrl="nullptr"), 257))
    k = only(p.drain(), killed=True, victim="VictimA")
    assert len(k) == 1
    assert k[0]["attacker"] == "KillerB"
    assert k[0]["selfInflicted"] is False


def test_bleed_out_death_attributed_to_the_wounder():
    # The classic Squad case: hit -> Wound (attacker known) -> later Die from
    # nullptr (bleed-out, no attacker on the line). Must credit the wounder.
    p = DamageLogParser()
    p.feed(actual("VictimA", "KillerB", "0002ab", "BP_AK74_C_1"))
    p.feed(wound("VictimA"))
    p.feed(die("VictimA", ctrl="nullptr"))   # bleed-out
    evs = p.drain()
    k = only(evs, killed=True)
    assert len(k) == 1
    assert k[0]["attacker"] == "KillerB"
    assert k[0]["causerWeapon"] == "BP_AK74_C"
    assert k[0]["selfInflicted"] is False


def test_instant_kill_attributed_from_last_hit():
    # Die straight from a real controller (no Wound): attacker is the last hit.
    p = DamageLogParser()
    p.feed(actual("VictimA", "KillerB", "0002ab", "BP_Tank_C_1"))
    p.feed(die("VictimA", ctrl="BP_PlayerController_C_1"))
    k = only(p.drain(), killed=True)
    assert len(k) == 1
    assert k[0]["attacker"] == "KillerB"


def test_suicide_has_no_killer():
    p = DamageLogParser()
    p.feed(actual("SoloGuy", "SoloGuy", "0002ab", "BP_Frag_C_1"))
    p.feed(die("SoloGuy", ctrl="BP_PlayerController_C_1"))
    k = only(p.drain(), killed=True)
    assert len(k) == 1
    assert k[0]["attacker"] is None
    assert k[0]["selfInflicted"] is True


def test_revive_clears_attribution_so_later_world_death_is_unattributed():
    # KillerB downs A; a medic revives A; A later dies to a world cause.
    # That death must NOT be credited to B.
    p = DamageLogParser()
    p.feed(actual("PlayerA", "KillerB", "0002ab", "BP_AK74_C_1"))
    p.feed(wound("PlayerA"))
    p.drain()  # wounded row consumed
    p.feed(revive("Medic", "PlayerA"))       # <-- back in the fight
    p.feed(die("PlayerA", ctrl="nullptr"))   # later world death (fell)
    k = only(p.drain(), killed=True)
    assert len(k) == 1
    assert k[0]["attacker"] is None          # NOT credited to KillerB
    assert k[0]["selfInflicted"] is False


def test_revive_then_fresh_incap_credits_the_new_attacker():
    p = DamageLogParser()
    p.feed(actual("PlayerA", "KillerB", "0002ab", "BP_AK74_C_1"))
    p.feed(wound("PlayerA"))
    p.feed(revive("Medic", "PlayerA"))
    p.drain()
    p.feed(actual("PlayerA", "KillerC", "0002cd", "BP_M4_C_1"))
    p.feed(wound("PlayerA"))                  # fresh incap by C
    p.feed(die("PlayerA", ctrl="nullptr"))    # bleeds out from C's incap
    k = only(p.drain(), killed=True)
    assert len(k) == 1
    assert k[0]["attacker"] == "KillerC"     # the new wounder, not B


def test_world_death_with_no_prior_hit_has_no_attacker():
    p = DamageLogParser()
    p.feed(die("Faller", ctrl="nullptr"))   # fell, never hit by anyone
    k = only(p.drain(), killed=True)
    assert len(k) == 1
    assert k[0]["attacker"] is None
    assert k[0]["selfInflicted"] is False


def test_give_up_from_own_pawn_is_self_inflicted():
    # The real "gave up while downed" shape: Die from the victim's OWN
    # controller, caused by nullptr, with no hit ever recorded. Nobody killed
    # them — flag it as a self death so the feed shows it, not a bare "?".
    p = DamageLogParser()
    p.feed(die("Quitter", ctrl="BP_PlayerController_C_9", caused="nullptr"))
    k = only(p.drain(), killed=True)
    assert len(k) == 1
    assert k[0]["attacker"] is None
    assert k[0]["selfInflicted"] is True
    assert k[0]["damageType"] is None


def test_world_cause_surfaced_as_damage_type():
    # An unattributed death whose log line names a real cause (fall/drown/etc.)
    # carries that cause as damageType so the feed can phrase it.
    p = DamageLogParser()
    p.feed(die("Diver", ctrl="nullptr", caused="BP_Drown_C_2007009837"))
    k = only(p.drain(), killed=True)
    assert len(k) == 1
    assert k[0]["attacker"] is None
    assert k[0]["selfInflicted"] is False
    assert k[0]["damageType"] == "BP_Drown_C"   # instance id stripped


def test_bleed_out_still_attributed_even_with_caused_by_nullptr():
    # Regression: a real bleed-out (wounded by an enemy, then Die caused by
    # nullptr) must STILL credit the wounder — the give-up path only triggers
    # when there is no attacker on record.
    p = DamageLogParser()
    p.feed(actual("VictimA", "KillerB", "0002ab", "BP_AK74_C_1"))
    p.feed(wound("VictimA"))
    p.feed(die("VictimA", ctrl="BP_PlayerController_C_1", caused="nullptr"))
    k = only(p.drain(), killed=True)
    assert len(k) == 1
    assert k[0]["attacker"] == "KillerB"
    assert k[0]["selfInflicted"] is False


def test_die_eos_recovers_attacker_after_cache_expiry():
    # Death lands long after the wound, so the name-cache is empty — but the Die
    # line still carries the killer's controller EOS. resolve_event_names
    # recovers the attacker from the live roster. This is the round-end "?" fix.
    p = DamageLogParser()
    p.feed(die("Victim", ctrl="BP_PlayerController_C_5", eos="0002killer",
               caused="BP_Soldier_RU_Rifleman1_Desert_C_1"))
    evs = p.drain()
    k = only(evs, killed=True)
    assert len(k) == 1
    assert k[0]["attacker"] is None             # logtail alone can't name them
    assert k[0]["killerEos"] == "0002killer"    # but it kept the Die-line EOS
    resolve_event_names(evs, [
        {"name": "KillerBob", "eosId": "0002killer"},
        {"name": "Victim", "eosId": "0002victim"},
    ])
    assert k[0]["attacker"] == "KillerBob"       # recovered by EOS
    assert k[0]["attackerEosId"] == "0002killer"
    assert "killerEos" not in k[0]               # scratch field dropped


def test_die_eos_resolved_from_the_logs_own_id_cache():
    # The repair: the Die line's credited-killer id is resolved against ids the
    # LOG itself supplied, so it works on a licensed server (where the roster's
    # ids are UUIDs and can never match a 32-hex log id). KillerB's id enters
    # the cache from a hit on somebody else entirely; 400 s later the wound
    # correlation is long dead and the id is all that is left.
    p = DamageLogParser()
    p.feed(stamped(actual("Bystander", "『GM』 KillerB", "0002killerb",
                          "BP_AK74_C_1"), 0))
    p.feed(stamped(actual("VictimA", "『GM』 KillerB", "0002killerb",
                          "BP_AK74_C_1"), 1))
    p.feed(stamped(wound("VictimA"), 2))
    p.drain()
    p.feed(stamped(die("Bystander", ctrl="nullptr"), 400))   # GC purges the wound
    p.feed(stamped(die("VictimA", ctrl="BP_PlayerController_C_5", eos="0002killerb",
                       caused="BP_Soldier_RU_Rifleman1_Desert_C_1"), 420))
    evs = p.drain()
    k = only(evs, killed=True, victim="VictimA")
    assert len(k) == 1
    assert k[0]["attacker"] == "『GM』 KillerB"     # named without the roster
    assert k[0]["attackerEosId"] == "0002killerb"
    assert k[0]["selfInflicted"] is False
    assert k[0]["killerEos"] is None               # answered here, nothing left to try
    # ...and the licensed-server roster, whose ids are a different namespace
    # entirely, only has to strip the clan tag.
    resolve_event_names(evs, [
        {"name": "KillerB", "eosId": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"},
        {"name": "VictimA", "eosId": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"},
    ])
    assert k[0]["attacker"] == "KillerB"


def test_die_eos_matching_the_victims_own_cached_id_is_a_self_death():
    # Ruby's own id is in the cache from a hit SHE dealt. When the Die line
    # credits that id, the kill is hers to nobody — the self-death check, now
    # asked in the namespace that can answer it. The causer here is a real
    # class, so the older "from a controller with no causer" heuristic stays
    # silent and only the cache can call this.
    p = DamageLogParser()
    p.feed(stamped(actual("SomeoneElse", "Ruby", "0002ruby", "BP_AK74_C_1"), 0))
    p.feed(stamped(actual("Ruby", "KillerB", "0002killerb", "BP_M4_C_1"), 5))
    p.feed(stamped(wound("Ruby"), 6))
    p.drain()
    p.feed(stamped(die("Bystander", ctrl="nullptr"), 400))   # GC purges the wound
    p.feed(stamped(die("Ruby", ctrl="BP_PlayerController_C_3", eos="0002ruby",
                       caused="BP_Frag_C_1"), 420))
    k = only(p.drain(), killed=True, victim="Ruby")
    assert len(k) == 1
    assert k[0]["attacker"] is None            # never credited her own kill
    assert k[0]["selfInflicted"] is True
    assert k[0]["damageType"] == "BP_Frag_C"


def test_die_eos_unknown_to_the_cache_is_left_for_the_roster():
    # A killer the log never named — no damage dealt, no revive either way.
    # The cache does not guess; the id goes out as it always did, for the
    # roster fallback to try, and an honest "?" is the answer when it misses.
    p = DamageLogParser()
    p.feed(actual("Bystander", "SomeoneElse", "0002else", "BP_AK74_C_1"))
    p.feed(die("Victim", ctrl="BP_PlayerController_C_5", eos="0002ghost",
               caused="BP_Soldier_RU_Rifleman1_Desert_C_1"))
    evs = p.drain()
    k = only(evs, killed=True, victim="Victim")
    assert len(k) == 1
    assert k[0]["attacker"] is None
    assert k[0]["selfInflicted"] is False
    assert k[0]["killerEos"] == "0002ghost"    # still handed to resolve_event_names
    resolve_event_names(evs, [{"name": "Victim", "eosId": "0002victim"}])
    assert k[0]["attacker"] is None            # roster misses too — stays "?"
    assert k[0]["selfInflicted"] is False


def test_revive_line_teaches_the_cache_both_of_its_players():
    # A medic can spend a whole match without dealing damage, so the damage
    # lines never name them — but every revive names both parties WITH their
    # ids, which is exactly the pairing the Die line needs later.
    p = DamageLogParser()
    p.feed(stamped(revive("Doc", "Ruby", reos="0002doc", veos="0002ruby"), 0))
    p.feed(stamped(wound("VictimA"), 1))
    p.feed(stamped(wound("VictimB"), 2))
    p.drain()
    p.feed(stamped(die("Bystander", ctrl="nullptr"), 400))   # GC purges both wounds
    p.feed(stamped(die("VictimA", ctrl="BP_PlayerController_C_2", eos="0002doc",
                       caused="BP_Soldier_RU_Rifleman1_Desert_C_1"), 420))
    p.feed(stamped(die("VictimB", ctrl="BP_PlayerController_C_4", eos="0002ruby",
                       caused="BP_Soldier_RU_Rifleman1_Desert_C_1"), 421))
    evs = p.drain()
    ka = only(evs, killed=True, victim="VictimA")
    kb = only(evs, killed=True, victim="VictimB")
    assert len(ka) == 1 and len(kb) == 1
    assert ka[0]["attacker"] == "Doc"          # the reviver half of the line
    assert ka[0]["attackerEosId"] == "0002doc"
    assert kb[0]["attacker"] == "Ruby"         # the revived half
    assert kb[0]["attackerEosId"] == "0002ruby"


def test_eos_name_cache_is_bounded():
    # Parser lifetime is a whole session, so the cache needs a ceiling. It
    # evicts the oldest id it learned, which costs a "?" and never a name.
    p = DamageLogParser(max_eos_names=4)
    for i in range(6):
        p.feed(actual(f"V{i}", f"K{i}", f"0002k{i}", "BP_AK74_C_1"))
    assert len(p._eos_names) == 4
    assert "0002k0" not in p._eos_names        # oldest evicted
    assert p._eos_names["0002k5"] == "K5"


def test_die_eos_self_kill_not_credited_to_victim():
    # If the Die-line EOS is the victim's own, do not name them their own killer
    # — flag self-inflicted instead.
    p = DamageLogParser()
    p.feed(die("Loner", ctrl="BP_PlayerController_C_7", eos="0002loner",
               caused="BP_Frag_C_1"))
    evs = p.drain()
    resolve_event_names(evs, [{"name": "Loner", "eosId": "0002loner"}])
    k = only(evs, killed=True)[0]
    assert k["attacker"] is None
    assert k["selfInflicted"] is True


def test_die_eos_unknown_killer_left_unattributed():
    # Killer EOS not on the live roster (disconnected) — stays "?" rather than
    # inventing a name.
    p = DamageLogParser()
    p.feed(die("Victim", ctrl="BP_PlayerController_C_5", eos="0002gone",
               caused="BP_Soldier_RU_Rifleman1_Desert_C_1"))
    evs = p.drain()
    resolve_event_names(evs, [{"name": "Victim", "eosId": "0002victim"}])
    k = only(evs, killed=True)[0]
    assert k["attacker"] is None
    assert k["selfInflicted"] is False


def test_names_with_spaces_and_unicode():
    p = DamageLogParser()
    p.feed(actual("[ TŞK ] Hannibal", "「Ns」 Alexa", "0002cd", "BP_M4_C_1"))
    p.feed(wound("[ TŞK ] Hannibal"))
    w = only(p.drain(), wounded=True)
    assert len(w) == 1
    assert w[0]["victim"] == "[ TŞK ] Hannibal"
    assert w[0]["attacker"] == "「Ns」 Alexa"


def test_correlation_state_survives_drain():
    # The startup catch-up feeds the log tail, drains-and-discards the stale
    # events, then goes live — so a death after the discard must still be
    # attributed from the wound that preceded it. i.e. drain() must NOT wipe
    # the pending / wounded_by correlation state.
    p = DamageLogParser()
    p.feed(actual("VictimA", "KillerB", "0002ab", "BP_AK74_C_1"))
    p.feed(wound("VictimA"))
    p.drain()  # <-- catch-up discard
    p.feed(die("VictimA", ctrl="nullptr"))   # dies AFTER the discard
    k = only(p.drain(), killed=True)
    assert len(k) == 1
    assert k[0]["attacker"] == "KillerB"     # still credited to the wounder


def test_drain_clears():
    p = DamageLogParser()
    p.feed(actual("V", "K", "0002ab", "BP_X_C_1"))
    p.feed(wound("V"))
    assert len(p.drain()) == 1
    assert p.drain() == []


def test_resolve_event_names_strips_clan_tag_to_base():
    players = [{"name": "I3lack"}, {"name": "MorrukGta5"}, {"name": "Selective"}]
    events = [
        {"attacker": "『GM』 I3lack", "victim": "Selective"},
        {"attacker": "TIM MorrukGta5", "victim": "『GM』 I3lack"},
    ]
    resolve_event_names(events, players)
    assert events[0]["attacker"] == "I3lack"      # tag stripped to base
    assert events[0]["victim"] == "Selective"     # exact stays
    assert events[1]["attacker"] == "MorrukGta5"
    assert events[1]["victim"] == "I3lack"


def test_resolve_event_names_no_midword_false_match():
    # "lack" must NOT match "I3lack" (no tag boundary before it).
    players = [{"name": "lack"}, {"name": "I3lack"}]
    events = [{"attacker": "『GM』 I3lack", "victim": None}]
    resolve_event_names(events, players)
    assert events[0]["attacker"] == "I3lack"


def test_resolve_event_names_unknown_left_as_is():
    players = [{"name": "Alice"}]
    events = [{"attacker": "SomeoneWhoLeft", "victim": "Alice"}]
    resolve_event_names(events, players)
    assert events[0]["attacker"] == "SomeoneWhoLeft"  # no crash, unchanged
    assert events[0]["victim"] == "Alice"


def test_find_squad_log_returns_none_or_str():
    # Environment-dependent (no logs on the test box) — just must not raise.
    r = find_squad_log()
    assert r is None or isinstance(r, str)
