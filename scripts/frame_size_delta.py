"""What does a set of keys cost a recording? — the storage question, made repeatable.

    python -m scripts.frame_size_delta match.sqrx teams[].commander drones
    python -m scripts.frame_size_delta match.sqrx --keys-file keys.txt

Every capture addition asks the same question and, until now, was answered by
hand: re-encode a recording's lines without the new keys and compare. That
method (2026-09-05, the +1.4 % / +1.7 % measurement in the journal) is what this
script is. It is the storage gate's tool for the commander capture and a general
one after it.

HOW IT MEASURES
---------------
Both sides are re-encoded the same way, so the difference is the keys and
nothing else:

  * the line encoding is the recorder's own — `json.dumps(obj,
    ensure_ascii=False)` plus a newline (`sqreader/cli.py`), measured as UTF-8
    bytes;
  * compression is one independent zstd frame per line, exactly as
    `SqrxWriter.write_line` writes them, at the level `SqrxWriter` defaults to.
    That level is READ from `sqreader.sqrx` (below), never chosen here: a
    recorder that changes level must move this measurement with it.

Full frames and 4 Hz position lines are reported separately, because a key on a
position line is paid four times a second and a key on a full frame once.

KEY PATHS
---------
Dotted, with `[]` marking a list to descend into:

    commandActions              a top-level key
    teams[].commander           one key on every element of `teams`
    players[].soldier.medical   two hops down, per player
    vehicles[].turrets[].weapons  lists nested in lists

A path that matches nothing costs nothing — an absent key is simply not there
to strip, which is also the honest answer for a recording made before it
existed.
"""
from __future__ import annotations

import argparse
import inspect
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import zstandard as zstd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqreader.sqrx import SqrxReader, SqrxWriter  # noqa: E402

# The recorder's compression level, taken from the writer that produces every
# .sqrx rather than restated here. `recorder.py` constructs `SqrxWriter(path,
# server_id=...)` with no level, so its default IS the level on disk.
RECORDER_ZSTD_LEVEL: int = (
    inspect.signature(SqrxWriter.__init__).parameters["level"].default)


def encode_line(obj: object) -> bytes:
    """The bytes the recorder would write for `obj` — its own encoding."""
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def strip_key(obj: object, segments: list[str]) -> None:
    """Remove the key `segments` names, in place, wherever it occurs.

    A segment ending in `[]` descends into a list; the last segment is the key
    removed. Anything the path does not fit — a missing key, a dict where a
    list was named — is left alone: the path simply does not match there.
    """
    if not segments:
        return
    head, rest = segments[0], segments[1:]
    if head.endswith("[]"):
        name = head[:-2]
        if not isinstance(obj, dict):
            return
        seq = obj.get(name)
        if not isinstance(seq, list):
            return
        for item in seq:
            strip_key(item, rest)
        return
    if not isinstance(obj, dict):
        return
    if not rest:
        obj.pop(head, None)
        return
    strip_key(obj.get(head), rest)


def strip_keys(obj: object, key_paths: list[str]) -> None:
    for path in key_paths:
        strip_key(obj, [s for s in path.split(".") if s])


@dataclass
class Sizes:
    """Bytes for one kind of line, with the keys and without them."""
    lines: int = 0
    raw_with: int = 0
    raw_without: int = 0
    comp_with: int = 0
    comp_without: int = 0

    @property
    def raw_delta(self) -> int:
        return self.raw_with - self.raw_without

    @property
    def comp_delta(self) -> int:
        return self.comp_with - self.comp_without

    def add(self, raw_with: int, raw_without: int,
            comp_with: int, comp_without: int) -> None:
        self.lines += 1
        self.raw_with += raw_with
        self.raw_without += raw_without
        self.comp_with += comp_with
        self.comp_without += comp_without


@dataclass
class Report:
    path: Path
    key_paths: list[str]
    level: int
    full: Sizes
    pos: Sizes
    other: Sizes
    on_disk: int
    unparsed: int


