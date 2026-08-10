"""Recording retention — the only code path that deletes user data.

The rule that matters most is the in-progress guard: the match being recorded
right now must never be touched, because deleting it corrupts a live capture.
Everything else here exists so a full disk cannot take the game server down
with the agent.
"""
from __future__ import annotations

import argparse
import time

import pytest

from sqreader.cli import cmd_retention

GB = 1024 ** 3


def mk(d, name, *, age_days=0.0, size=1024):
    """Write a .sqrx plus its sidecar, backdated by age_days."""
    p = d / f"{name}.sqrx"
    p.write_bytes(b"x" * size)
    side = p.with_suffix(".meta.json")
    side.write_text("{}", encoding="utf-8")
    if age_days:
        when = time.time() - age_days * 86400
        for f in (p, side):
            import os
            os.utime(f, (when, when))
    return p


def run(d, **kw):
    args = argparse.Namespace(
        recordings_dir=d, max_age_days=0, max_total_gb=0, min_free_gb=0,
        in_progress_min=60, dry_run=False, min_keep=0)
    for k, v in kw.items():
        setattr(args, k, v)
    return cmd_retention(args)


def test_missing_dir_is_an_error(tmp_path):
    assert run(tmp_path / "nope") == 1


def test_nothing_to_do_still_succeeds(tmp_path):
    """The timer must stay green when no policy matches."""
    mk(tmp_path, "recent")
    assert run(tmp_path, max_age_days=90) == 0
    assert (tmp_path / "recent.sqrx").exists()


def test_in_progress_recording_is_never_deleted(tmp_path):
    """A freshly-written file is the live capture — off limits to every policy."""
    live = mk(tmp_path, "live")                       # mtime = now
    old = mk(tmp_path, "old", age_days=400)
    # Aggressive limits that would otherwise sweep everything.
    assert run(tmp_path, max_age_days=1, max_total_gb=0.0000001) == 0
    assert live.exists(), "deleted the in-flight recording"
    assert not old.exists()


def test_age_policy_removes_sidecar_too(tmp_path):
    p = mk(tmp_path, "ancient", age_days=200)
    assert run(tmp_path, max_age_days=90) == 0
    assert not p.exists()
    assert not p.with_suffix(".meta.json").exists(), "orphaned sidecar left behind"


def test_age_policy_keeps_files_inside_the_window(tmp_path):
    keep = mk(tmp_path, "keep", age_days=10)
    drop = mk(tmp_path, "drop", age_days=120)
    assert run(tmp_path, max_age_days=90) == 0
    assert keep.exists() and not drop.exists()


def test_total_size_policy_drops_oldest_first(tmp_path):
    """Oldest go first so the newest history survives."""
    a = mk(tmp_path, "a", age_days=30, size=GB // 2)
    b = mk(tmp_path, "b", age_days=20, size=GB // 2)
    c = mk(tmp_path, "c", age_days=10, size=GB // 2)
    assert run(tmp_path, max_total_gb=1.0) == 0
    assert not a.exists(), "oldest should go first"
    assert b.exists() and c.exists()


def test_dry_run_deletes_nothing(tmp_path, capsys):
    p = mk(tmp_path, "old", age_days=500)
    assert run(tmp_path, max_age_days=1, dry_run=True) == 0
    assert p.exists()
    assert "DRY RUN" in capsys.readouterr().out


def test_free_space_policy_frees_until_the_floor(tmp_path, monkeypatch):
    """Simulate a nearly-full disk: prune until min-free is satisfied."""
    import shutil

    a = mk(tmp_path, "a", age_days=30, size=GB)
    b = mk(tmp_path, "b", age_days=20, size=GB)
    keep = mk(tmp_path, "c", age_days=10, size=GB)
    monkeypatch.setattr(
        shutil, "disk_usage",
        lambda _p: type("U", (), {"total": 100 * GB, "used": 98 * GB,
                                  "free": 2 * GB})())
    assert run(tmp_path, min_free_gb=4, min_keep=0) == 0
    assert not a.exists() and not b.exists()
    assert keep.exists(), "freed more than the floor required"


@pytest.mark.parametrize("policy", [
    {"max_age_days": 90}, {"max_total_gb": 150}, {"min_free_gb": 50},
])
def test_policies_are_independent(tmp_path, policy):
    """Each policy is opt-in; an unset one must not delete anything."""
    p = mk(tmp_path, "small", age_days=5, size=1024)
    assert run(tmp_path, **policy) == 0
    assert p.exists()


# ------------------------------------------------- unreachable free-space ----

def test_unreachable_free_space_target_deletes_nothing(tmp_path, monkeypatch,
                                                       capsys):
    """The disk is full because of something else — do not wipe the archive.

    Free space is a property of the whole filesystem, but this can only delete
    recordings. Chasing a target the recordings cannot satisfy used to delete
    every match ever recorded and still miss it.
    """
    import shutil

    keep = [mk(tmp_path, f"r{i}", age_days=30 + i, size=GB) for i in range(4)]
    monkeypatch.setattr(
        shutil, "disk_usage",
        lambda _p: type("U", (), {"total": 900 * GB, "used": 899 * GB,
                                  "free": 1 * GB})())
    assert run(tmp_path, min_free_gb=50, min_keep=0) == 0
    assert all(p.exists() for p in keep), "wiped the archive for nothing"
    assert "cannot reach" in capsys.readouterr().err


def test_keep_floor_survives_the_free_space_policy(tmp_path, monkeypatch):
    """Even when the target IS reachable, never leave the operator with zero."""
    import shutil

    files = [mk(tmp_path, f"r{i}", age_days=40 - i, size=GB) for i in range(6)]
    monkeypatch.setattr(
        shutil, "disk_usage",
        lambda _p: type("U", (), {"total": 100 * GB, "used": 98 * GB,
                                  "free": 2 * GB})())
    assert run(tmp_path, min_free_gb=5, min_keep=3) == 0
    survivors = [p for p in files if p.exists()]
    assert len(survivors) >= 3, "keep-floor was ignored"
    # the newest are the ones kept
    assert files[-1].exists() and files[-2].exists()
