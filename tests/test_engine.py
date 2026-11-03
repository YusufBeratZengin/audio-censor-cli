"""Tests for probing, command assembly and FFmpeg execution."""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest
from conftest import requires_ffmpeg

from audio_censor import engine as engine_module
from audio_censor.engine import (
    Engine,
    MediaInfo,
    ffmpeg_version,
    format_argv,
    probe,
    script_option_for,
)
from audio_censor.errors import FFmpegError

MEDIA = MediaInfo(path="input.mp4", duration=5.0, sample_rate=48000, channels=2, has_video=True)


class TestProbe:
    @requires_ffmpeg
    def test_wav(self, audio_file: Path) -> None:
        info = probe(audio_file)
        assert info.sample_rate == 48000
        assert info.channels == 1
        assert info.has_video is False
        assert info.duration == pytest.approx(6.0, abs=0.05)
        assert info.audio_codec == "pcm_s16le"

    @requires_ffmpeg
    def test_video(self, video_file: Path) -> None:
        info = probe(video_file)
        assert info.has_video is True
        assert info.video_codec == "h264"
        assert info.channels == 1

    @requires_ffmpeg
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FFmpegError):
            probe(tmp_path / "missing.wav")

    @requires_ffmpeg
    def test_file_without_audio(self, tmp_path: Path) -> None:
        from conftest import run_ffmpeg

        target = tmp_path / "silent.mp4"
        run_ffmpeg("-f", "lavfi", "-i", "testsrc=size=64x64:rate=5:duration=1", str(target))
        with pytest.raises(FFmpegError, match="no audio stream"):
            probe(target)

    def test_missing_ffprobe(self, audio_file: Path) -> None:
        with pytest.raises(FFmpegError, match="not found"):
            probe(audio_file, ffprobe="ffprobe-does-not-exist")

    @requires_ffmpeg
    def test_invalid_json(self, audio_file: Path, tmp_path: Path, monkeypatch) -> None:
        fake = tmp_path / "fake-ffprobe"
        fake.write_text("#!/bin/sh\necho 'not json'\n", encoding="utf-8")
        fake.chmod(0o755)
        with pytest.raises(FFmpegError, match="invalid JSON"):
            probe(audio_file, ffprobe=str(fake))

    @requires_ffmpeg
    def test_failure_exit_code(self, audio_file: Path, tmp_path: Path) -> None:
        fake = tmp_path / "fake-ffprobe"
        fake.write_text("#!/bin/sh\necho boom >&2\nexit 3\n", encoding="utf-8")
        fake.chmod(0o755)
        with pytest.raises(FFmpegError, match="could not read"):
            probe(audio_file, ffprobe=str(fake))


class TestVersionDetection:
    @requires_ffmpeg
    def test_real_version(self) -> None:
        version = ffmpeg_version()
        assert version >= (3,)

    def test_missing_binary(self) -> None:
        with pytest.raises(FFmpegError, match="not found"):
            ffmpeg_version("ffmpeg-does-not-exist")

    def test_classic_script_option(self, monkeypatch) -> None:
        monkeypatch.setattr(engine_module, "ffmpeg_version", lambda *_: (9, 0))
        assert script_option_for("ffmpeg", help_text="-filter_complex_script <file>") == (
            "-filter_complex_script"
        )

    def test_modern_file_syntax(self, monkeypatch) -> None:
        monkeypatch.setattr(engine_module, "ffmpeg_version", lambda *_: (8, 0))
        assert (
            script_option_for("ffmpeg", help_text="-filter_complex <graph>") == "-/filter_complex"
        )

    def test_old_inline_fallback(self, monkeypatch) -> None:
        monkeypatch.setattr(engine_module, "ffmpeg_version", lambda *_: (5, 1))
        assert script_option_for("ffmpeg", help_text="-filter_complex <graph>") == ""

    def test_help_probe_missing_binary(self) -> None:
        with pytest.raises(FFmpegError, match="not found"):
            script_option_for("ffmpeg-does-not-exist")


