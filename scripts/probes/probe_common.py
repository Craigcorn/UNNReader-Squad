"""Shared attach plumbing for the probes in this directory.

A probe is a per-test capture harness (tracker table T): one script, one
question, read-only against the live server. This module is the dozen
lines every probe was retyping — find the server, attach, resolve the two
reflection anchors the same way the reader itself does (cache + discovery,
never the KNOWN_* constants) — so a probe is only its question-specific
code.

Probes run as scripts (`.venv/bin/python scripts/probes/<name>.py` on the
box), never imported by the reader; running them that way puts this
directory on sys.path, so a sibling imports `probe_common` directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqreader import addrcache
from sqreader.cli import _resolve_fname_pool, _resolve_gobjects
from sqreader.config import find_squad_server_pid
from sqreader.mem import ProcessMemory


@dataclass
class Attached:
    """Open read-only handles on the live server process."""

    pid: int
    pm: ProcessMemory
    arr: Any
    alloc: Any


def attach() -> Attached:
    pid = find_squad_server_pid()
    pm = ProcessMemory(pid)
    bid = addrcache.binary_identity(pid)
    return Attached(pid=pid, pm=pm,
                    arr=_resolve_gobjects(pm, bid),
                    alloc=_resolve_fname_pool(pm, bid))


def attach_with_paths() -> tuple[Attached, Any]:
    """attach() plus the reader's own resolved SnapshotPaths — for probes
    that want the offsets actually in use (autoresolve applied, a served
    pack respected) rather than raw module constants. Costs one class-
    layout walk at start; a tight-loop tracker that manages its own
    reflection can stay with plain attach()."""
    a = attach()
    from sqreader.squad.snapshot import resolve_paths
    return a, resolve_paths(a.pm, a.arr, a.alloc)
