"""
Serial-diff incremental GUObjectArray walk.

build_snapshot used to run its classify/collect body over every slot of
the object array every tick (~475k Python iterations on a full 100-player
server, ~95% of the tick). The (idx, serial) pair is a stable identity
for "the UObject currently in this slot" — the same invariant obj_class
already keys on — so a slot whose (addr, serial) both match last tick's
values classifies to the SAME contribution and doesn't need revisiting.

DeltaWalk keeps last tick's per-slot (addr, serial) columns as numpy
arrays plus a ledger of what each relevant slot contributed. Each tick
it diffs the fresh columns against the previous ones vectorized, runs the
caller's visit callback only over changed slots (plus any slots the
caller explicitly wants re-verified — see revisit_slices), and the caller
rebuilds its collection lists from the ledger in slot order.

Safety valves:
  * Rolling resync: each delta tick force-revisits the 1/resync_ticks
    slice of ALL slots congruent to the tick number, so every slot's
    contribution is re-derived at least once per resync_ticks ticks.
    (This replaced a periodic single-tick full re-walk, whose ~600 ms
    spike every interval capped the sustainable tick rate; spreading it
    matches what partial_invalidate already does for obj_class.) Callers
    set resync_ticks from the tick rate to make the window time-based.
  * revisit_slices lets SnapshotCaches.partial_invalidate keep its
    anti-poisoning guarantee: the slots whose obj_class entries were
    dropped this tick are re-visited (and therefore re-read) even though
    their (addr, serial) didn't change.
  * A genuine full walk still happens on the first tick and whenever the
    caller drops the state (SnapshotCaches.reset() — the recovery path,
    where the ledger itself is suspect and a one-off spike is the point).
  * full_walk_ledger() is the pure-Python reference implementation, used
    when numpy / the vectorized read is unavailable, for the
    SQREADER_WALK_VERIFY debug cross-check, and by the equivalence tests.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

try:
    import numpy as _np
except ImportError:  # callers fall back to full_walk_ledger()
    _np = None  # type: ignore[assignment]

# Contribution kinds. Every ledger entry is (kind, obj_addr, extra) where
# `extra` is the class_addr for kinds that collect (obj, class) pairs, the
# collector key for KIND_COLLECTOR, and 0 otherwise.
KIND_PLAYER = 1
KIND_SQUAD = 2
KIND_CAPZONE = 3
KIND_GAMESTATE = 4
KIND_VEHICLE = 5
KIND_MARKER = 6
KIND_DEPLOYABLE = 7
KIND_SPAWNER = 8
KIND_RALLY = 9
KIND_MARKER_MGR = 10
KIND_COLLECTOR = 11
KIND_AMMOWEP = 12
KIND_PROJECTILE = 13
KIND_LANE_INIT = 14
KIND_LANE_VIS = 15
KIND_SOLDIER = 16
KIND_SEAT = 17

Contribution = tuple[int, int, Any]
VisitFn = Callable[[int, int, int], "Contribution | None"]


def full_walk_ledger(records: Iterable[tuple[int, int, int]],
                     visit: VisitFn) -> dict[int, Contribution]:
    """Reference full walk: visit every record, return a fresh ledger."""
    ledger: dict[int, Contribution] = {}
    for idx, addr, serial in records:
        c = visit(idx, addr, serial)
        if c is not None:
            ledger[idx] = c
    return ledger


class DeltaWalk:
    """Cross-tick state for the incremental walk. One instance per
    SnapshotCaches; survives as long as the caches do."""

    DEFAULT_RESYNC_TICKS = 30

    __slots__ = ("prev_addrs", "prev_serials", "ledger", "tick",
                 "resync_ticks", "last_full_tick")

    def __init__(self, resync_ticks: int = DEFAULT_RESYNC_TICKS) -> None:
        self.prev_addrs: Any = None      # np.uint64[n] from last tick
        self.prev_serials: Any = None    # np.int32[n] from last tick
        self.ledger: dict[int, Contribution] = {}
        self.tick = 0               # builds since creation
        # Rolling-resync window: every slot is force-revisited once per
        # this many ticks. Callers pace it from the tick rate so the
        # window is a wall-clock bound, not a tick-count one.
        self.resync_ticks = max(1, resync_ticks)
        self.last_full_tick = -1

    def needs_full(self) -> bool:
        return self.prev_addrs is None

    def walk(self, addrs, serials, visit: VisitFn, *,
             revisit_slices: Iterable[tuple[int, int]] = (),
             revisit_idx: Iterable[int] = (),
             ) -> tuple[int, bool]:
        """Run one tick over fresh (addrs, serials) columns.

        visit(idx, addr, serial) -> Contribution | None runs for every
        changed / revisited slot; the ledger is updated in place.
        revisit_slices are (phase, window) strided slot ranges and
        revisit_idx individual slots to visit even if unchanged.
        Returns (slots_visited, was_full_walk).
        """
        n = len(addrs)
        ledger = self.ledger
        full = self.needs_full()
        if full:
            ledger.clear()
            vis = _np.flatnonzero(addrs)
            self.last_full_tick = self.tick
            for idx, addr, serial in zip(vis.tolist(),
                                         addrs[vis].tolist(),
                                         serials[vis].tolist(), strict=True):
                c = visit(idx, addr, serial)
                if c is not None:
                    ledger[idx] = c
        else:
            prev_a = self.prev_addrs
            prev_s = self.prev_serials
            pn = len(prev_a)
            if pn < n:
                # Array grew: unseen slots diff against (0, 0), so a
                # non-null newcomer is changed by definition.
                pa = _np.zeros(n, dtype=_np.uint64)
                pa[:pn] = prev_a
                ps = _np.zeros(n, dtype=_np.int32)
                ps[:pn] = prev_s
                prev_a, prev_s = pa, ps
            elif pn > n:
                # Array shrank: slots beyond the new end no longer exist;
                # their contributions must not survive the tick.
                prev_a = prev_a[:n]
                prev_s = prev_s[:n]
                for idx in [k for k in ledger if k >= n]:
                    del ledger[idx]
            mask = (addrs != prev_a) | (serials != prev_s)
            # Rolling resync: revisit this tick's 1/resync_ticks slice of
            # every slot, changed or not, so stale contributions (e.g. a
            # classification-rule change without a serial bump) can't
            # outlive the window — spread evenly instead of the old
            # periodic full-walk spike.
            rw = self.resync_ticks
            mask[self.tick % rw::rw] = True
            for phase, window in revisit_slices:
                if window > 0:
                    mask[phase % window::window] = True
            ri = [k for k in revisit_idx if 0 <= k < n]
            if ri:
                mask[ri] = True
            vis = _np.flatnonzero(mask)
            for idx, addr, serial in zip(vis.tolist(),
                                         addrs[vis].tolist(),
                                         serials[vis].tolist(), strict=True):
                ledger.pop(idx, None)  # old contribution is void either way
                if addr == 0:
                    continue
                c = visit(idx, addr, serial)
                if c is not None:
                    ledger[idx] = c
        self.prev_addrs = addrs
        self.prev_serials = serials
        self.tick += 1
        return len(vis), full

    def adopt(self, ledger: dict[int, Contribution]) -> None:
        """Install a ledger produced by full_walk_ledger() (the pure-Python
        fallback path). Drops the prev columns so the next vectorized tick
        starts from a full walk rather than diffing against stale state."""
        self.ledger = ledger
        self.prev_addrs = None
        self.prev_serials = None
        self.last_full_tick = self.tick
        self.tick += 1
