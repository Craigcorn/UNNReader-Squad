"""Installing a staged release: the binary swap, the smoke gate, the rollback
copy, and the boot-confirmation state machine.

The point of these tests is the FAILURE paths. A self-update that works is
worth little; one that cannot brick a box is worth the whole feature. So most
of what is asserted here is what happens when the new binary is corrupt, does
not execute, or executes once and then never comes up again.

Three tests need the candidate to REALLY execute, and they are POSIX-only —
not out of laziness but because the swap deliberately stages the candidate as
`<name>.new`, and Windows will not run a file with that extension. The target
platform is Linux, where the shipped artifact has no extension at all. Marking
them skipped is more honest than a Windows variant that would "pass" by failing
to execute, which is indistinguishable from the failure the gate exists to
catch.

Everything around them — which decision is taken, and what is left on disk —
is exercised on both platforms by stubbing the smoke test.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from sqreader import cli, updater

_WIN = os.name == "nt"
posix_only = pytest.mark.skipif(
    _WIN, reason="the staged candidate is named .new, which Windows cannot run")


def _exe(path: Path, marker: str, *, code: int = 0) -> Path:
    """A tiny executable standing in for an agent binary.

    `marker` is echoed so a test can tell which binary is currently in place;
    `code` is its exit status, which is what the smoke gate actually judges.
    """
    if _WIN:
        path = path.with_suffix(".bat")
        path.write_text(f"@echo {marker}\r\n@exit /b {code}\r\n",
                        encoding="ascii")
    else:
        path.write_text(f"#!/bin/sh\necho {marker}\nexit {code}\n",
                        encoding="utf-8")
        os.chmod(path, 0o755)
    return path


def _stage(tmp_path: Path, data: bytes, version: str = "9.9.9",
           *, sha: str | None = None) -> Path:
    """Put `data` through the real staging path so the marker is authentic."""
    if sha is None:
        sha = hashlib.sha256(data).hexdigest()
    updater.stage(tmp_path, version, "sqreader-9.9.9-linux-x86_64", data,
                  sha256=sha)
    return tmp_path


def _marker(state_dir: Path) -> dict:
    return json.loads(
        (state_dir / updater._STAGED_MARKER).read_text("utf-8"))


# --------------------------------------------------------------- swap_binary

@posix_only
def test_swap_replaces_and_keeps_a_rollback_copy(tmp_path):
    target = _exe(tmp_path / "sqreader", "OLD")
    src = _exe(tmp_path / "new", "NEW")

    ok, _ = updater.swap_binary(src, target)

    assert ok
    assert "NEW" in target.read_text()
    # The outgoing binary is the only way back if the new one is bad.
    assert "OLD" in target.with_name(target.name + ".bak").read_text()
    assert os.access(target, os.X_OK)


@posix_only
def test_swap_refuses_a_candidate_that_will_not_run(tmp_path):
    target = _exe(tmp_path / "sqreader", "OLD")
    src = _exe(tmp_path / "new", "NEW", code=3)

    ok, msg = updater.swap_binary(src, target)

    assert not ok and "refused" in msg
    # Refusing must be inert: the agent that was there is untouched, and no
    # half-written candidate is left lying beside it.
    assert "OLD" in target.read_text()
    assert not target.with_name(target.name + ".new").exists()


def test_swap_refuses_a_sha_mismatch(tmp_path):
    target = tmp_path / "sqreader"
    target.write_bytes(b"old")
    src = tmp_path / "new"
    src.write_bytes(b"tampered")

    ok, msg = updater.swap_binary(src, target, sha256="0" * 64)

    assert not ok and "sha256 mismatch" in msg
    assert target.read_bytes() == b"old"


def test_swap_reports_a_vanished_artifact(tmp_path):
    ok, msg = updater.swap_binary(tmp_path / "gone", tmp_path / "sqreader")
    assert not ok and "gone" in msg


@posix_only
def test_rollback_restores_the_previous_binary(tmp_path):
    target = _exe(tmp_path / "sqreader", "OLD")
    src = _exe(tmp_path / "new", "NEW")
    updater.swap_binary(src, target)
    assert "NEW" in target.read_text()

    ok, _ = updater.rollback_binary(target)

    assert ok and "OLD" in target.read_text()


def test_rollback_without_a_backup_is_an_honest_failure(tmp_path):
    target = tmp_path / "sqreader"
    target.write_bytes(b"x")
    ok, msg = updater.rollback_binary(target)
    assert not ok and "no rollback copy" in msg


# ------------------------------------------------------- marker bookkeeping

def test_mark_applied_keeps_the_marker_alive(tmp_path):
    """The swap must NOT clear the marker.

    Clearing at install time is what the first implementation did, and it means
    a release that crash-loops leaves no evidence it was ever attempted — the
    box is bricked and nothing on disk says why.
    """
    _stage(tmp_path, b"payload")
    updater.mark_applied(tmp_path)

    m = _marker(tmp_path)
    assert m["applied_at"] > 0 and m["attempts"] == 0
    assert m["version"] == "9.9.9"


def test_boot_attempts_accumulate(tmp_path):
    _stage(tmp_path, b"payload")
    updater.mark_applied(tmp_path)
    assert [updater.note_boot_attempt(tmp_path) for _ in range(3)] == [1, 2, 3]


def test_stage_records_the_pinned_sha(tmp_path):
    """The download verified the bytes; the install runs in another process,
    possibly days later, and has to be able to check them again itself."""
    _stage(tmp_path, b"payload")
    assert _marker(tmp_path)["sha256"] == hashlib.sha256(b"payload").hexdigest()


# ------------------------------------------- cmd_apply_staged_update wiring

class _Args:
    def __init__(self, state_dir):
        self.state_dir, self.stats_db = str(state_dir), None


def _no_binary(monkeypatch, target=None):
    monkeypatch.setattr(updater, "running_binary", lambda **kw: target)


def test_apply_is_silent_when_nothing_is_staged(tmp_path, monkeypatch, capsys):
    _no_binary(monkeypatch)
    assert cli.cmd_apply_staged_update(_Args(tmp_path)) == 0
    assert capsys.readouterr().err == ""


def test_apply_clears_a_stage_on_a_source_checkout(tmp_path, monkeypatch):
    """`running_binary()` is None from a source checkout. There is nothing to
    swap, and leaving the marker would re-trigger this on every single boot."""
    _stage(tmp_path, b"payload")
    _no_binary(monkeypatch)

    assert cli.cmd_apply_staged_update(_Args(tmp_path)) == 0
    assert updater.staged(tmp_path) is None


def test_apply_swaps_then_marks_applied(tmp_path, monkeypatch):
    target = tmp_path / "sqreader"
    target.write_bytes(b"old")
    _stage(tmp_path, b"new-binary")
    _no_binary(monkeypatch, target)
    monkeypatch.setattr(updater, "_smoke_ok", lambda p: (True, "sqreader 9.9.9"))

    assert cli.cmd_apply_staged_update(_Args(tmp_path)) == 0
    assert target.read_bytes() == b"new-binary"
    assert (tmp_path / "sqreader.bak").read_bytes() == b"old"
    # Still staged — the release is installed but NOT yet confirmed working.
    assert updater.staged(tmp_path)["applied_at"] > 0


def test_apply_rolls_back_after_max_boot_attempts(tmp_path, monkeypatch):
    target = tmp_path / "sqreader"
    target.write_bytes(b"old")
    _stage(tmp_path, b"new-binary")
    _no_binary(monkeypatch, target)
    monkeypatch.setattr(updater, "_smoke_ok", lambda p: (True, ""))

    cli.cmd_apply_staged_update(_Args(tmp_path))          # swap
    assert target.read_bytes() == b"new-binary"

    # Each boot re-runs ExecStartPre. `serve` never confirmed, so the marker
    # still says applied — that is the crash-loop signature.
    for _ in range(updater.MAX_BOOT_ATTEMPTS):
        cli.cmd_apply_staged_update(_Args(tmp_path))
        assert target.read_bytes() == b"new-binary", "rolled back too eagerly"

    cli.cmd_apply_staged_update(_Args(tmp_path))
    assert target.read_bytes() == b"old"
    assert updater.staged(tmp_path) is None, "a rolled-back release must not retry"


def test_apply_drops_a_release_it_refuses_to_install(tmp_path, monkeypatch):
    """A candidate that fails the smoke gate is discarded, not retried forever:
    the same bytes will fail the same way on every boot."""
    target = tmp_path / "sqreader"
    target.write_bytes(b"old")
    _stage(tmp_path, b"broken")
    _no_binary(monkeypatch, target)
    monkeypatch.setattr(updater, "_smoke_ok", lambda p: (False, "segfault"))

    assert cli.cmd_apply_staged_update(_Args(tmp_path)) == 1
    assert target.read_bytes() == b"old"
    assert updater.staged(tmp_path) is None


def test_a_refused_release_is_never_offered_again(tmp_path, monkeypatch):
    """The loop this prevents: refuse v9.9.9, forget it, accept v9.9.9 again on
    the next check-in, exit to apply it, refuse it again — one restart per
    check-in interval, on every agent in the fleet, from one bad build."""
    target = tmp_path / "sqreader"
    target.write_bytes(b"old")
    _stage(tmp_path, b"broken")
    _no_binary(monkeypatch, target)
    monkeypatch.setattr(updater, "_smoke_ok", lambda p: (False, "segfault"))

    cli.cmd_apply_staged_update(_Args(tmp_path))

    assert updater.staged(tmp_path) is None          # no work pending
    # ...but the version is still on record, and that is what the downgrade
    # guard is given as `min_applied`, so 9.9.9 can never be accepted again.
    assert updater.staged_version(tmp_path) == "9.9.9"
    assert not updater.is_newer("9.9.9", updater.staged_version(tmp_path))


def test_a_rolled_back_release_is_never_offered_again(tmp_path, monkeypatch):
    target = tmp_path / "sqreader"
    target.write_bytes(b"old")
    _stage(tmp_path, b"new-binary")
    _no_binary(monkeypatch, target)
    monkeypatch.setattr(updater, "_smoke_ok", lambda p: (True, ""))

    for _ in range(updater.MAX_BOOT_ATTEMPTS + 2):
        cli.cmd_apply_staged_update(_Args(tmp_path))

    assert target.read_bytes() == b"old"
    assert updater.staged(tmp_path) is None
    assert updater.staged_version(tmp_path) == "9.9.9"


def test_refusing_deletes_the_downloaded_artifact(tmp_path, monkeypatch):
    """A ~40 MB binary that will never be installed has no business staying on
    a game server's disk."""
    _stage(tmp_path, b"broken")
    art = Path(updater.staged(tmp_path, include_refused=True)["path"])
    assert art.is_file()

    updater.mark_refused(tmp_path, "nope")

    assert not art.exists()


