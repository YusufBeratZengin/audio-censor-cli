"""Unit tests for transcript parsing and text helpers."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from audio_censor.errors import TranscriptError
from audio_censor.parser import (
    Cue,
    Word,
    detect_format,
    load_transcript,
    parse_srt,
    parse_timestamp,
    parse_vtt,
    parse_whisper_json,
    shift_transcript,
)
from audio_censor.text import (
    collapse_repeats,
    fold,
    format_timecode,
    levenshtein,
    normalize_token,
)

SRT_SAMPLE = """1
00:00:01,000 --> 00:00:04,000
Hello there friend

2
00:00:05,500 --> 00:00:07,250
Second cue
with two lines
"""


class TestTimestamps:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("00:00:01,000", 1.0),
            ("00:00:01.250", 1.25),
            ("01:02:03.500", 3723.5),
            ("02:03.250", 123.25),
            ("1:02:03", 3723.0),
            ("12.5", 12.5),
            ("90", 90.0),
            (" 00:00:00,050 ", 0.05),
        ],
    )
    def test_valid(self, text: str, expected: float) -> None:
        assert parse_timestamp(text) == pytest.approx(expected)

    @pytest.mark.parametrize("text", ["", "abc", "00:00:01,", "--:--", "1:2:3:4"])
    def test_invalid(self, text: str) -> None:
        with pytest.raises(TranscriptError):
            parse_timestamp(text)


class TestSrt:
    def test_basic_structure(self) -> None:
        cues = parse_srt(SRT_SAMPLE)
        assert len(cues) == 2
        assert cues[0].start == pytest.approx(1.0)
        assert cues[0].end == pytest.approx(4.0)
        assert cues[1].end == pytest.approx(7.25)
        assert cues[1].text == "Second cue with two lines"
        assert [word.text for word in cues[0].words] == ["Hello", "there", "friend"]

    def test_word_timings_fill_the_cue(self) -> None:
        cue = parse_srt(SRT_SAMPLE)[0]
        assert cue.words[0].start == pytest.approx(1.0)
        assert cue.words[-1].end == pytest.approx(4.0)
        for earlier, later in zip(cue.words, cue.words[1:], strict=False):
            assert earlier.end == pytest.approx(later.start)
        assert sum(word.duration for word in cue.words) == pytest.approx(3.0)

    def test_crlf_and_bom(self) -> None:
        text = "\ufeff1\r\n00:00:00,500 --> 00:00:01,500\r\nfuck this\r\n"
        cues = parse_srt(text)
        assert cues[0].start == pytest.approx(0.5)
        assert cues[0].text == "fuck this"

    def test_cue_without_index_line(self) -> None:
        cues = parse_srt("00:00:01,000 --> 00:00:02,000\nhello\n")
        assert len(cues) == 1
        assert cues[0].index == 1

    def test_empty_body(self) -> None:
        cues = parse_srt("1\n00:00:01,000 --> 00:00:02,000\n\n")
        assert cues[0].words == ()
        assert cues[0].text == ""

    def test_missing_timing_line(self) -> None:
        with pytest.raises(TranscriptError, match="timing line"):
            parse_srt("1\nnot a timing line\nhello\n")

    def test_invalid_timing_line(self) -> None:
        with pytest.raises(TranscriptError, match="invalid SRT timing"):
            parse_srt("1\nxx:yy --> 00:00:02,000\nhello\n")

    def test_end_before_start(self) -> None:
        with pytest.raises(TranscriptError, match="before start"):
            parse_srt("1\n00:00:05,000 --> 00:00:02,000\nhello\n")

    def test_empty_input(self) -> None:
        with pytest.raises(TranscriptError, match="no cues"):
            parse_srt("\n\n")


class TestVtt:
    def test_basic(self) -> None:
        text = (
            "WEBVTT - demo\n\n"
            "NOTE this is a comment\nthat spans lines\n\n"
            "STYLE\n::cue { color: red }\n\n"
            "intro\n00:00:01.000 --> 00:00:02.500 align:start position:10%\n"
            "<v Bob>Hello</v> <c.yellow>world</c>\n\n"
            "00:02.500 --> 00:04.000\nsecond cue\n"
        )
        cues = parse_vtt(text)
        assert len(cues) == 2
        assert cues[0].words[0].text == "Hello"
        assert cues[0].text == "Hello world"
        assert cues[1].start == pytest.approx(2.5)

    def test_missing_header(self) -> None:
        with pytest.raises(TranscriptError, match="WEBVTT"):
            parse_vtt("00:00:01.000 --> 00:00:02.000\nhello\n")

    def test_no_cues(self) -> None:
        with pytest.raises(TranscriptError, match="no cues"):
            parse_vtt("WEBVTT\n\nNOTE nothing here\n")

    def test_ass_tags_are_stripped(self) -> None:
        text = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n{\\an8}fuck this\n"
        assert parse_vtt(text)[0].text == "fuck this"


class TestWhisperJson:
    def test_word_level(self) -> None:
        payload = {
            "segments": [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "text": " hello world",
                    "words": [
                        {"word": " hello", "start": 0.0, "end": 0.6},
                        {"word": " world", "start": 0.7, "end": 1.9},
                    ],
                }
            ]
        }
        cues = parse_whisper_json(json.dumps(payload))
        assert len(cues) == 1
        assert [word.text for word in cues[0].words] == ["hello", "world"]
        assert cues[0].words[1].start == pytest.approx(0.7)

    def test_segment_without_words_is_distributed(self) -> None:
        cues = parse_whisper_json('{"segments": [{"start": 1.0, "end": 3.0, "text": "a bb ccc"}]}')
        assert len(cues[0].words) == 3
        assert cues[0].words[-1].end == pytest.approx(3.0)

    def test_flat_word_list(self) -> None:
        payload = {"words": [{"word": "fuck", "start": 0.0, "end": 0.5}]}
        cues = parse_whisper_json(json.dumps(payload))
        assert cues[0].words[0].text == "fuck"

    def test_list_of_segments(self) -> None:
        payload = [{"start": 0.0, "end": 1.0, "text": "hello"}, {"start": 1.0, "end": 2.0}]
        cues = parse_whisper_json(json.dumps(payload))
        assert len(cues) == 2
        assert cues[1].words == ()

    def test_whisper_cpp_offsets(self) -> None:
        payload = {
            "transcription": [
                {
                    "timestamps": {"from": "00:00:01,000", "to": "00:00:02,000"},
                    "offsets": {"from": 1000, "to": 2000},
                    "text": " hello there",
                }
            ]
        }
        cues = parse_whisper_json(json.dumps(payload))
        assert cues[0].start == pytest.approx(1.0)
        assert cues[0].end == pytest.approx(2.0)

    def test_offsets_only(self) -> None:
        payload = {"segments": [{"offsets": {"from": 1500, "to": 2500}, "text": "hi"}]}
        cues = parse_whisper_json(json.dumps(payload))
        assert cues[0].start == pytest.approx(1.5)
        assert cues[0].end == pytest.approx(2.5)

    def test_invalid_json(self) -> None:
        with pytest.raises(TranscriptError, match="invalid JSON"):
            parse_whisper_json("{not json")

    def test_unsupported_layout(self) -> None:
        with pytest.raises(TranscriptError, match="unsupported"):
            parse_whisper_json('{"hello": 1}')

    def test_missing_timestamps(self) -> None:
        with pytest.raises(TranscriptError, match="no usable timestamps"):
            parse_whisper_json('{"segments": [{"text": "hello"}]}')

    def test_empty_segments(self) -> None:
        with pytest.raises(TranscriptError, match="no segments"):
            parse_whisper_json('{"segments": []}')

    def test_words_without_timing_fall_back(self) -> None:
        payload = {
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "text": "hello",
                    "words": [{"word": "hello"}],
                }
            ]
        }
        cues = parse_whisper_json(json.dumps(payload))
        assert cues[0].words[0].text == "hello"

    def test_blank_words_are_skipped(self) -> None:
        payload = {
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "words": [
                        {"word": "  ", "start": 0.0, "end": 0.1},
                        {"word": "yo", "start": 0.1, "end": 0.4},
                    ],
                }
            ]
        }
        cues = parse_whisper_json(json.dumps(payload))
        assert [word.text for word in cues[0].words] == ["yo"]
        assert cues[0].text == "yo"


class TestLoadTranscript:
    def test_srt_file(self, tmp_path: Path) -> None:
        target = tmp_path / "a.srt"
        target.write_text(SRT_SAMPLE, encoding="utf-8")
        transcript = load_transcript(target)
        assert transcript.format == "srt"
        assert len(transcript.words) == 8
        assert transcript.duration == pytest.approx(7.25)
        assert transcript.source == str(target)

    def test_unknown_suffix_detected_by_content(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhi\n", encoding="utf-8")
        assert load_transcript(target).format == "vtt"

    def test_json_detected(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text('{"segments": [{"start": 0, "end": 1, "text": "hi"}]}', encoding="utf-8")
        assert load_transcript(target).format == "whisper"

    def test_srt_detected_without_suffix(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text(SRT_SAMPLE, encoding="utf-8")
        assert load_transcript(target).format == "srt"

    def test_undetectable(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("just some prose", encoding="utf-8")
        with pytest.raises(TranscriptError, match="cannot detect"):
            load_transcript(target)

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(TranscriptError, match="not found"):
            load_transcript(tmp_path / "nope.srt")

    def test_explicit_format(self, tmp_path: Path) -> None:
        target = tmp_path / "a.dat"
        target.write_text(SRT_SAMPLE, encoding="utf-8")
        assert load_transcript(target, format="srt").format == "srt"

    def test_unknown_format(self, tmp_path: Path) -> None:
        target = tmp_path / "a.dat"
        target.write_text(SRT_SAMPLE, encoding="utf-8")
        with pytest.raises(TranscriptError, match="unknown transcript format"):
            load_transcript(target, format="ass")

    def test_shift(self, tmp_path: Path) -> None:
        target = tmp_path / "a.srt"
        target.write_text(SRT_SAMPLE, encoding="utf-8")
        transcript = load_transcript(target)
        shifted = shift_transcript(transcript, 2.0)
        assert shifted.cues[0].start == pytest.approx(3.0)
        assert shifted.words[0].start == pytest.approx(3.0)
        assert shift_transcript(transcript, 0.0) is transcript

    def test_shift_reuses_word_objects(self, tmp_path: Path) -> None:
        target = tmp_path / "a.srt"
        target.write_text(SRT_SAMPLE, encoding="utf-8")
        shifted = shift_transcript(load_transcript(target), 1.5)
        cue_words = [word for cue in shifted.cues for word in cue.words]
        assert cue_words
        assert all(any(word is other for other in shifted.words) for word in cue_words)


class TestTextHelpers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("FUCK,", "fuck"),
            ("  shit!  ", "shit"),
            ("SİKTİR", "siktir"),
            ("ISLIK", "islik"),
            ("ılık", "ilik"),
            ("fuckin'", "fuckin"),
            ("...", ""),
        ],
    )
    def test_normalize(self, raw: str, expected: str) -> None:
        assert normalize_token(raw) == expected

    def test_fold(self) -> None:
        assert fold("İSTANBUL") == "istanbul"
        assert fold("IĞDIR") == "igdir"

    def test_collapse(self) -> None:
        assert collapse_repeats("salllaaak") == "salak"
        assert collapse_repeats("aa") == "aa"
        assert collapse_repeats("fuuuck") == "fuck"

    def test_levenshtein(self) -> None:
        assert levenshtein("kitten", "sitting", 3) == 3
        assert levenshtein("abc", "abc", 0) == 0
        assert levenshtein("abc", "xyz", 1) == 2
        assert levenshtein("", "abc", 5) == 3
        assert levenshtein("abc", "", 5) == 3
        assert levenshtein("abcd", "abc", 5) == 1
        assert levenshtein("a", "b", -1) == 0

    @pytest.mark.parametrize(
        ("seconds", "millis", "expected"),
        [
            (0.0, True, "00:00:00.000"),
            (1.2345, True, "00:00:01.234"),
            (3723.5, False, "01:02:03,500"),
            (-3.0, True, "00:00:00.000"),
        ],
    )
    def test_timecode(self, seconds: float, millis: bool, expected: str) -> None:
        assert format_timecode(seconds, millis=millis) == expected


def test_detect_format_prefers_suffix(tmp_path: Path) -> None:
    target = tmp_path / "clip.json"
    assert detect_format(target, "WEBVTT\n") == "whisper"


def test_cue_and_word_are_frozen() -> None:
    word = Word(start=0.0, end=1.0, text="a", norm="a", cue=1)
    with pytest.raises(FrozenInstanceError):
        word.start = 2.0  # type: ignore[misc]
    cue = Cue(index=1, start=0.0, end=1.0, text="a", words=(word,))
    assert cue.words[0].duration == pytest.approx(1.0)