def measure(path: str | Path, key_paths: list[str], *,
            level: int = RECORDER_ZSTD_LEVEL) -> Report:
    """Re-encode every line of a .sqrx with and without `key_paths`."""
    p = Path(path)
    cctx = zstd.ZstdCompressor(level=level)
    full, pos, other = Sizes(), Sizes(), Sizes()
    unparsed = 0
    with SqrxReader(p) as reader:
        for line in reader.lines():
            try:
                kept = json.loads(line)
            except (ValueError, TypeError):
                unparsed += 1
                continue
            stripped = json.loads(line)
            strip_keys(stripped, key_paths)
            raw_with = encode_line(kept)
            raw_without = encode_line(stripped)
            bucket = (pos if isinstance(kept, dict) and kept.get("t") == "pos"
                      else full if isinstance(kept, dict) else other)
            bucket.add(len(raw_with), len(raw_without),
                       len(cctx.compress(raw_with)),
                       len(cctx.compress(raw_without)))
    return Report(path=p, key_paths=list(key_paths), level=level,
                  full=full, pos=pos, other=other,
                  on_disk=p.stat().st_size, unparsed=unparsed)


def _pct(delta: int, total: int) -> str:
    return f"{100.0 * delta / total:6.2f} %" if total else "     — "


def _row(label: str, s: Sizes) -> str:
    if not s.lines:
        return f"  {label:<14s} {'(none)':>10s}"
    return (f"  {label:<14s} {s.lines:>10d} lines"
            f"  raw {s.raw_with:>12d} -{s.raw_delta:>10d} ({_pct(s.raw_delta, s.raw_with)})"
            f"  zstd {s.comp_with:>11d} -{s.comp_delta:>9d}"
            f" ({_pct(s.comp_delta, s.comp_with)})")


def render_text(report: Report) -> str:
    total_with = report.full.comp_with + report.pos.comp_with + report.other.comp_with
    total_delta = (report.full.comp_delta + report.pos.comp_delta
                   + report.other.comp_delta)
    out = [
        f"{report.path}",
        f"  keys stripped: {', '.join(report.key_paths) or '(none)'}",
        f"  zstd level {report.level} (the recorder's), one frame per line; "
        f"{report.on_disk} bytes on disk",
        "",
        _row("full frames", report.full),
        _row("position", report.pos),
    ]
    if report.other.lines:
        out.append(_row("non-object", report.other))
    out += [
        "",
        f"  compressed cost of these keys: {total_delta} B of "
        f"{total_with} B ({_pct(total_delta, total_with)})",
    ]
    if report.full.lines:
        out.append(f"  per full frame: {report.full.comp_delta / report.full.lines:.1f} B "
                   f"compressed, {report.full.raw_delta / report.full.lines:.1f} B raw")
    if report.pos.lines:
        out.append(f"  per position line: {report.pos.comp_delta / report.pos.lines:.1f} B "
                   f"compressed, {report.pos.raw_delta / report.pos.lines:.1f} B raw")
    if report.unparsed:
        out.append(f"  {report.unparsed} line(s) would not parse as JSON and were skipped")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Measure what a set of keys costs a .sqrx recording.")
    ap.add_argument("recording", type=Path)
    ap.add_argument("keys", nargs="*",
                    help="key paths, e.g. teams[].commander players[].soldier.medical")
    ap.add_argument("--keys-file", type=Path,
                    help="read key paths from a file, one per line (# comments ok)")
    ap.add_argument("--level", type=int, default=RECORDER_ZSTD_LEVEL,
                    help="zstd level (default: the recorder's own)")
    args = ap.parse_args(argv)

    key_paths = list(args.keys)
    if args.keys_file:
        for raw in args.keys_file.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                key_paths.append(line)
    if not key_paths:
        print("no key paths given — nothing to measure", file=sys.stderr)
        return 2
    print(render_text(measure(args.recording, key_paths, level=args.level)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