# ------------------------------------------------------- boot confirmation

def test_confirm_boot_is_quiet_when_nothing_was_applied(tmp_path):
    assert updater.confirm_boot(tmp_path, "1.3.0") is None
    _stage(tmp_path, b"payload")                    # staged, not yet applied
    assert updater.confirm_boot(tmp_path, "1.3.0") is None


def test_confirm_boot_clears_the_marker_when_the_new_version_comes_up(tmp_path):
    _stage(tmp_path, b"payload")
    art = Path(updater.staged(tmp_path)["path"])
    updater.mark_applied(tmp_path)

    msg = updater.confirm_boot(tmp_path, "9.9.9")

    assert msg and "up and running" in msg
    assert updater.staged(tmp_path) is None
    assert not art.exists(), "a confirmed artifact is dead weight on a game server"


def test_confirm_boot_keeps_the_marker_when_the_wrong_version_is_running(tmp_path):
    """The swap said it worked and the old binary is still what booted. Keeping
    the marker is what lets the boot counter escalate this to a rollback."""
    _stage(tmp_path, b"payload")
    updater.mark_applied(tmp_path)

    msg = updater.confirm_boot(tmp_path, "1.3.0")

    assert msg and "expected v9.9.9" in msg
    assert updater.staged(tmp_path)["applied_at"] > 0


