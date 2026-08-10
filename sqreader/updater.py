"""Agent code self-update client (Phase 2).

Fetches a SIGNED release manifest from central, verifies it (Ed25519 + our
platform + it is actually NEWER than the running version + a downgrade guard),
downloads the artifact and verifies its sha256, then STAGES it to disk.

The install is a deliberate SEPARATE step, in a SEPARATE process. The running
reader never replaces itself mid-run: it waits for a moment with no match in
progress, then exits, and systemd (`Restart=always`) starts it again. The unit's
`ExecStartPre` runs `sqreader apply-staged-update` in the gap between the two
processes — the only instant the binary is not executing, which is why the swap
cannot hit ETXTBSY and why no match can be split by it.

That step is also where the safety net lives: the staged binary is sha-checked
again, smoke-tested (`version`) before it is allowed to become the agent, and
the old one is kept beside it as `.bak`. The staging marker survives the install
and is only cleared when the NEW binary reaches `serve` and reports its own
version — so a release that will not boot is counted (`MAX_BOOT_ATTEMPTS`) and
rolled back rather than crash-looping forever.

Config `update_enabled` (default true, inert unless enrolled). Verification is
fail-closed
and mirrors the offset-pack path: a leaked per-agent transport secret cannot
forge a release, because the manifest is Ed25519-signed under the OFFLINE key and
the artifact is sha256-pinned by that signed manifest.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__, ingest_client, update_sign, update_trust

_STAGED_MARKER = "pending_release.json"
_ARTIFACT_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")


def platform_name() -> str:
    """The platform token central routes releases by ("linux" on the servers)."""
    return "linux" if platform.system() == "Linux" else platform.system().lower()


def _ver_tuple(v: str) -> tuple[int, ...]:
    """Best-effort numeric version tuple for comparison. Non-numeric suffixes on a
    part (e.g. "1.2.0rc1") stop at the first non-digit; empty parts count as 0, so
    an odd version never raises — it just sorts conservatively."""
    out: list[int] = []
    for part in str(v).split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_newer(candidate: str, current: str) -> bool:
    return _ver_tuple(candidate) > _ver_tuple(current)


def accept(manifest: Any, *, current_version: str, min_applied: str,
           this_platform: str) -> dict[str, Any] | None:
    """Verify a fetched manifest. Returns ``{version, artifact, sha256}`` iff it is
    trusted (valid Ed25519 signature), for OUR platform, strictly NEWER than the
    running version, and newer than the highest version already staged (downgrade
    guard). Fail-closed: any problem → None (never raises)."""
    if not isinstance(manifest, dict):
        return None
    if str(manifest.get("platform") or "linux") != this_platform:
        return None
    version = str(manifest.get("version") or "")
    if not version or not is_newer(version, current_version):
        return None
    if min_applied and not is_newer(version, min_applied):
        return None
    artifact = str(manifest.get("artifact") or "")
    sha = str(manifest.get("sha256") or "")
    if not _ARTIFACT_RE.fullmatch(artifact) or len(sha) != 64:
        return None
    if not update_sign.verify(update_trust.EMBEDDED_ED25519_PUBKEY_B64, manifest):
        return None
    return {"version": version, "artifact": artifact, "sha256": sha}


def fetch_and_accept(creds: dict[str, str], channel: str, *, seq: int,
                     timeout: float = 15.0,
                     min_applied: str = "") -> dict[str, Any] | None:
    """Fetch central's current manifest for our platform+channel and verify it."""
    try:
        manifest = ingest_client.fetch_manifest(creds, platform_name(), channel,
                                                 seq=seq, timeout=timeout)
    except ingest_client.PushError:
        return None
    return accept(manifest, current_version=__version__, min_applied=min_applied,
                  this_platform=platform_name())


def download_verified(creds: dict[str, str], artifact: str, sha256: str, *,
                      timeout: float = 300.0) -> bytes | None:
    """Download the artifact and verify its sha256. None on any failure."""
    try:
        data = ingest_client.download_artifact(creds, artifact, timeout=timeout)
    except ingest_client.PushError:
        return None
    if not data or hashlib.sha256(data).hexdigest() != sha256:
        return None
    return data


# -- staging: the verified artifact waits for the explicit apply step ------

