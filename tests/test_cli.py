"""End-to-end CLI tests, including real FFmpeg renders and a DFT check."""

from __future__ import annotations

import csv
import json
import math
import os
import struct
import subprocess
import sys
import wave
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import (
    FFMPEG,
    FFPROBE,
    max_step,
    md5_stream,
    read_wav,
    requires_ffmpeg,
    rms,
    tone_amplitude,
)

from audio_censor import __version__
from audio_censor.cli import build_parser, execute, main, parse_args
from audio_censor.errors import ConfigError


def write_tone_wav(path: Path, segments: list[tuple[float, float]], rate: int = 48000) -> None:
    """Write a mono 16 bit wav made of consecutive constant tones."""
    frames = bytearray()
    for frequency, duration in segments:
        count = int(rate * duration)
        for index in range(count):
            value = int(0.4 * 32767 * math.sin(2 * math.pi * frequency * index / rate))
            frames += struct.pack("<h", value)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))


@pytest.fixture
def stepped_file(tmp_path: Path) -> Path:
    """0-3 s: 440 Hz, 3-3.5 s: 2000 Hz, 3.5-4 s: 200 Hz, 4-6 s: 440 Hz."""
    path = tmp_path / "stepped.wav"
    write_tone_wav(path, [(440, 3.0), (2000, 0.5), (200, 0.5), (440, 2.0)])
    return path


@pytest.fixture
def cue_srt(tmp_path: Path) -> Path:
    """A single cue covering 3.0-4.0 s with one word."""
    path = tmp_path / "cue.srt"
    path.write_text("1\n00:00:03,000 --> 00:00:04,000\nshit\n", encoding="utf-8")
    return path


def run_cli(*argv: object) -> int:
    return main([str(item) for item in argv])


def partial_files(directory: Path) -> list[str]:
    """Names of in-progress render files (``.name.<token>.part<suffix>``)."""
    return sorted(path.name for path in directory.iterdir() if ".part" in path.name)


class TestArgumentParsing:
    def test_defaults(self, audio_file: Path, srt_file: Path, words_file: Path) -> None:
        options = parse_args([str(audio_file), "-s", str(srt_file), "-w", str(words_file)])
        assert options.mode == "bleep"
        assert options.output.name == "input.censored.wav"
        assert options.fade_ms == 5.0
        assert options.audit is None

    def test_words_alias(self, audio_file: Path, srt_file: Path) -> None:
        options = parse_args([str(audio_file), "-s", str(srt_file), "--bad-words", "fuck,shit"])
        assert options.words == "fuck,shit"

    def test_invalid_mode_exits_two(self, audio_file: Path, srt_file: Path) -> None:
        assert run_cli(audio_file, "-s", srt_file, "-w", "fuck", "--mode", "explode") == 2

    def test_missing_arguments_exit_two(self) -> None:
        assert main([]) == 2

    def test_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["--version"]) == 0
        assert __version__ in capsys.readouterr().out

    def test_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["--help"]) == 0
        assert "audio-censor" in capsys.readouterr().out

    def test_parser_prog(self) -> None:
        assert build_parser().prog == "audio-censor"