def test_a_confirmed_release_is_not_treated_as_refused(tmp_path):
    """After confirmation the marker is gone, so `min_applied` is empty and the
    downgrade guard falls back to the running version — which is now the new
    one. A stale marker here would block every future release."""
    _stage(tmp_path, b"payload")
    updater.mark_applied(tmp_path)
    updater.confirm_boot(tmp_path, "9.9.9")
    assert updater.staged_version(tmp_path) == ""


# --------------------------------------------------- which binary to replace
#
# The bug these cover shipped once. `running_binary` decided "am I compiled?"
# by testing sys.frozen, which Nuitka never sets, so the packaged agent
# reported "source checkout - nothing to swap" and remote update did nothing
# at all. Nothing here can simulate a real onefile; what these pin down is the
# contract that made the bug possible - the answer comes from the caller, and
# the path comes from argv[0], not from sys.executable.

def test_source_checkout_has_nothing_to_swap():
    assert updater.running_binary(compiled=False) is None


def test_compiled_build_resolves_its_own_argv0(tmp_path, monkeypatch):
    exe = tmp_path / "sqreader"
    exe.write_bytes(b"binary")
    monkeypatch.delenv("NUITKA_ONEFILE_BINARY", raising=False)
    monkeypatch.setattr(sys, "argv", [str(exe), "apply-staged-update"])

    assert updater.running_binary(compiled=True) == exe.resolve()


def test_nuitka_env_wins_when_the_runtime_provides_it(tmp_path, monkeypatch):
    real = tmp_path / "sqreader"
    real.write_bytes(b"binary")
    monkeypatch.setenv("NUITKA_ONEFILE_BINARY", str(real))
    monkeypatch.setattr(sys, "argv", ["/nonexistent/elsewhere"])

    assert updater.running_binary(compiled=True) == real.resolve()


@posix_only
def test_a_symlinked_invocation_replaces_the_real_file(tmp_path, monkeypatch):
    """install.sh puts /usr/local/bin/sqreader on PATH as a symlink. Replacing
    the symlink would break it and leave the actual agent untouched."""
    real = tmp_path / "opt" / "sqreader"
    real.parent.mkdir()
    real.write_bytes(b"binary")
    link = tmp_path / "bin-sqreader"
    link.symlink_to(real)
    monkeypatch.delenv("NUITKA_ONEFILE_BINARY", raising=False)
    monkeypatch.setattr(sys, "argv", [str(link)])

    assert updater.running_binary(compiled=True) == real.resolve()


def test_a_bare_name_is_looked_up_on_path(tmp_path, monkeypatch):
    exe = _exe(tmp_path / "sqreader", "OK")
    monkeypatch.delenv("NUITKA_ONEFILE_BINARY", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(sys, "argv", [exe.name])

    assert updater.running_binary(compiled=True) == exe.resolve()


def test_an_unresolvable_argv0_is_not_guessed_at(monkeypatch):
    monkeypatch.delenv("NUITKA_ONEFILE_BINARY", raising=False)
    monkeypatch.setattr(sys, "argv", ["/no/such/sqreader"])
    assert updater.running_binary(compiled=True) is None