def stage(state_dir: str | Path, version: str, artifact: str, data: bytes,
          *, sha256: str = "") -> Path:
    """Write the verified artifact + a pending marker under state_dir/staged/.
    Returns the staged artifact path. Install is a separate explicit step so the
    live reader is never replaced mid-run."""
    d = Path(state_dir) / "staged"
    d.mkdir(parents=True, exist_ok=True)
    art_path = d / artifact
    tmp = art_path.with_name(art_path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(art_path)
    marker = Path(state_dir) / _STAGED_MARKER
    mtmp = marker.with_name(marker.name + ".tmp")
    # sha256 is carried so the install step can re-check the bytes it is
    # about to make the agent — the download check happened in another
    # process, possibly days earlier.
    mtmp.write_text(json.dumps({"version": version, "artifact": artifact,
                                "path": str(art_path), "sha256": sha256},
                               separators=(",", ":")), encoding="utf-8")
    mtmp.replace(marker)
    return art_path


def mark_refused(state_dir: str | Path, reason: str) -> None:
    """Remember that a version was tried and would not do.

    Deleting the marker instead — the obvious move — creates a slow restart
    loop: nothing then records that v9.9.9 was refused, so the next check-in
    accepts it again, stages it again, and the agent exits to apply it again,
    forever, once per check-in interval. `staged_version` still reports the
    refused version, and it is what feeds `min_applied`, so the downgrade guard
    now does double duty as a "do not offer me that one again" list.
    """
    d = staged(state_dir, include_refused=True) or {}
    d.pop("applied_at", None)
    d.pop("attempts", None)
    d["refused"] = reason[:200]
    path = d.pop("path", None)
    _write_marker(state_dir, d)
    if path:                       # the bytes are of no further use
        try:
            Path(path).unlink()
        except OSError:
            pass


def staged(state_dir: str | Path, *,
           include_refused: bool = False) -> dict[str, Any] | None:
    """The pending staged release ``{version, artifact, path, sha256}``, or None.

    A REFUSED marker reads as "nothing pending" — there is no work left to do
    for it — while still being on disk for `staged_version` to report.
    """
    try:
        d = json.loads((Path(state_dir) / _STAGED_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    if d.get("refused") and not include_refused:
        return None
    return d


def staged_version(state_dir: str | Path) -> str:
    """Highest version already handled — staged, applied, OR refused.

    Feeds `min_applied`, so a version that has been tried and rejected is never
    offered again. That is the whole reason a refused marker is kept.
    """
    d = staged(state_dir, include_refused=True)
    return str(d.get("version")) if d else ""


def clear_staged(state_dir: str | Path) -> None:
    try:
        (Path(state_dir) / _STAGED_MARKER).unlink()
    except OSError:
        pass


# -- applying: swap the binary, and be able to take it back ------------------
#
# The staged artifact is the SAME KIND of thing that is already running: a
# single self-contained executable. Installing it is a file swap, not a package
# install — the previous implementation ran `pip install` on it, which cannot
# work on a Nuitka onefile and never did.
#
# Everything here is written so that a failure leaves a WORKING agent behind.
# The order is: prove the new binary runs, keep the old one, swap atomically,
# and do not forget any of it until the new version has actually come up.

BACKUP_SUFFIX = ".bak"
# Consecutive boots that may fail before the swap is treated as a bad release.
# Two, not one: a single failure can be the game server not being up yet.
MAX_BOOT_ATTEMPTS = 2


def running_binary(*, compiled: bool) -> Path | None:
    """The executable to replace, or None when there is nothing to swap.

    `compiled` is passed in rather than worked out here, because the codebase
    already has exactly one answer to "am I a compiled build" — `cli._is_compiled`
    — and this function was born broken by inventing a second one. It tested
    `sys.frozen`, which is a PyInstaller convention that Nuitka does not set, so
    on a packaged agent it returned None and the install step cheerfully
    reported "source checkout — nothing to swap". The entire remote-update path
    would have done nothing on every box in the fleet, silently.

    That was caught by running the real artifact on Linux, not by a test: no
    unit test can fake a Nuitka onefile, which is exactly why the detection is
    borrowed from the one place that is already proven in production (`version`
    prints "compiled") instead of being re-derived.

    The path itself: NUITKA_ONEFILE_BINARY is the authoritative answer when the
    runtime provides it, but this build does not, so argv[0] is the real
    source — it is the path the unit invoked. `sys.executable` is NOT usable
    here: inside a onefile it points into a throwaway extraction directory that
    will not exist next boot (see cli._worker_prefix, bitten by this before).
    """
    if not compiled:
        return None                  # source checkout: nothing to swap
    cand = os.environ.get("NUITKA_ONEFILE_BINARY") or sys.argv[0]
    if not cand:
        return None
    p = Path(cand)
    if not p.is_absolute():
        found = shutil.which(cand)   # invoked by bare name off PATH
        if found:
            p = Path(found)
    try:
        # Resolve the /usr/local/bin/sqreader symlink the installer creates:
        # replacing the symlink itself would break it and leave the real
        # binary untouched.
        p = p.resolve()
    except OSError:
        return None
    return p if p.is_file() else None


def _smoke_ok(binary: Path) -> tuple[bool, str]:
    """Does the candidate actually run? Cheapest possible proof, before it is
    allowed to become the agent."""
    try:
        r = subprocess.run([str(binary), "version"], capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"cannot execute: {e!r}"
    if r.returncode != 0:
        return False, f"`version` exited {r.returncode}: {(r.stderr or '')[:120]}"
    return True, (r.stdout or "").strip()[:80]


def swap_binary(staged_path: str | Path, target: str | Path, *,
                sha256: str = "") -> tuple[bool, str]:
    """Replace `target` with `staged_path`. Returns (ok, message).

    Never leaves the target missing: the new file is staged BESIDE it on the
    same filesystem and moved into place with os.replace, which is atomic and —
    unlike writing over the file — is legal while the old binary is executing.
    """
    src, dst = Path(staged_path), Path(target)
    if not src.is_file():
        return False, "staged artifact is gone"
    if sha256:
        h = hashlib.sha256(src.read_bytes()).hexdigest()
        if h != sha256:
            return False, f"sha256 mismatch: {h[:12]} != {sha256[:12]}"

    beside = dst.with_name(dst.name + ".new")
    try:
        shutil.copyfile(src, beside)
        os.chmod(beside, 0o755)
    except OSError as e:
        return False, f"cannot stage beside the target: {e!r}"

    ok, detail = _smoke_ok(beside)
    if not ok:
        beside.unlink(missing_ok=True)
        return False, f"candidate refused: {detail}"

    backup = dst.with_name(dst.name + BACKUP_SUFFIX)
    try:
        shutil.copyfile(dst, backup)
        os.chmod(backup, 0o755)
    except OSError as e:
        beside.unlink(missing_ok=True)
        return False, f"cannot keep a rollback copy: {e!r}"

    try:
        os.replace(beside, dst)
    except OSError as e:
        beside.unlink(missing_ok=True)
        return False, f"swap failed: {e!r}"
    return True, detail


def rollback_binary(target: str | Path) -> tuple[bool, str]:
    """Put the previous binary back. Used when a new one will not come up."""
    dst = Path(target)
    backup = dst.with_name(dst.name + BACKUP_SUFFIX)
    if not backup.is_file():
        return False, "no rollback copy to restore"
    try:
        beside = dst.with_name(dst.name + ".restore")
        shutil.copyfile(backup, beside)
        os.chmod(beside, 0o755)
        os.replace(beside, dst)
    except OSError as e:
        return False, f"restore failed: {e!r}"
    return True, "previous binary restored"


def mark_applied(state_dir: str | Path) -> None:
    """Record that the swap happened but has NOT yet been seen to work.

    The marker deliberately outlives the swap. Clearing it at install time —
    which is what the old flow did — means a new binary that crash-loops leaves
    nothing behind saying an update was ever attempted, and the box is bricked
    with no evidence.
    """
    d = staged(state_dir) or {}
    d["applied_at"] = int(time.time())
    d["attempts"] = 0
    _write_marker(state_dir, d)


def confirm_boot(state_dir: str | Path, running_version: str) -> str | None:
    """Called by `serve` at startup. Returns a line to log, or None.

    This is where a release is finally believed. Not the swap — the swap only
    proves the file could be replaced and that it prints a version string. The
    proof that matters is the new binary getting far enough into `serve` to say
    which version it is, and that can only be observed from inside the new
    process.
    """
    d = staged(state_dir)
    if not d or not d.get("applied_at"):
        return None
    want = str(d.get("version") or "")
    if want != running_version:
        # Not who we installed. The swap did not take, or something restored
        # the old binary underneath us. Leave the marker: the boot counter is
        # what turns this into a rollback instead of an endless mystery.
        return (f"[update] expected v{want} but this is v{running_version} — "
                f"leaving the marker for apply-staged-update to resolve")
    art = d.get("path")
    clear_staged(state_dir)
    if art:                      # a ~40 MB artifact with nothing left to do
        try:
            Path(str(art)).unlink()
        except OSError:
            pass
    return f"[update] v{running_version} is up and running"


def note_boot_attempt(state_dir: str | Path) -> int:
    """Count one boot of an applied-but-unconfirmed release. Returns the count."""
    d = staged(state_dir) or {}
    n = int(d.get("attempts") or 0) + 1
    d["attempts"] = n
    _write_marker(state_dir, d)
    return n


def _write_marker(state_dir: str | Path, data: dict[str, Any]) -> None:
    marker = Path(state_dir) / _STAGED_MARKER
    tmp = marker.with_name(marker.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        tmp.replace(marker)
    except OSError:
        pass
