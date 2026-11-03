"""Unit tests for the censored subtitle exporter."""

from __future__ import annotations

from pathlib import Path

import pytest

from audio_censor.matcher import BadWordMatcher
from audio_censor.parser import load_transcript
from audio_censor.subtitles import DEFAULT_MASK, masked_indices, render_clean_srt, write_clean_srt

SRT_TEXT = (
    "1\n"
    "00:00:01,000 --> 00:00:04,000\n"
    "son of a bitch indeed\n"
    "\n"
    "2\n"
    "00:00:05,000 --> 00:00:06,000\n"
    "hello world\n"
)


@pytest.fixture
def transcript(tmp_path: Path):
    path = tmp_path / "subs.srt"
    path.write_text(SRT_TEXT, encoding="utf-8")
    return load_transcript(path)


class TestMaskedIndices:
    def test_single_word(self, transcript) -> None:
        matches = BadWordMatcher(["world"]).find(transcript.words)
        assert masked_indices(transcript, matches) == {6}

    def test_phrase_covers_every_word(self, transcript) -> None:
        matches = BadWordMatcher(["son of a bitch"]).find(transcript.words)
        assert masked_indices(transcript, matches) == {0, 1, 2, 3}

    def test_no_matches(self, transcript) -> None:
        assert masked_indices(transcript, []) == set()


class TestRenderCleanSrt:
    def test_single_cue_structure(self, transcript) -> None:
        rendered = render_clean_srt(transcript, BadWordMatcher(["world"]).find(transcript.words))
        assert rendered.startswith("1\n00:00:01,000 --> 00:00:04,000\nson of a bitch indeed\n")
        assert "2\n00:00:05,000 --> 00:00:06,000\nhello [BLEEP]\n" in rendered

    def test_phrase_collapses_into_one_mask(self, transcript) -> None:
        rendered = render_clean_srt(
            transcript, BadWordMatcher(["son of a bitch"]).find(transcript.words)
        )
        assert f"{DEFAULT_MASK} indeed" in rendered
        assert rendered.count(DEFAULT_MASK) == 1

    def test_custom_mask(self, transcript) -> None:
        rendered = render_clean_srt(
            transcript,
            BadWordMatcher(["son of a bitch"]).find(transcript.words),
            mask="****",
        )
        assert "**** indeed" in rendered
        assert DEFAULT_MASK not in rendered

    def test_shifted_transcript_is_still_masked(self, transcript) -> None:
        from audio_censor.parser import shift_transcript

        shifted = shift_transcript(transcript, 1.0)
        matches = BadWordMatcher(["world"]).find(shifted.words)
        rendered = render_clean_srt(shifted, matches)
        assert "hello [BLEEP]" in rendered
        assert "hello world" not in rendered

    def test_accepts_precomputed_indices(self, transcript) -> None:
        rendered = render_clean_srt(transcript, {6})
        assert "hello [BLEEP]" in rendered
        assert "son of a bitch indeed" in rendered

    def test_empty_cue_keeps_its_text(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.srt"
        path.write_text("1\n00:00:01,000 --> 00:00:02,000\n\n", encoding="utf-8")
        parsed = load_transcript(path)
        rendered = render_clean_srt(parsed, set())
        assert rendered == "1\n00:00:01,000 --> 00:00:02,000\n\n"

    def test_untouched_cues_keep_original_punctuation(self, tmp_path: Path) -> None:
        path = tmp_path / "punct.srt"
        path.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello, world!\n", encoding="utf-8")
        parsed = load_transcript(path)
        rendered = render_clean_srt(parsed, set())
        assert "Hello, world!" in rendered


class TestWriteCleanSrt:
    def test_writes_file_and_parent_dirs(self, transcript, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "out" / "clean.srt"
        returned = write_clean_srt(target, transcript, {6})
        assert returned == target
        assert target.exists()
        assert "hello [BLEEP]" in target.read_text(encoding="utf-8")

    def test_mask_is_parameter(self, transcript, tmp_path: Path) -> None:
        target = tmp_path / "clean.srt"
        write_clean_srt(target, transcript, {6}, mask="[CENSORED]")
        assert "[CENSORED]" in target.read_text(encoding="utf-8")
