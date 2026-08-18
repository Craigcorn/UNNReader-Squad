"""The notifier sits inside the reader's tick loop, so its guarantees are not
about Discord — they are about what happens to the reader when Discord misbehaves.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from sqreader.plugins import notify
from sqreader.plugins.notify import (
    COLOUR_INFO, COLOUR_MAYBE, COLOUR_SURE, DiscordNotifier, build_embed,
    colour_for, match_clock, replay_url,
)


def _alert(**kw):
    a = {"ts": 1_760_000_000.0, "tick": 42, "plugin_id": "cheat_detect",
         "alert_type": "speedhack", "eos_id": "0002abc", "player_name": "Kenlaus",
         "match_id": "e9e2639f-9062-4d6a-a457-eb6479506af5", "confidence": 0.9,
         "details": {"speed_ms": 24.1, "elapsedSec": 1325}}
    a.update(kw)
    return a


# --- the message ------------------------------------------------------------

def test_the_embed_carries_the_evidence_not_just_the_accusation():
    e = build_embed(_alert(), replay_base="https://squadreader.com",
                    server_label="altai-tr-1")
    d = e["description"]
    assert "speedhack" in e["title"] and "Kenlaus" in e["title"]
    assert "90%" in d                                   # confidence
    assert "22:05" in d                                 # 1325 s of match time
    assert "speed_ms" in d, "the numbers behind the call must travel with it"
    assert "watch the replay" in d and "e9e2639f" in d
    assert e["footer"]["text"] == "altai-tr-1 - cheat_detect"


def test_colour_says_how_sure_not_how_bad():
    assert colour_for(0.95) == COLOUR_SURE
    assert colour_for(0.6) == COLOUR_MAYBE
    assert colour_for(0.1) == COLOUR_INFO
    assert colour_for(None) == COLOUR_INFO
    assert colour_for("nonsense") == COLOUR_INFO        # never raises


def test_a_link_is_only_offered_when_it_would_work():
    assert replay_url("https://x.dev", "m1") == \
        "https://x.dev/replay/?mode=replay&id=m1"
    assert replay_url("https://x.dev/", "m1").count("//") == 1, \
        "a trailing slash must not produce a doubled path"
    # No base configured, or no match to point at: no link rather than a
    # broken one.
    assert replay_url(None, "m1") is None
    assert replay_url("https://x.dev", None) is None


def test_the_embed_survives_junk():
    """An alert is built by a plugin, and a plugin can be wrong. Formatting one
    must not be the thing that takes the reader down."""
    for bad in ({}, {"details": None}, {"details": "not a dict"},
                {"confidence": "high"}, {"ts": None}, {"player_name": None,
                                                       "eos_id": None}):
        e = build_embed(_alert(**bad))
        assert isinstance(e["title"], str) and isinstance(e["color"], int)


def test_match_clock_only_reports_a_time_it_was_given():
    assert match_clock({"elapsedSec": 0}) == "0:00"
    assert match_clock({"elapsedSec": 1325}) == "22:05"
    assert match_clock({}) is None
    assert match_clock({"elapsedSec": -1}) is None
    assert match_clock("not a dict") is None


def test_a_huge_evidence_blob_is_truncated_not_rejected():
    e = build_embed(_alert(details={"blob": "x" * 5000, "elapsedSec": 10}))
    assert len(e["description"]) <= 3900


# --- what the reader is promised -------------------------------------------

def test_storing_happens_even_when_announcing_explodes(monkeypatch):
    """The database write is the durable half. If the notifier throws, the
    alert must still be recorded — losing evidence to a failed notification
    would be the wrong way round."""
    stored = []
    n = DiscordNotifier.__new__(DiscordNotifier)          # no worker thread
    n._min_conf = 0.0

    def boom(_a):
        raise RuntimeError("network gone")
    monkeypatch.setattr(n, "offer", boom, raising=False)

    emit = DiscordNotifier.wrap(n, stored.append)
    emit(_alert())
    assert len(stored) == 1, "the alert was lost when the webhook failed"


def test_a_full_queue_drops_instead_of_blocking():
    """Plugins run in the tick loop. If Discord is down and the queue fills,
    offering must return immediately — a stalled reader costs the recording,
    which is worth more than the notification."""
    n = DiscordNotifier.__new__(DiscordNotifier)
    n._min_conf = 0.0
    n.dropped = 0
    import queue as _q
    n._q = _q.Queue(maxsize=2)

    t0 = time.monotonic()
    for _ in range(50):
        n.offer(_alert())
    assert time.monotonic() - t0 < 1.0, "offer() blocked the caller"
    assert n._q.qsize() == 2
    assert n.dropped == 48


def test_low_confidence_is_stored_but_not_announced():
    n = DiscordNotifier.__new__(DiscordNotifier)
    n._min_conf = 0.7
    n.dropped = 0
    import queue as _q
    n._q = _q.Queue(maxsize=10)

    stored = []
    emit = DiscordNotifier.wrap(n, stored.append)
    emit(_alert(confidence=0.2))
    emit(_alert(confidence=0.9))
    assert len(stored) == 2, "everything is kept"
    assert n._q.qsize() == 1, "only the confident one is announced"


def test_the_webhook_url_never_reaches_a_log(caplog):
    """The URL is a credential: whoever has it can post in that channel."""
    secret = "https://discord.com/api/webhooks/1234/SUPERSECRETTOKEN"
    n = DiscordNotifier.__new__(DiscordNotifier)
    n._url = secret
    n._replay_base = None
    n._server_label = None
    n._timeout = 0.01
    n._stop = __import__("threading").Event()

    import urllib.error
    def fail(*_a, **_k):
        raise urllib.error.HTTPError(secret, 403, "Forbidden", {}, None)
    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fail
    try:
        with caplog.at_level("WARNING"):
            n._post([_alert()])
    finally:
        urllib.request.urlopen = orig
    assert "SUPERSECRETTOKEN" not in caplog.text
    assert "403" in caplog.text, "the failure itself must still be reported"


def test_a_batch_is_one_message_not_ten(monkeypatch):
    """A cheater tripping four detectors in a second should arrive as one
    readable post, not four that race the rate limiter."""
    sent = []
    n = DiscordNotifier.__new__(DiscordNotifier)
    n._url = "https://example.invalid/hook"
    n._replay_base = None
    n._server_label = None
    n._timeout = 1.0
    n._stop = __import__("threading").Event()

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def capture(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        return FakeResp()
    monkeypatch.setattr("urllib.request.urlopen", capture)

    n._post([_alert(), _alert(alert_type="magic_bullet")])
    assert len(sent) == 1
    assert len(sent[0]["embeds"]) == 2


def test_it_never_sends_more_embeds_than_discord_accepts():
    assert notify.MAX_EMBEDS <= 10


# --- turning plugins on without owning the start command --------------------

def test_plugins_can_be_enabled_from_the_config_file(tmp_path, monkeypatch):
    """Altai's agent is started by a container entrypoint that is not in this
    repo, so `--plugins-config` could not be added to it. A deployment must be
    able to turn plugins on by editing a file it does own."""
    from sqreader import config

    cfg = tmp_path / "plugins_config.json"
    cfg.write_text('{"cheat_detect": {"enabled": true}}', encoding="utf-8")
    monkeypatch.setattr(config, "get",
                        lambda k, d=None: str(cfg) if k == "plugins_config"
                        else d)

    class Args:
        plugins_config = None
    raw = getattr(Args, "plugins_config", None) or config.get("plugins_config")
    assert raw and Path(str(raw)).is_file()

    from sqreader.plugins import load_config
    loaded = load_config(Path(str(raw)))
    assert loaded.get("cheat_detect", {}).get("enabled") is True


def test_the_flag_still_wins_over_the_config_key(tmp_path, monkeypatch):
    from sqreader import config
    monkeypatch.setattr(config, "get",
                        lambda k, d=None: "/from/config" if k == "plugins_config"
                        else d)

    class Args:
        plugins_config = "/from/flag"
    raw = getattr(Args, "plugins_config", None) or config.get("plugins_config")
    assert raw == "/from/flag"
