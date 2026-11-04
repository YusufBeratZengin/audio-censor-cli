"""The optional report files publish together, or not at all."""

from __future__ import annotations

from pathlib import Path

import pytest

from audio_censor.errors import ReportError
from audio_censor.reports import Report, ReportBatch


def leftovers(directory: Path) -> list[str]:
    """Names of staging/backup files left behind by a report batch."""
    return sorted(
        path.name for path in directory.iterdir() if ".part" in path.name or ".bak" in path.name
    )


class TestStaging:
    def test_stage_leaves_the_visible_files_alone(self, tmp_path: Path) -> None:
        audit = tmp_path / "audit.csv"
        audit.write_text("previous audit", encoding="utf-8")
        batch = ReportBatch([Report(audit, "index\n"), Report(tmp_path / "clean.srt", "1\n")])
        batch.stage()

        assert audit.read_text(encoding="utf-8") == "previous audit"
        assert not (tmp_path / "clean.srt").exists()
        assert len(leftovers(tmp_path)) == 2

        batch.discard()
        assert leftovers(tmp_path) == []
        assert audit.read_text(encoding="utf-8") == "previous audit"

    def test_empty_batch_is_falsy_and_writes_nothing(self, tmp_path: Path) -> None:
        batch = ReportBatch([])
        assert not batch
        batch.stage()
        batch.publish()
        assert list(tmp_path.iterdir()) == []

    def test_stage_failure_removes_what_it_already_staged(self, tmp_path: Path) -> None:
        audit = tmp_path / "audit.csv"
        blocker = tmp_path / "blocked"
        blocker.write_text("a file, not a directory", encoding="utf-8")
        batch = ReportBatch([Report(audit, "index\n"), Report(blocker / "clean.srt", "1\n")])

        with pytest.raises(ReportError) as caught:
            batch.stage()

        assert "clean.srt" in str(caught.value)
        assert not audit.exists()
        assert leftovers(tmp_path) == []


class TestPublishing:
    def test_publish_swaps_every_report_in(self, tmp_path: Path) -> None:
        audit = tmp_path / "audit.csv"
        audit.write_text("previous audit", encoding="utf-8")
        clean = tmp_path / "clean.srt"
        batch = ReportBatch([Report(audit, "new audit"), Report(clean, "new subtitles")])

        batch.stage()
        batch.publish()

        assert audit.read_text(encoding="utf-8") == "new audit"
        assert clean.read_text(encoding="utf-8") == "new subtitles"
        assert leftovers(tmp_path) == []

    def test_publish_failure_restores_the_previous_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed second swap must not leave the first report updated."""
        import os

        audit = tmp_path / "audit.csv"
        audit.write_text("previous audit", encoding="utf-8")
        clean = tmp_path / "clean.srt"
        clean.write_text("previous subtitles", encoding="utf-8")
        batch = ReportBatch([Report(audit, "new audit"), Report(clean, "new subtitles")])
        batch.stage()

        # audit -> .bak, staged -> audit, clean -> .bak (this one fails).
        real_replace = os.replace
        calls = {"count": 0}

        def flaky_replace(src: object, dst: object) -> None:
            calls["count"] += 1
            if calls["count"] == 3:
                raise OSError("simulated disk failure")
            real_replace(src, dst)  # type: ignore[arg-type]

        monkeypatch.setattr("audio_censor.reports.os.replace", flaky_replace)

        with pytest.raises(ReportError) as caught:
            batch.publish()

        assert "clean.srt" in str(caught.value)
        assert audit.read_text(encoding="utf-8") == "previous audit"
        assert clean.read_text(encoding="utf-8") == "previous subtitles"
        assert leftovers(tmp_path) == []

    def test_publish_failure_removes_a_freshly_written_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        audit = tmp_path / "audit.csv"
        clean = tmp_path / "clean.srt"
        batch = ReportBatch([Report(audit, "new audit"), Report(clean, "new subtitles")])
        batch.stage()

        # The first (brand new) report lands, the second swap fails.
        real_replace = os.replace
        calls = {"count": 0}

        def flaky_replace(src: object, dst: object) -> None:
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("simulated disk failure")
            real_replace(src, dst)  # type: ignore[arg-type]

        monkeypatch.setattr("audio_censor.reports.os.replace", flaky_replace)

        with pytest.raises(ReportError):
            batch.publish()

        assert not audit.exists(), "a report from the failed batch was left behind"
        assert leftovers(tmp_path) == []

    def test_a_directory_target_is_refused_before_anything_is_replaced(
        self, tmp_path: Path
    ) -> None:
        audit = tmp_path / "audit.csv"
        audit.write_text("previous audit", encoding="utf-8")
        clean = tmp_path / "clean.srt"
        clean.mkdir()
        (clean / "keep.txt").write_text("keep", encoding="utf-8")
        batch = ReportBatch([Report(audit, "new audit"), Report(clean, "new subtitles")])
        batch.stage()

        with pytest.raises(ReportError) as caught:
            batch.publish()

        assert "not a regular file" in str(caught.value)
        assert audit.read_text(encoding="utf-8") == "previous audit"
        assert (clean / "keep.txt").read_text(encoding="utf-8") == "keep"
        assert leftovers(tmp_path) == []

    def test_publish_without_staging_is_refused(self, tmp_path: Path) -> None:
        batch = ReportBatch([Report(tmp_path / "audit.csv", "index\n")])
        with pytest.raises(ReportError):
            batch.publish()
