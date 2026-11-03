"""CSV audit log for every masked word."""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from pathlib import Path

from .matcher import Interval, Match
from .text import format_timecode

FIELDNAMES = [
    "index",
    "start_seconds",
    "end_seconds",
    "duration_seconds",
    "start_timecode",
    "end_timecode",
    "mode",
    "word",
    "pattern",
    "cue",
    "fuzzy",
    "interval",
]


def audit_rows(
    matches: Sequence[Match],
    intervals: Sequence[Interval],
    *,
    mode: str,
    offset: float = 0.0,
    pad: float = 0.0,
    duration: float | None = None,
) -> list[dict[str, str]]:
    """Build one audit record per match, clamped like the audio intervals."""
    rows: list[dict[str, str]] = []
    for index, match in enumerate(matches, start=1):
        start = max(0.0, match.start + offset - pad)
        end = match.end + offset + pad
        if duration is not None:
            end = min(end, duration)
        if end <= start:
            continue
        owner = next(
            (
                position
                for position, interval in enumerate(intervals, start=1)
                if interval.start <= start <= interval.end
            ),
            None,
        )
        rows.append(
            {
                "index": str(index),
                "start_seconds": f"{start:.3f}",
                "end_seconds": f"{end:.3f}",
                "duration_seconds": f"{end - start:.3f}",
                "start_timecode": format_timecode(start),
                "end_timecode": format_timecode(end),
                "mode": mode,
                "word": match.text,
                "pattern": match.pattern,
                "cue": str(match.cue),
                "fuzzy": "yes" if match.fuzzy else "no",
                "interval": "" if owner is None else str(owner),
            }
        )
    return rows


def write_audit(
    path: str | Path,
    matches: Iterable[Match],
    intervals: Sequence[Interval],
    *,
    mode: str,
    offset: float = 0.0,
    pad: float = 0.0,
    duration: float | None = None,
) -> Path:
    """Write the audit CSV and return its path."""
    target = Path(path)
    rows = audit_rows(
        list(matches),
        intervals,
        mode=mode,
        offset=offset,
        pad=pad,
        duration=duration,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return target
