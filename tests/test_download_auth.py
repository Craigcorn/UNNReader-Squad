"""Agent-side download auth: the X-Sqr-Auth header and the `download-auth` cmd.

The wire-compat (agent seals -> central opens) is proven in the central repo's
test_download_gate. Here we pin the agent-local contract install.sh depends on:
the header is a sealed envelope bound to the filename, and `download-auth`
prints ONLY that JSON to stdout and fails cleanly when unenrolled.
"""
from __future__ import annotations

import argparse
import json

import pytest

from sqreader import ingest_client as ic
from sqreader.crypto_envelope import open_envelope

CREDS = {"SQREADER_AGENT_ID": "eu1", "SQREADER_AGENT_SECRET_HEX": "cd" * 32,
         "SQREADER_PUSH_URL": "https://c.test"}


def test_header_is_a_sealed_envelope_bound_to_the_file():
    hdr = ic.download_auth_header(CREDS, "sqreader-1.2.3-linux-x86_64")
    env = json.loads(hdr)
    assert env["agent_id"] == "eu1"
    payload = json.loads(open_envelope(
        env, secret=bytes.fromhex(CREDS["SQREADER_AGENT_SECRET_HEX"]),
        freshness_sec=None))
    assert payload == {"schema": "sqr-download-1",
                       "file": "sqreader-1.2.3-linux-x86_64"}


def test_header_is_compact_single_line():
    """install.sh drops it straight into a header — no newline may sneak in."""
    hdr = ic.download_auth_header(CREDS, "x")
    assert "\n" not in hdr and " " not in hdr


def test_download_artifact_attaches_auth(monkeypatch):
    seen = {}

    def fake_send(req, *, timeout):
        seen["auth"] = req.headers.get("X-sqr-auth")   # urllib title-cases keys
        return b"bytes"

    monkeypatch.setattr(ic, "_send", fake_send)
    ic.download_artifact(CREDS, "art")
    assert seen["auth"], "download_artifact must send X-Sqr-Auth"
    env = json.loads(seen["auth"])
    assert env["agent_id"] == "eu1"


def test_download_artifact_without_creds_sends_no_auth(monkeypatch):
    """Unenrolled box: bare GET, no crash."""
    seen = {}

    def fake_send(req, *, timeout):
        seen["auth"] = req.headers.get("X-sqr-auth")
        return b""

    monkeypatch.setattr(ic, "_send", fake_send)
    ic.download_artifact({"SQREADER_PUSH_URL": "https://c.test"}, "art")
    assert seen["auth"] is None


def test_download_auth_cmd_prints_only_json(capsys):
    from sqreader import agent_creds, cli

    # Pretend the box is enrolled.
    monkey = pytest.MonkeyPatch()
    monkey.setattr(agent_creds, "load", lambda: CREDS)
    try:
        rc = cli.cmd_download_auth(argparse.Namespace(file="the-artifact"))
    finally:
        monkey.undo()
    assert rc == 0
    out = capsys.readouterr().out.strip()
    env = json.loads(out)                        # stdout is exactly the JSON
    assert env["agent_id"] == "eu1"


def test_download_auth_cmd_fails_cleanly_when_unenrolled(capsys):
    from sqreader import agent_creds, cli

    monkey = pytest.MonkeyPatch()
    monkey.setattr(agent_creds, "load", lambda: None)
    try:
        rc = cli.cmd_download_auth(argparse.Namespace(file="x"))
    finally:
        monkey.undo()
    assert rc == 1
    cap = capsys.readouterr()
    assert cap.out.strip() == ""                 # nothing on stdout to splice
    assert "not enrolled" in cap.err
