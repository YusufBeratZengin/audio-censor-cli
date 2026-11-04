"""Publish the optional report files (audit CSV, censored subtitles) atomically.

The render itself has always been all-or-nothing: FFmpeg writes a hidden
staging file and ``os.replace`` publishes it (see :mod:`audio_censor.engine`).
The reports follow the same rule.  ``ReportBatch.stage`` writes every report to
a hidden sibling of its target, and ``publish`` swaps them in only once all of
them were staged.  If one swap fails, the targets that were already replaced
are restored from the backups taken during the swap, so a reader never finds a
fresh audit CSV next to a stale subtitle file.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import ReportError

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Report:
    """One finished report file, ready to be written."""

    path: Path
    text: str


def _unique_token() -> str:
    return f"{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _remove(path: Path) -> None:
    """Best effort removal of a staging or backup file."""
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:  # pragma: no cover - the directory vanished under us
        LOGGER.warning("could not remove %s: %s", path, exc)


class ReportBatch:
    """Write a set of reports together, or leave the previous files alone.

    ``stage`` touches nothing the user can see; ``discard`` throws the staged
    copies away (used when the render failed); ``publish`` performs the swaps.
    """

    def __init__(self, reports: Sequence[Report]) -> None:
        self._reports = list(reports)
        self._token = _unique_token()
        self._staged: list[Path] = []

    def __bool__(self) -> bool:
        return bool(self._reports)

    def _staged_path(self, target: Path) -> Path:
        return target.with_name(f".{target.name}.{self._token}.part")

    def _backup_path(self, target: Path) -> Path:
        return target.with_name(f".{target.name}.{self._token}.bak")

    def stage(self) -> None:
        """Write every report next to its target without replacing anything."""
        current: Path | None = None
        try:
            for report in self._reports:
                target = Path(report.path)
                current = target
                target.parent.mkdir(parents=True, exist_ok=True)
                staged = self._staged_path(target)
                with staged.open("w", encoding="utf-8", newline="") as handle:
                    handle.write(report.text)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._staged.append(staged)
        except OSError as exc:
            self.discard()
            name = "report" if current is None else str(current)
            raise ReportError(f"could not stage the report {name}: {exc}") from exc

    def publish(self) -> None:
        """Swap every staged report into place, or restore the previous ones."""
        if not self._reports:
            return
        if len(self._staged) != len(self._reports):
            raise ReportError("reports must be staged before they are published")
        for report in self._reports:
            target = Path(report.path)
            if target.exists() and not target.is_file():
                # ``os.replace`` would happily rename a directory out of the
                # way on Windows; refuse instead of replacing it with a file.
                self.discard()
                raise ReportError(f"cannot replace {target}: it is not a regular file")

        published: list[tuple[Path, Path | None]] = []
        current: Path | None = None
        try:
            for report, staged in zip(self._reports, self._staged, strict=True):
                target = Path(report.path)
                current = target
                backup: Path | None = None
                if target.exists():
                    backup = self._backup_path(target)
                    os.replace(target, backup)
                published.append((target, backup))
                os.replace(staged, target)
        except OSError as exc:
            self._restore(published)
            self.discard()
            name = "report" if current is None else str(current)
            raise ReportError(f"could not publish the report {name}: {exc}") from exc
        self._staged = []
        for _, backup in published:
            if backup is not None:
                _remove(backup)

    def _restore(self, published: Sequence[tuple[Path, Path | None]]) -> None:
        for target, backup in reversed(published):
            try:
                if backup is not None and backup.exists():
                    os.replace(backup, target)
                elif backup is None:
                    _remove(target)
            except OSError as exc:  # pragma: no cover - restore is best effort
                LOGGER.warning("could not restore %s: %s", target, exc)

    def discard(self) -> None:
        """Remove the staged copies; the visible files are untouched."""
        for staged in self._staged:
            _remove(staged)
        self._staged = []
