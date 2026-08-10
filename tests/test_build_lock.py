"""The compiled-in central lock.

A released binary is built for exactly one central. These tests prove the lock
holds at the two places an agent can be talked into contacting somewhere else:
``ingest_client._push_base`` (the single choke point all six outbound calls go
through) and ``cli.cmd_enroll``. They also pin the fail-open behaviour of a
half-configured build — refusing every URL would brick a working box on upgrade,
which is worse than staying unlocked.

`build_config` constants are monkeypatched rather than imported from a built
artifact, so this runs against a plain source checkout.
"""
from __future__ import annotations

import pytest

from sqreader import build_config
from sqreader import ingest_client as ic

CENTRAL = "https://squadreader.com"


@pytest.fixture
def locked(monkeypatch):
    """Simulate a binary baked+locked to CENTRAL."""
    monkeypatch.setattr(build_config, "BAKED_CENTRAL_URL", CENTRAL)
    monkeypatch.setattr(build_config, "LOCKED", True)


# --------------------------------------------------------------- unlocked ---

def test_source_checkout_is_not_locked():
    """A plain checkout must ship unlocked, or dev/staging enroll breaks."""
    assert build_config.locked_base() is None


def test_unlocked_build_uses_whatever_creds_say():
    assert ic._push_base({"SQREADER_PUSH_URL": "https://evil.test"}) \
        == "https://evil.test"


def test_unlocked_build_still_requires_a_url():
    with pytest.raises(ic.PushError):
        ic._push_base({})


# ----------------------------------------------------------------- locked ---

def test_locked_build_refuses_a_foreign_push_url(locked):
    """The attack this exists for: hand-edit .env.agent to redirect pushes."""
    with pytest.raises(ic.PushError) as exc:
        ic._push_base({"SQREADER_PUSH_URL": "https://evil.test"})
    assert "locked to" in str(exc.value)


def test_locked_build_refuses_a_scheme_downgrade(locked):
    """Same host over http would expose sealed traffic to a MITM."""
    with pytest.raises(ic.PushError):
        ic._push_base({"SQREADER_PUSH_URL": "http://squadreader.com"})


def test_locked_build_refuses_a_lookalike_host(locked):
    with pytest.raises(ic.PushError):
        ic._push_base({"SQREADER_PUSH_URL": "https://squadreader.com.evil.test"})


def test_locked_build_accepts_its_own_central(locked):
    assert ic._push_base({"SQREADER_PUSH_URL": CENTRAL}) == CENTRAL


def test_locked_build_keeps_a_matching_url_with_a_path(locked):
    """Central may hand back a path prefix; only the origin is pinned."""
    assert ic._push_base({"SQREADER_PUSH_URL": CENTRAL + "/api/"}) \
        == CENTRAL + "/api"


def test_locked_build_falls_back_to_the_baked_url(locked):
    """Creds with no URL at all still reach the right platform."""
    assert ic._push_base({}) == CENTRAL


# ------------------------------------------------------------- fail-open ---

def test_locked_without_a_baked_url_stays_unlocked(monkeypatch):
    """Half-configured build must fail OPEN, not brick a working agent."""
    monkeypatch.setattr(build_config, "LOCKED", True)
    monkeypatch.setattr(build_config, "BAKED_CENTRAL_URL", "")
    assert build_config.locked_base() is None
    assert ic._push_base({"SQREADER_PUSH_URL": "https://x.test"}) \
        == "https://x.test"


# ------------------------------------------------------------ origin math ---

@pytest.mark.parametrize("a,b,same", [
    (CENTRAL, CENTRAL + "/", True),
    (CENTRAL, CENTRAL + "/deep/path", True),
    (CENTRAL, "HTTPS://SquadReader.com", True),        # case-insensitive
    (CENTRAL, "https://squadreader.com:443", False),   # explicit port differs
    (CENTRAL, "http://squadreader.com", False),        # scheme pinned
    (CENTRAL, "https://evil.test", False),
])
def test_same_origin(a, b, same):
    assert build_config.same_origin(a, b) is same