class TestCommandAssembly:
    @staticmethod
    def builder(option: str = "-filter_complex_script") -> Engine:
        engine = Engine()
        engine._script_option = option
        return engine

    def test_script_is_used(self) -> None:
        argv = self.builder().build_argv(
            input_path="in.mp4",
            output_path="out.mp4",
            media=MEDIA,
            graph="[0:a]anull[aout]",
            script_path="/tmp/graph.txt",
        )
        assert "-filter_complex_script" in argv
        assert argv[argv.index("-filter_complex_script") + 1] == "/tmp/graph.txt"
        assert "[0:a]anull[aout]" not in argv

    def test_inline_fallback(self) -> None:
        argv = self.builder("").build_argv(
            input_path="in.mp4",
            output_path="out.mp4",
            media=MEDIA,
            graph="[0:a]anull[aout]",
            script_path=None,
        )
        assert "-filter_complex" in argv
        assert "[0:a]anull[aout]" in argv

    def test_video_is_copied(self) -> None:
        argv = self.builder().build_argv(
            input_path="in.mp4", output_path="out.mp4", media=MEDIA, graph="g", script_path="s"
        )
        assert "-c:v" in argv
        assert argv[argv.index("-c:v") + 1] == "copy"
        assert "0:v?" in argv
        assert "[aout]" in argv

    def test_audio_only_input_has_no_video_map(self) -> None:
        media = MediaInfo(
            path="a.wav", duration=1.0, sample_rate=48000, channels=1, has_video=False
        )
        argv = self.builder().build_argv(
            input_path="a.wav", output_path="a.wav", media=media, graph="g", script_path="s"
        )
        assert "0:v?" not in argv

    @pytest.mark.parametrize(
        ("suffix", "codec", "bitrate"),
        [
            (".mp4", "aac", "192k"),
            (".mkv", "aac", "192k"),
            (".webm", "libopus", "128k"),
            (".mp3", "libmp3lame", "192k"),
            (".wav", "pcm_s16le", None),
            (".flac", "flac", None),
        ],
    )
    def test_encoder_by_container(self, suffix: str, codec: str, bitrate: str | None) -> None:
        argv = self.builder().build_argv(
            input_path="in", output_path=f"out{suffix}", media=MEDIA, graph="g", script_path="s"
        )
        assert argv[argv.index("-c:a") + 1] == codec
        if bitrate:
            assert argv[argv.index("-b:a") + 1] == bitrate
        else:
            assert "-b:a" not in argv

    def test_unknown_container_uses_ffmpeg_default(self) -> None:
        argv = self.builder().build_argv(
            input_path="in", output_path="out.xyz", media=MEDIA, graph="g", script_path="s"
        )
        assert "-c:a" not in argv

    def test_codec_override(self) -> None:
        argv = self.builder().build_argv(
            input_path="in.mp4",
            output_path="out.mp4",
            media=MEDIA,
            graph="g",
            script_path="s",
            audio_codec="libopus",
            audio_bitrate="96k",
        )
        assert argv[argv.index("-c:a") + 1] == "libopus"
        assert argv[argv.index("-b:a") + 1] == "96k"

    def test_faststart_for_mp4(self) -> None:
        argv = self.builder().build_argv(
            input_path="in.mp4", output_path="out.mp4", media=MEDIA, graph="g", script_path="s"
        )
        assert "+faststart" in argv
        argv = self.builder().build_argv(
            input_path="in.mkv", output_path="out.mkv", media=MEDIA, graph="g", script_path="s"
        )
        assert "+faststart" not in argv

    def test_metadata_is_copied(self) -> None:
        argv = self.builder().build_argv(
            input_path="in.mp4", output_path="out.mp4", media=MEDIA, graph="g", script_path="s"
        )
        assert "-map_metadata" in argv

    def test_copy_argv(self) -> None:
        argv = Engine().build_copy_argv(input_path="in.mp4", output_path="out.mp4")
        assert argv[-1] == "out.mp4"
        assert "copy" in argv

    def test_format_argv(self) -> None:
        assert format_argv(["ffmpeg", "-i", "a b.mp4"]) == "ffmpeg -i 'a b.mp4'"
        assert shlex.split(format_argv(["ffmpeg", "-i", "a b.mp4"])) == ["ffmpeg", "-i", "a b.mp4"]


@requires_ffmpeg
class TestExecution:
    def test_run_writes_output_and_keeps_script(self, audio_file: Path, tmp_path: Path) -> None:
        engine = Engine()
        media = engine.probe(audio_file)
        script = tmp_path / "graph.txt"
        output = tmp_path / "out.wav"
        graph = "[0:a]anull[aout]"
        result = engine.run(
            input_path=audio_file,
            output_path=output,
            media=media,
            graph=graph,
            keep_script=script,
        )
        assert result.returncode == 0
        assert output.exists() and output.stat().st_size > 1000
        assert script.read_text(encoding="utf-8") == graph

    def test_run_cleans_up_temp_script(self, audio_file: Path, tmp_path: Path) -> None:
        engine = Engine()
        media = engine.probe(audio_file)
        output = tmp_path / "out.wav"
        engine.run(input_path=audio_file, output_path=output, media=media, graph="[0:a]anull[aout]")
        assert output.exists()

    def test_run_reports_failure(self, audio_file: Path, tmp_path: Path) -> None:
        engine = Engine()
        media = engine.probe(audio_file)
        with pytest.raises(FFmpegError, match="FFmpeg failed"):
            engine.run(
                input_path=audio_file,
                output_path=tmp_path / "out.wav",
                media=media,
                graph="[0:a]notarealfilter[aout]",
            )

    def test_run_missing_ffmpeg(self, audio_file: Path, tmp_path: Path) -> None:
        engine = Engine(ffmpeg="ffmpeg-does-not-exist")
        engine._script_option = ""
        media = engine.probe(audio_file)
        with pytest.raises(FFmpegError, match="not found"):
            engine.run(
                input_path=audio_file,
                output_path=tmp_path / "out.wav",
                media=media,
                graph="[0:a]anull[aout]",
            )

    def test_probe_missing_ffmpeg(self, audio_file: Path) -> None:
        engine = Engine(ffprobe="ffprobe-does-not-exist")
        with pytest.raises(FFmpegError, match="not found"):
            engine.probe(audio_file)

    def test_script_option_is_cached(self) -> None:
        engine = Engine()
        first = engine.script_option
        second = engine.script_option
        assert first == second

    def test_remux_copies_streams(self, video_file: Path, tmp_path: Path) -> None:
        engine = Engine()
        output = tmp_path / "copy.mp4"
        result = engine.remux(input_path=video_file, output_path=output)
        assert result.returncode == 0
        assert output.exists()