@requires_ffmpeg
class TestBleep:
    def test_tone_replaces_speech(
        self,
        audio_file: Path,
        srt_file: Path,
        words_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output = tmp_path / "clean.wav"
        assert run_cli(audio_file, "-s", srt_file, "-w", words_file, "-o", output) == 0
        samples, rate = read_wav(output)
        assert len(samples) / rate == pytest.approx(6.0, abs=0.05)

        # inside the masked word the 1 kHz beep is dominant ...
        assert tone_amplitude(samples, rate, 1000, 3.02, 3.26) > 0.2
        # ... and the original 880 Hz tone is gone
        assert tone_amplitude(samples, rate, 880, 3.02, 3.26) < 0.02
        # untouched audio keeps its tone
        assert tone_amplitude(samples, rate, 440, 1.3, 1.9) > 0.4
        assert tone_amplitude(samples, rate, 1000, 4.2, 5.0) < 0.01
        captured = capsys.readouterr().out
        assert "match(es)" in captured
        assert "00:00:03.000" in captured

    def test_no_clicks_at_edges(
        self, audio_file: Path, srt_file: Path, words_file: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "clean.wav"
        assert run_cli(audio_file, "-s", srt_file, "-w", words_file, "-o", output) == 0
        samples, _ = read_wav(output)
        # a 1 kHz sine at 0.3 amplitude has a natural step of ~0.039
        assert max_step(samples) < 0.06

    def test_fade_bounds_are_quiet(
        self, stepped_file: Path, cue_srt: Path, words_file: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "faded.wav"
        assert run_cli(stepped_file, "-s", cue_srt, "-w", "shit", "-o", output) == 0
        samples, rate = read_wav(output)
        # the very first samples of the beep ramp up from silence
        start = round(3.0 * rate)
        assert abs(samples[start]) < 0.02
        assert abs(samples[start + int(0.01 * rate)]) < 0.1

    def test_custom_beep_frequency(
        self, audio_file: Path, srt_file: Path, words_file: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "beep.wav"
        assert (
            run_cli(
                audio_file,
                "-s",
                srt_file,
                "-w",
                words_file,
                "-o",
                output,
                "--beep-freq",
                "800",
                "--beep-volume",
                "0.5",
            )
            == 0
        )
        samples, rate = read_wav(output)
        assert tone_amplitude(samples, rate, 800, 3.02, 3.26) > 0.35
        assert tone_amplitude(samples, rate, 1000, 3.02, 3.26) < 0.05


@requires_ffmpeg
class TestModes:
    def test_mute(self, audio_file: Path, srt_file: Path, words_file: Path, tmp_path: Path) -> None:
        output = tmp_path / "mute.wav"
        assert (
            run_cli(audio_file, "-s", srt_file, "-w", words_file, "-o", output, "--mode", "mute")
            == 0
        )
        samples, rate = read_wav(output)
        assert rms(samples, rate, 3.01, 3.27) < 0.01
        assert rms(samples, rate, 2.0, 2.9) > 0.2
        assert max_step(samples) < 0.06

    def test_reverse(self, stepped_file: Path, cue_srt: Path, tmp_path: Path) -> None:
        output = tmp_path / "reverse.wav"
        assert (
            run_cli(stepped_file, "-s", cue_srt, "-w", "shit", "-o", output, "--mode", "reverse")
            == 0
        )
        samples, rate = read_wav(output)
        # the 3.0-4.0 slice (2000 Hz then 200 Hz) must now play backwards
        assert tone_amplitude(samples, rate, 200, 3.02, 3.45) > 0.2
        assert tone_amplitude(samples, rate, 2000, 3.02, 3.45) < 0.05
        assert tone_amplitude(samples, rate, 2000, 3.56, 3.98) > 0.2
        assert tone_amplitude(samples, rate, 440, 0.5, 2.5) > 0.2
        assert max_step(samples) < 0.15


@requires_ffmpeg
class TestVideo:
    def test_video_stream_is_copied(
        self, video_file: Path, srt_file: Path, words_file: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "clean.mp4"
        before = md5_stream(video_file, "0:v")
        assert run_cli(video_file, "-s", srt_file, "-w", words_file, "-o", output) == 0
        assert md5_stream(output, "0:v") == before
        assert md5_stream(output, "0:a") != md5_stream(video_file, "0:a")

    def test_no_match_copies_everything(
        self, video_file: Path, srt_file: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "untouched.mp4"
        assert run_cli(video_file, "-s", srt_file, "-w", "banana", "-o", output) == 0
        assert md5_stream(output, "0:v") == md5_stream(video_file, "0:v")
        assert md5_stream(output, "0:a") == md5_stream(video_file, "0:a")


@requires_ffmpeg
class TestReports:
    def test_audit_csv(
        self, audio_file: Path, srt_file: Path, words_file: Path, tmp_path: Path
    ) -> None:
        audit = tmp_path / "audit.csv"
        output = tmp_path / "clean.wav"
        assert (
            run_cli(audio_file, "-s", srt_file, "-w", words_file, "-o", output, "--audit", audit)
            == 0
        )
        with audit.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 1
        assert rows[0]["word"] == "shit"
        assert rows[0]["start_seconds"] == "3.000"
        assert rows[0]["end_timecode"] == "00:00:03.278"
        assert rows[0]["mode"] == "bleep"
        assert rows[0]["interval"] == "1"

    def test_clean_subtitles(
        self, audio_file: Path, srt_file: Path, words_file: Path, tmp_path: Path
    ) -> None:
        clean = tmp_path / "clean.srt"
        output = tmp_path / "clean.wav"
        assert (
            run_cli(
                audio_file,
                "-s",
                srt_file,
                "-w",
                words_file,
                "-o",
                output,
                "--clean-subtitles",
                clean,
                "--mask-text",
                "***",
            )
            == 0
        )
        text = clean.read_text(encoding="utf-8")
        assert "*** happens here" in text
        assert "shit" not in text
        assert "hello fucking world" in text
        assert "00:00:03,000 --> 00:00:04,000" in text

    def test_json_summary(
        self,
        audio_file: Path,
        srt_file: Path,
        words_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output = tmp_path / "clean.wav"
        assert run_cli(audio_file, "-s", srt_file, "-w", words_file, "-o", output, "--json") == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["mode"] == "bleep"
        assert payload["matches"][0]["text"] == "shit"
        assert payload["matches"][0]["timing"] == "estimated"
        assert payload["word_timing"] == "estimated"
        assert payload["intervals"][0]["start"] == pytest.approx(3.0)
        assert payload["elapsed_seconds"] >= 0
        assert payload["command"]

    def test_srt_transcript_warns_about_estimated_timings(
        self,
        audio_file: Path,
        srt_file: Path,
        words_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output = tmp_path / "approx.wav"
        assert run_cli(audio_file, "-s", srt_file, "-w", words_file, "-o", output) == 0
        captured = capsys.readouterr()
        assert "estimated" in captured.err
        assert "Whisper" in captured.err
        # the human listing marks the approximate matches
        assert "[approx]" in captured.out

    def test_whisper_word_timings_are_exact_and_quiet(
        self,
        audio_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        transcript = tmp_path / "words.json"
        transcript.write_text(
            json.dumps(
                {
                    "segments": [
                        {
                            "start": 3.0,
                            "end": 4.0,
                            "text": "shit",
                            "words": [{"word": "shit", "start": 3.1, "end": 3.4}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        output = tmp_path / "exact.wav"
        assert run_cli(audio_file, "-s", transcript, "-w", "shit", "-o", output, "--json") == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["word_timing"] == "exact"
        assert payload["matches"][0]["timing"] == "exact"
        assert "estimated" not in captured.err

    def test_dry_run(
        self,
        audio_file: Path,
        srt_file: Path,
        words_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output = tmp_path / "dry.wav"
        assert run_cli(audio_file, "-s", srt_file, "-w", words_file, "-o", output, "--dry-run") == 0
        assert not output.exists()
        captured = capsys.readouterr().out
        assert "ffmpeg" in captured
        assert "[aout]" in captured

    def test_dry_run_writes_nothing(
        self,
        audio_file: Path,
        srt_file: Path,
        words_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output = tmp_path / "dry.wav"
        audit = tmp_path / "dry.csv"
        clean = tmp_path / "dry.srt"
        script = tmp_path / "dry-graph.txt"
        assert (
            run_cli(
                audio_file,
                "-s",
                srt_file,
                "-w",
                words_file,
                "-o",
                output,
                "--audit",
                audit,
                "--clean-subtitles",
                clean,
                "--keep-filter-script",
                script,
                "--dry-run",
            )
            == 0
        )
        assert not output.exists()
        assert not audit.exists()
        assert not clean.exists()
        assert not script.exists()
        assert "would write audit log" in capsys.readouterr().out

    def test_dry_run_json_is_a_single_document(
        self,
        audio_file: Path,
        srt_file: Path,
        words_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output = tmp_path / "dry.wav"
        assert (
            run_cli(
                audio_file,
                "-s",
                srt_file,
                "-w",
                words_file,
                "-o",
                output,
                "--dry-run",
                "--json",
            )
            == 0
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out)  # must be exactly one JSON document
        assert payload["command"][0] == "ffmpeg"
        assert payload["filter_graph"] and "[aout]" in payload["filter_graph"]
        assert not output.exists()

    def test_offset_and_clean_subtitles_mask_the_same_words(
        self,
        audio_file: Path,
        srt_file: Path,
        words_file: Path,
        tmp_path: Path,
    ) -> None:
        clean = tmp_path / "shifted.srt"
        output = tmp_path / "shifted.wav"
        assert (
            run_cli(
                audio_file,
                "-s",
                srt_file,
                "-w",
                words_file,
                "-o",
                output,
                "--offset-ms",
                "1000",
                "--clean-subtitles",
                clean,
            )
            == 0
        )
        text = clean.read_text(encoding="utf-8")
        assert "[BLEEP]" in text
        assert "shit" not in text
        assert "00:00:04,000 --> 00:00:05,000" in text

    def test_no_match_still_writes_empty_reports(
        self,
        audio_file: Path,
        srt_file: Path,
        tmp_path: Path,
    ) -> None:
        output = tmp_path / "untouched.wav"
        audit = tmp_path / "empty.csv"
        clean = tmp_path / "unchanged.srt"
        assert (
            run_cli(
                audio_file,
                "-s",
                srt_file,
                "-w",
                "banana",
                "-o",
                output,
                "--audit",
                audit,
                "--clean-subtitles",
                clean,
            )
            == 0
        )
        with audit.open(encoding="utf-8", newline="") as handle:
            assert handle.readline().strip().startswith("index,")
            assert handle.read() == ""
        text = clean.read_text(encoding="utf-8")
        assert "[BLEEP]" not in text
        assert "shit happens here" in text

    @requires_ffmpeg
    def test_failed_render_leaves_no_reports_or_partial_output(
        self,
        audio_file: Path,
        srt_file: Path,
        words_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A broken encoder must not leave an audit log for audio that was never made."""
        output = tmp_path / "broken.wav"
        audit = tmp_path / "broken.csv"
        clean = tmp_path / "broken.srt"
        assert (
            run_cli(
                audio_file,
                "-s",
                srt_file,
                "-w",
                words_file,
                "-o",
                output,
                "--audit",
                audit,
                "--clean-subtitles",
                clean,
                # FFmpeg fails at graph build time, after the probe succeeded.
                "--audio-codec",
                "no-such-encoder",
            )
            == 1
        )
        assert "FFmpeg failed" in capsys.readouterr().err
        assert not audit.exists(), "audit log written for a failed render"
        assert not clean.exists(), "censored subtitles written for a failed render"
        assert not output.exists(), "truncated output left behind"
        assert partial_files(tmp_path) == [], "staged report files were left behind"

    def test_failed_render_keeps_the_previous_output(
        self,
        audio_file: Path,
        srt_file: Path,
        words_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A failed render must not destroy the file that was already there."""
        output = tmp_path / "previous.wav"
        output.write_bytes(b"previous render")
        assert (
            run_cli(
                audio_file,
                "-s",
                srt_file,
                "-w",
                words_file,
                "-o",
                output,
                "--audio-codec",
                "no-such-encoder",
            )
            == 1
        )
        capsys.readouterr()
        assert output.read_bytes() == b"previous render"
        assert partial_files(tmp_path) == [], "a temporary render file was left behind"

    def test_missing_ffmpeg_keeps_the_previous_output(
        self,
        audio_file: Path,
        srt_file: Path,
        words_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Nothing ran, so nothing may be deleted -- not even the old output."""
        output = tmp_path / "previous.wav"
        output.write_bytes(b"previous render")
        assert (
            run_cli(
                audio_file,
                "-s",
                srt_file,
                "-w",
                words_file,
                "-o",
                output,
                "--ffmpeg",
                "ffmpeg-does-not-exist",
            )
            == 1
        )
        capsys.readouterr()
        assert output.read_bytes() == b"previous render"
        assert partial_files(tmp_path) == []

    def test_successful_render_replaces_the_previous_output(
        self, audio_file: Path, srt_file: Path, words_file: Path, tmp_path: Path
    ) -> None:
        """The finished render lands on the requested path, with no leftovers."""
        output = tmp_path / "previous.wav"
        output.write_bytes(b"previous render")
        assert run_cli(audio_file, "-s", srt_file, "-w", words_file, "-o", output) == 0
        assert output.read_bytes() != b"previous render"
        assert output.stat().st_size > 1000
        assert partial_files(tmp_path) == []

    def test_unwritable_report_path_fails_before_the_render(
        self,
        audio_file: Path,
        srt_file: Path,
        words_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A report that cannot even be staged must not cost a full render."""
        blocker = tmp_path / "blocked"
        blocker.write_text("a file, not a directory", encoding="utf-8")
        output = tmp_path / "clean.wav"
        assert (
            run_cli(
                audio_file,
                "-s",
                srt_file,
                "-w",
                words_file,
                "-o",
                output,
                "--audit",
                blocker / "audit.csv",
            )
            == 1
        )
        assert "could not stage the report" in capsys.readouterr().err
        assert not output.exists(), "audio was rendered although its report could not be staged"

    def test_report_failure_keeps_the_previous_reports(
        self,
        audio_file: Path,
        srt_file: Path,
        words_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A directory at one report path must not half-publish the others."""
        output = tmp_path / "clean.wav"
        audit = tmp_path / "audit.csv"
        audit.write_text("previous audit", encoding="utf-8")
        clean = tmp_path / "clean.srt"
        clean.mkdir()
        (clean / "keep.txt").write_text("keep", encoding="utf-8")
        assert (
            run_cli(
                audio_file,
                "-s",
                srt_file,
                "-w",
                words_file,
                "-o",
                output,
                "--audit",
                audit,
                "--clean-subtitles",
                clean,
            )
            == 1
        )
        err = capsys.readouterr().err
        assert "not a regular file" in err
        assert "reports were not updated" in err
        assert audit.read_text(encoding="utf-8") == "previous audit"
        assert (clean / "keep.txt").read_text(encoding="utf-8") == "keep"
        assert partial_files(tmp_path) == []

    def test_keep_filter_script(
        self, audio_file: Path, srt_file: Path, words_file: Path, tmp_path: Path
    ) -> None:
        script = tmp_path / "graph.txt"
        output = tmp_path / "clean.wav"
        assert (
            run_cli(
                audio_file,
                "-s",
                srt_file,
                "-w",
                words_file,
                "-o",
                output,
                "--keep-filter-script",
                script,
            )
            == 0
        )
        content = script.read_text(encoding="utf-8")
        assert "[aout]" in content
        assert "aevalsrc" in content


@requires_ffmpeg
class TestOptions:
    def test_offset_shifts_the_mask(
        self, audio_file: Path, srt_file: Path, words_file: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "offset.wav"
        assert (
            run_cli(
                audio_file, "-s", srt_file, "-w", words_file, "-o", output, "--offset-ms", "-1000"
            )
            == 0
        )
        samples, rate = read_wav(output)
        assert tone_amplitude(samples, rate, 1000, 2.02, 2.26) > 0.2
        assert tone_amplitude(samples, rate, 880, 3.02, 3.26) > 0.2

    def test_pad_extends_the_mask(
        self, audio_file: Path, srt_file: Path, words_file: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "pad.wav"
        assert (
            run_cli(audio_file, "-s", srt_file, "-w", words_file, "-o", output, "--pad-ms", "200")
            == 0
        )
        samples, rate = read_wav(output)
        assert tone_amplitude(samples, rate, 1000, 2.82, 2.95) > 0.2

    def test_fuzzy_matching(self, stepped_file: Path, tmp_path: Path) -> None:
        script = tmp_path / "fuzzy.srt"
        script.write_text("1\n00:00:03,000 --> 00:00:04,000\nsalllaaak\n", encoding="utf-8")
        output = tmp_path / "fuzzy.wav"
        assert (
            run_cli(stepped_file, "-s", script, "-w", "sallak", "-o", output, "--fuzzy", "1") == 0
        )
        samples, rate = read_wav(output)
        assert tone_amplitude(samples, rate, 1000, 3.02, 3.26) > 0.2

    def test_inline_word_list(self, audio_file: Path, srt_file: Path, tmp_path: Path) -> None:
        output = tmp_path / "inline.wav"
        assert run_cli(audio_file, "-s", srt_file, "-w", "shit", "-o", output) == 0
        assert output.exists()

    def test_quiet_suppresses_info(
        self,
        audio_file: Path,
        srt_file: Path,
        words_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output = tmp_path / "quiet.wav"
        assert run_cli(audio_file, "-s", srt_file, "-w", words_file, "-o", output, "--quiet") == 0
        assert capsys.readouterr().out == ""

    def test_default_output_name(self, audio_file: Path, srt_file: Path, words_file: Path) -> None:
        assert run_cli(audio_file, "-s", srt_file, "-w", words_file, "--quiet") == 0
        assert (audio_file.parent / "input.censored.wav").exists()

    def test_short_transcript_warns(
        self, audio_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        script = tmp_path / "late.srt"
        script.write_text("1\n00:00:20,000 --> 00:00:22,000\nshit\n", encoding="utf-8")
        output = tmp_path / "late.wav"
        assert run_cli(audio_file, "-s", script, "-w", "shit", "-o", output) == 0
        captured = capsys.readouterr()
        assert "warn" in captured.err
        assert "offset-ms" in captured.err


@requires_ffmpeg
class TestErrors:
    def test_missing_input(self, srt_file: Path, words_file: Path, capsys) -> None:
        assert run_cli("nope.wav", "-s", srt_file, "-w", words_file) == 1
        assert "input file not found" in capsys.readouterr().err

    def test_missing_transcript(self, audio_file: Path, words_file: Path, capsys) -> None:
        assert run_cli(audio_file, "-s", "nope.srt", "-w", words_file) == 1
        assert "transcript not found" in capsys.readouterr().err

    def test_output_equals_input(
        self, audio_file: Path, srt_file: Path, words_file: Path, capsys
    ) -> None:
        assert run_cli(audio_file, "-s", srt_file, "-w", words_file, "-o", audio_file) == 1
        assert "must differ" in capsys.readouterr().err

    def test_output_hard_linked_to_the_input_is_refused(
        self, audio_file: Path, srt_file: Path, words_file: Path, tmp_path: Path, capsys
    ) -> None:
        # A hard link has a different name for the same inode, so comparing
        # resolved path strings would let FFmpeg's -y truncate the source.
        link = tmp_path / "linked-input.wav"
        try:
            os.link(audio_file, link)
        except (OSError, NotImplementedError):
            pytest.skip("this filesystem does not support hard links")
        before = audio_file.read_bytes()
        assert run_cli(audio_file, "-s", srt_file, "-w", words_file, "-o", link) == 1
        assert "must differ from the input path" in capsys.readouterr().err
        assert audio_file.read_bytes() == before

    def test_audit_hard_linked_to_the_input_is_refused(
        self, audio_file: Path, srt_file: Path, words_file: Path, tmp_path: Path, capsys
    ) -> None:
        link = tmp_path / "linked-audit.csv"
        try:
            os.link(audio_file, link)
        except (OSError, NotImplementedError):
            pytest.skip("this filesystem does not support hard links")
        before = audio_file.read_bytes()
        assert run_cli(audio_file, "-s", srt_file, "-w", words_file, "--audit", link) == 1
        assert "--audit path must differ from the input path" in capsys.readouterr().err
        assert audio_file.read_bytes() == before

    def test_audit_cannot_overwrite_the_input(
        self, audio_file: Path, srt_file: Path, words_file: Path, capsys
    ) -> None:
        assert run_cli(audio_file, "-s", srt_file, "-w", words_file, "--audit", audio_file) == 1
        assert "--audit path must differ from the input path" in capsys.readouterr().err

    def test_clean_subtitles_cannot_overwrite_the_transcript(
        self, audio_file: Path, srt_file: Path, words_file: Path, capsys
    ) -> None:
        assert (
            run_cli(
                audio_file,
                "-s",
                srt_file,
                "-w",
                words_file,
                "--clean-subtitles",
                srt_file,
            )
            == 1
        )
        assert "--clean-subtitles path must differ from the transcript path" in (
            capsys.readouterr().err
        )

    def test_output_paths_must_not_collide(
        self, audio_file: Path, srt_file: Path, words_file: Path, tmp_path: Path, capsys
    ) -> None:
        shared = tmp_path / "shared.csv"
        assert (
            run_cli(
                audio_file,
                "-s",
                srt_file,
                "-w",
                words_file,
                "--audit",
                shared,
                "--keep-filter-script",
                shared,
            )
            == 1
        )
        assert "is the same file as the" in capsys.readouterr().err

    def test_keep_filter_script_cannot_overwrite_the_output(
        self, audio_file: Path, srt_file: Path, words_file: Path, tmp_path: Path, capsys
    ) -> None:
        output = tmp_path / "out.wav"
        assert (
            run_cli(
                audio_file,
                "-s",
                srt_file,
                "-w",
                words_file,
                "-o",
                output,
                "--keep-filter-script",
                output,
            )
            == 1
        )
        assert "is the same file as the" in capsys.readouterr().err

    def test_missing_word_file_is_an_error(
        self, audio_file: Path, srt_file: Path, tmp_path: Path, capsys
    ) -> None:
        missing = tmp_path / "badwords.txt"
        assert run_cli(audio_file, "-s", srt_file, "-w", missing) == 1
        assert "bad word file not found" in capsys.readouterr().err
        assert not (audio_file.parent / "input.censored.wav").exists()

    def test_bad_word_file(self, audio_file: Path, srt_file: Path, tmp_path: Path, capsys) -> None:
        empty = tmp_path / "empty.txt"
        empty.write_text("# only comments\n", encoding="utf-8")
        assert run_cli(audio_file, "-s", srt_file, "-w", empty) == 1
        assert "no usable bad words" in capsys.readouterr().err

    def test_invalid_regex(self, audio_file: Path, srt_file: Path, capsys) -> None:
        assert run_cli(audio_file, "-s", srt_file, "-w", "re:(") == 1
        assert "invalid regular expression" in capsys.readouterr().err

    def test_invalid_fade(self, audio_file: Path, srt_file: Path, words_file: Path, capsys) -> None:
        assert run_cli(audio_file, "-s", srt_file, "-w", words_file, "--fade-ms", "-5") == 1
        assert "fade-ms" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("option", "value"),
        [
            ("--max-gap-ms", "nan"),
            ("--max-gap-ms", "inf"),
            ("--fade-ms", "NaN"),
            ("--pad-ms", "inf"),
            ("--offset-ms", "Infinity"),
            ("--beep-freq", "nan"),
            ("--beep-volume", "nan"),
        ],
    )
    def test_non_finite_numbers_exit_two(
        self,
        audio_file: Path,
        srt_file: Path,
        words_file: Path,
        option: str,
        value: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # argparse's float() accepts nan/inf; passing them on would defeat
        # every range check downstream (e.g. --max-gap-ms nan turns the pause
        # guard into a no-op and a phrase matches across minutes of silence).
        assert run_cli(audio_file, "-s", srt_file, "-w", words_file, option, value) == 2
        assert "finite" in capsys.readouterr().err

    def test_non_numeric_float_option_exits_two(
        self, audio_file: Path, srt_file: Path, words_file: Path, capsys
    ) -> None:
        assert run_cli(audio_file, "-s", srt_file, "-w", words_file, "--fade-ms", "abc") == 2
        assert "not a number" in capsys.readouterr().err

    def test_library_caller_cannot_bypass_the_finite_check(
        self, audio_file: Path, srt_file: Path, words_file: Path
    ) -> None:
        # ``parse_args`` rejects nan, but ``CliOptions`` is also built by code
        # that imports the package; ``execute`` must not trust its caller.
        options = parse_args([str(audio_file), "-s", str(srt_file), "-w", str(words_file)])
        with pytest.raises(ConfigError, match="--max-gap-ms must be a finite number"):
            execute(replace(options, max_gap_ms=float("nan")))

    def test_invalid_volume(
        self, audio_file: Path, srt_file: Path, words_file: Path, capsys
    ) -> None:
        assert run_cli(audio_file, "-s", srt_file, "-w", words_file, "--beep-volume", "2") == 1
        assert "beep-volume" in capsys.readouterr().err

    def test_negative_fuzzy(
        self, audio_file: Path, srt_file: Path, words_file: Path, capsys
    ) -> None:
        assert run_cli(audio_file, "-s", srt_file, "-w", words_file, "--fuzzy", "-1") == 1
        assert "fuzzy" in capsys.readouterr().err

    @requires_ffmpeg
    def test_beep_frequency_above_nyquist_is_refused(
        self, audio_file: Path, srt_file: Path, words_file: Path, tmp_path: Path, capsys
    ) -> None:
        # Above fs/2 a sine folds back to another pitch, so the output would
        # not contain the requested tone at all.
        output = tmp_path / "aliased.wav"
        assert (
            run_cli(
                audio_file,
                "-s",
                srt_file,
                "-w",
                words_file,
                "-o",
                output,
                "--beep-freq",
                "30000",
            )
            == 1
        )
        assert "Nyquist" in capsys.readouterr().err
        assert not output.exists()

    def test_broken_ffmpeg_binary(
        self, audio_file: Path, srt_file: Path, words_file: Path, capsys
    ) -> None:
        assert (
            run_cli(
                audio_file,
                "-s",
                srt_file,
                "-w",
                words_file,
                "--ffmpeg",
                "ffmpeg-does-not-exist",
                "--ffprobe",
                "ffprobe-does-not-exist",
            )
            == 1
        )
        assert "not found" in capsys.readouterr().err

    def test_unreadable_transcript(
        self, audio_file: Path, words_file: Path, tmp_path: Path, capsys
    ) -> None:
        broken = tmp_path / "broken.srt"
        broken.write_text("this is not a subtitle file", encoding="utf-8")
        assert run_cli(audio_file, "-s", broken, "-w", words_file) == 1
        assert "error:" in capsys.readouterr().err


@requires_ffmpeg
def test_python_module_entrypoint(
    audio_file: Path, srt_file: Path, words_file: Path, tmp_path: Path
) -> None:
    output = tmp_path / "module.wav"
    # The child process gets the checkout on PYTHONPATH, so `-m audio_censor`
    # works without an editable install and regardless of the current directory.
    env = dict(os.environ)
    repository_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [repository_root, env.get("PYTHONPATH")]))
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "audio_censor",
            str(audio_file),
            "-s",
            str(srt_file),
            "-w",
            str(words_file),
            "-o",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    assert output.exists()


def test_ffmpeg_is_available() -> None:
    """Guard: the suite must not silently skip everything."""
    assert FFMPEG is not None, "ffmpeg is not on PATH; integration tests would be skipped"
    assert FFPROBE is not None, "ffprobe is not on PATH; integration tests would be skipped"
    completed = subprocess.run([FFMPEG, "-version"], capture_output=True, text=True, check=False)
    assert completed.returncode == 0
    assert "ffmpeg version" in (completed.stdout + completed.stderr)