# ----------------------------------------------------------------- enroll ---

# ------------------------------------------------- packaging entry contract ---

def test_version_json_shape(capsys):
    """install.sh parses this; the key set is a contract, not cosmetics."""
    import argparse
    import json as _json

    from sqreader import cli

    assert cli.cmd_version(argparse.Namespace(json=True)) == 0
    got = _json.loads(capsys.readouterr().out)
    assert set(got) >= {"version", "compiled", "central", "platform"}
    assert got["central"] is None            # unlocked source checkout


def test_version_json_reports_the_baked_central(locked, capsys):
    import argparse
    import json as _json

    from sqreader import cli

    cli.cmd_version(argparse.Namespace(json=True))
    assert _json.loads(capsys.readouterr().out)["central"] == CENTRAL


def test_selftest_passes_on_a_working_install(capsys):
    """The build smoke test and the installer both gate on this exit code."""
    import argparse

    from sqreader import cli

    assert cli.cmd_selftest(argparse.Namespace()) == 0
    assert "PASS" in capsys.readouterr().out


def test_entry_point_module_exists():
    """Nuitka compiles a real __main__; the console-script shim never ships."""
    import sqreader.__main__ as m

    assert callable(m.main)


# ----------------------------------------------------------------- enroll ---

def test_enroll_ignores_a_foreign_central_url_when_locked(locked, monkeypatch,
                                                          capsys, tmp_path):
    """`enroll --central-url https://evil` must still enroll against CENTRAL."""
    import argparse

    from sqreader import agent_creds, cli, hwfp

    seen: dict[str, str] = {}

    def fake_enroll(central, token, fp, **kw):
        seen["central"] = central
        return {"SQREADER_AGENT_ID": "a1", "SQREADER_AGENT_SECRET_HEX": "ab",
                "SQREADER_PUSH_URL": central}

    monkeypatch.setattr(ic, "enroll", fake_enroll)
    monkeypatch.setattr(hwfp, "compute", lambda: "fp-1")
    monkeypatch.setattr(agent_creds, "write", lambda f: tmp_path / ".env.agent")

    rc = cli.cmd_enroll(argparse.Namespace(
        token="tok", central_url="https://evil.test", status=False,
        stats_db=None))

    assert rc == 0
    assert seen["central"] == CENTRAL
    assert "locked to" in capsys.readouterr().err


# --------------------------------------------------- two-tier worker spawn ---

def test_worker_prefix_from_source_uses_python_dash_m():
    """Source behaviour must stay byte-identical — production altai runs it."""
    import sys as _sys

    from sqreader import cli

    assert cli._worker_prefix() == [_sys.executable, "-m", "sqreader.cli",
                                    "build-worker"]


def test_worker_prefix_when_compiled_reinvokes_the_binary(monkeypatch):
    """In a Nuitka onefile sys.executable is a python3 that does not exist, so
    spawning it killed the agent on startup. Re-invoke the artifact instead."""
    from sqreader import cli

    monkeypatch.setattr(cli, "_is_compiled", lambda: True)
    monkeypatch.setenv("NUITKA_ONEFILE_BINARY", "/opt/sqreader/sqreader")
    assert cli._worker_prefix() == ["/opt/sqreader/sqreader", "build-worker"]


def test_worker_prefix_compiled_falls_back_to_argv0(monkeypatch):
    from sqreader import cli

    monkeypatch.setattr(cli, "_is_compiled", lambda: True)
    monkeypatch.delenv("NUITKA_ONEFILE_BINARY", raising=False)
    monkeypatch.setattr("sys.argv", ["/opt/sqreader/sqreader", "serve"])
    assert cli._worker_prefix() == ["/opt/sqreader/sqreader", "build-worker"]
