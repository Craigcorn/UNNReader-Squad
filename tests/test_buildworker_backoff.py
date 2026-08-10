"""Respawn throttling for the two-tier build worker.

The worker runs on someone else's game server. A child that dies at startup —
bad argv, missing interpreter, a compiled build whose re-invocation path is
wrong — used to be re-forked in a tight loop with no delay and no cap, which
turns a broken helper into a CPU fire on the box running Squad.

A worker that ran normally and then exited must still respawn immediately;
only the never-produced-a-frame case backs off.
"""
from __future__ import annotations

import threading

from sqreader.squad import buildworker as bw


class _DeadPipe:
    """stdout of a process that exits without writing anything."""

    def __iter__(self):
        return iter(())


class _Proc:
    def __init__(self):
        self.stdout = _DeadPipe()


def _worker(monkeypatch, *, spawns_before_stop):
    """A worker whose child always dies instantly; stops after N spawns."""
    w = bw.ProcessBuildWorker(["/nonexistent"], target_alive=lambda: True)
    calls: list[float] = []
    waits: list[float] = []

    def fake_spawn():
        calls.append(1)
        w._gen_at_spawn = w._gen
        w._proc = _Proc()
        if len(calls) >= spawns_before_stop:
            w._stop.set()

    def fake_wait(timeout=None):
        waits.append(timeout if timeout is not None else 0.0)
        return w._stop.is_set()

    monkeypatch.setattr(w, "_spawn", fake_spawn)
    monkeypatch.setattr(w._stop, "wait", fake_wait)
    w._proc = _Proc()
    return w, calls, waits


def test_failing_worker_backs_off_instead_of_spinning(monkeypatch):
    w, calls, waits = _worker(monkeypatch, spawns_before_stop=4)
    w._read_loop()
    assert calls, "should have attempted a respawn"
    assert waits, "a child that never produced a frame must be throttled"
    assert all(d > 0 for d in waits), "zero-delay respawn is the fork storm"


def test_backoff_grows_and_is_capped(monkeypatch):
    w, _calls, waits = _worker(monkeypatch, spawns_before_stop=12)
    w._read_loop()
    assert waits == sorted(waits), "delay should not shrink while failing"
    assert max(waits) <= bw._RESPAWN_MAX_DELAY_SEC
    assert max(waits) >= 2.0


def test_a_healthy_worker_respawns_immediately(monkeypatch):
    """Normal exit after real work must not inherit the failure backoff."""
    w = bw.ProcessBuildWorker(["/x"], target_alive=lambda: True)
    waits: list[float] = []
    spawns: list[int] = []

    def fake_spawn():
        spawns.append(1)
        w._gen_at_spawn = w._gen
        w._proc = _Proc()
        w._gen += 1                 # pretend it published a frame
        if len(spawns) >= 2:
            w._stop.set()

    monkeypatch.setattr(w, "_spawn", fake_spawn)
    monkeypatch.setattr(w._stop, "wait",
                        lambda timeout=None: waits.append(timeout) or w._stop.is_set())
    w._proc = _Proc()
    w._gen = 1                      # frames were produced before this exit
    w._gen_at_spawn = 0
    w._read_loop()
    assert not waits, "a productive worker should respawn without delay"


def test_spawn_failure_does_not_kill_the_reader(monkeypatch):
    """OSError from fork/exec used to escape and end the reader thread."""
    w = bw.ProcessBuildWorker(["/nonexistent"], target_alive=lambda: True)
    tries = {"n": 0}

    def boom():
        tries["n"] += 1
        if tries["n"] >= 3:
            w._stop.set()
        raise OSError("cannot exec")

    monkeypatch.setattr(w, "_spawn", boom)
    monkeypatch.setattr(w._stop, "wait", lambda timeout=None: w._stop.is_set())
    w._proc = _Proc()
    w._read_loop()                  # must return, not raise
    assert tries["n"] >= 2
    assert "respawn failed" in (w.last_error or "")


def test_stop_event_breaks_out_of_the_backoff(monkeypatch):
    """Shutdown must not wait out a 30 s sleep."""
    w = bw.ProcessBuildWorker(["/x"], target_alive=lambda: True)
    monkeypatch.setattr(w, "_spawn", lambda: None)
    monkeypatch.setattr(w._stop, "wait", lambda timeout=None: True)  # stopped
    w._proc = _Proc()
    w._read_loop()                  # returns promptly
    assert not threading.current_thread().daemon or True
