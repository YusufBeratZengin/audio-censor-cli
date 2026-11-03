"""Unit tests for the CSV audit log."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from audio_censor.audit import FIELDNAMES, audit_rows, write_audit
from audio_censor.matcher import BadWordMatcher, Interval, Match
from audio_censor.parser import load_transcript

SRT_TEXT = (
    "1\n"
    "00:00:01,000 --> 00:00:02,000\n"
    "that is shit\n"
    "\n"
    "2\n"
    "00:00:05,000 --> 00:00:06,000\n"
    "bloody hell\n"
)


@pytest.fixture
def matches(tmp_path: Path) -> list[Match]:
    path = tmp_path / "audit.srt"
    path.write_text(SRT_TEXT, encoding="utf-8")
    transcript = load_transcript(path)
    return BadWordMatcher(["shit", "bloody hell"]).find(transcript.words)


@pytest.fixture
def intervals() -> list[Interval]:
    return [Interval(start=1.5, end=2.5), Interval(start=5.0, end=6.0)]


class TestAuditRows:
    def test_one_row_per_match(self, matches, intervals) -> None:
        rows = audit_rows(matches, intervals, mode="bleep")
        assert len(rows) == 2
        assert rows[0]["word"] == "shit"
        assert rows[0]["mode"] == "bleep"
        assert rows[0]["cue"] == "1"
        assert rows[0]["fuzzy"] == "no"

    def test_timecodes_are_human_readable(self, matches, intervals) -> None:
        rows = audit_rows(matches, intervals, mode="mute")
        assert rows[0]["start_seconds"] == "1.615"
        assert rows[0]["end_timecode"].startswith("00:00:02")

    def test_pad_and_offset_are_applied(self, matches, intervals) -> None:
        rows = audit_rows(matches, intervals, mode="bleep", pad=0.1, offset=0.5)
        assert rows[0]["start_seconds"] == "2.015"
        assert rows[0]["end_seconds"] == "2.600"

    def test_rows_are_clamped_to_media_duration(self, matches, intervals) -> None:
        rows = audit_rows(matches, intervals, mode="bleep", duration=5.5)
        assert rows[1]["end_seconds"] == "5.500"

    def test_rows_outside_the_media_are_dropped(self, matches, intervals) -> None:
        rows = audit_rows(matches, [], mode="bleep", duration=1.0)
        assert rows == []

    def test_interval_number_is_resolved(self, matches, intervals) -> None:
        rows = audit_rows(matches, intervals, mode="bleep")
        assert rows[0]["interval"] == "1"
        assert rows[1]["interval"] == "2"

    def test_missing_interval_is_blank(self, matches) -> None:
        rows = audit_rows(matches, [Interval(start=9.0, end=10.0)], mode="bleep")
        assert rows[0]["interval"] == ""

    def test_header_matches_rows(self, matches, intervals) -> None:
        rows = audit_rows(matches, intervals, mode="bleep")
        assert set(rows[0]) == set(FIELDNAMES)


class TestWriteAudit:
    def test_writes_csv_with_header(self, matches, intervals, tmp_path: Path) -> None:
        target = tmp_path / "reports" / "audit.csv"
        returned = write_audit(target, matches, intervals, mode="reverse")
        assert returned == target
        with target.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            assert reader.fieldnames == FIELDNAMES
            rows = list(reader)
        assert len(rows) == 2
        assert rows[1]["mode"] == "reverse"
        assert rows[1]["word"] == "bloody hell"

    def test_quoting_of_commas(self, tmp_path: Path) -> None:
        match = Match(
            start=0.0,
            end=1.0,
            text="hello, world",
            pattern="hello",
            cue=1,
            first_word=0,
            last_word=0,
        )
        target = tmp_path / "quoted.csv"
        write_audit(target, [match], [Interval(start=0.0, end=1.0)], mode="mute")
        with target.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert rows[0]["word"] == "hello, world"
