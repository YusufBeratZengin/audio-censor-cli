"""FFmpeg/FFprobe execution: probe the source, feed the graph, mux losslessly."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import FFmpegError


@dataclass(frozen=True, slots=True)
class MediaInfo:
    """Everything the filter builder needs to know about the source."""

    path: str
    duration: float
    sample_rate: int
    channels: int
    has_video: bool
    audio_codec: str | None = None
    video_codec: str | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Result of one FFmpeg invocation."""

    argv: list[str]
    stdout: str
    stderr: str
    returncode: int = 0


#: container -> (audio encoder, bitrate).  ``None`` bitrate means lossless or
#: encoder default.  Unknown containers fall back to FFmpeg's default encoder.
AUDIO_ENCODERS: dict[str, tuple[str | None, str | None]] = {
    ".mp4": ("aac", "192k"),
    ".m4a": ("aac", "192k"),
    ".m4v": ("aac", "192k"),
    ".mov": ("aac", "192k"),
    ".mkv": ("aac", "192k"),
    ".ts": ("aac", "192k"),
    ".avi": ("aac", "192k"),
    ".webm": ("libopus", "128k"),
    ".ogg": ("libopus", "128k"),
    ".opus": ("libopus", "96k"),
    ".mp3": ("libmp3lame", "192k"),
    ".wav": ("pcm_s16le", None),
    ".flac": ("flac", None),
    ".m4b": ("aac", "128k"),
}

_FASTSTART_SUFFIXES = {".mp4", ".m4a", ".m4v", ".mov"}


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def probe(path: str | Path, *, ffprobe: str = "ffprobe", timeout: float = 60.0) -> MediaInfo:
    """Read stream properties with FFprobe."""
    argv = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise FFmpegError(
            f"{ffprobe!r} was not found; install FFmpeg and make sure ffprobe is on PATH"
        ) from exc
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - defensive
        raise FFmpegError(f"ffprobe timed out after {timeout} seconds") from exc
    if completed.returncode != 0:
        raise FFmpegError(
            f"ffprobe could not read {path}",
            command=argv,
            stderr=completed.stderr.strip(),
        )
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise FFmpegError("ffprobe returned invalid JSON", command=argv) from exc

    streams = payload.get("streams") or []
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    if audio is None:
        raise FFmpegError(f"{path} has no audio stream; nothing to censor")

    sample_rate = _as_int(audio.get("sample_rate"))
    channels = _as_int(audio.get("channels"))
    if not sample_rate or not channels:
        raise FFmpegError(f"could not determine audio format of {path}")

    duration = _as_float(payload.get("format", {}).get("duration"))
    if duration is None:
        duration = _as_float(audio.get("duration"))
    if duration is None:
        raise FFmpegError(f"could not determine duration of {path}")

    return MediaInfo(
        path=str(path),
        duration=duration,
        sample_rate=sample_rate,
        channels=channels,
        has_video=video is not None,
        audio_codec=audio.get("codec_name"),
        video_codec=(video or {}).get("codec_name"),
    )


def ffmpeg_version(ffmpeg: str = "ffmpeg") -> tuple[int, ...]:
    """Return the FFmpeg version tuple, e.g. ``(7, 0, 1)``."""
    try:
        completed = subprocess.run(
            [ffmpeg, "-version"], capture_output=True, text=True, timeout=30, check=False
        )
    except FileNotFoundError as exc:
        raise FFmpegError(
            f"{ffmpeg!r} was not found; install FFmpeg and make sure it is on PATH"
        ) from exc
    first = (completed.stdout or completed.stderr or "").splitlines()
    if not first:
        return (0,)
    parts = first[0].split()
    for part in parts:
        if part[:1].isdigit():
            numbers = []
            for chunk in part.split("-")[0].split("."):
                if not chunk.isdigit():
                    break
                numbers.append(int(chunk))
            if numbers:
                return tuple(numbers)
    return (0,)


def script_option_for(ffmpeg: str = "ffmpeg", *, help_text: str | None = None) -> str:
    """Pick the CLI flag used to feed a filter graph from a file.

    FFmpeg 9 removed ``-filter_complex_script`` in favour of the generic
    ``-/filter_complex <file>`` syntax; older builds still need the classic
    option.  Returns an empty string when the graph has to be passed inline.
    """
    if help_text is None:
        try:
            completed = subprocess.run(
                [ffmpeg, "-hide_banner", "-h", "long"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            help_text = (completed.stdout or "") + (completed.stderr or "")
        except FileNotFoundError as exc:
            raise FFmpegError(
                f"{ffmpeg!r} was not found; install FFmpeg and make sure it is on PATH"
            ) from exc
    if "filter_complex_script" in help_text:
        return "-filter_complex_script"
    if ffmpeg_version(ffmpeg) >= (7, 1):
        return "-/filter_complex"
    return ""


class Engine:
    """Thin, testable wrapper around the FFmpeg command line."""

    def __init__(self, *, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self._script_option: str | None = None

    # ------------------------------------------------------------------ setup
    def probe(self, path: str | Path) -> MediaInfo:
        return probe(path, ffprobe=self.ffprobe)

    @property
    def script_option(self) -> str:
        if self._script_option is None:
            self._script_option = script_option_for(self.ffmpeg)
        return self._script_option

    def encoder_for(self, output: str | Path) -> tuple[str | None, str | None]:
        return AUDIO_ENCODERS.get(Path(output).suffix.lower(), (None, None))

    # ---------------------------------------------------------------- commands
    def build_argv(
        self,
        *,
        input_path: str | Path,
        output_path: str | Path,
        media: MediaInfo,
        graph: str,
        script_path: str | Path | None = None,
        audio_codec: str | None = None,
        audio_bitrate: str | None = None,
    ) -> list[str]:
        """Assemble the FFmpeg arguments. Copy the video stream untouched."""
        option = self.script_option
        argv = [self.ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(input_path)]
        if option and script_path is not None:
            argv += [option, str(script_path)]
        else:
            argv += ["-filter_complex", graph]
        argv += ["-map", "[aout]"]
        if media.has_video:
            argv += ["-map", "0:v?", "-c:v", "copy"]
        argv += ["-map_metadata", "0"]

        default_codec, default_bitrate = self.encoder_for(output_path)
        codec = audio_codec if audio_codec is not None else default_codec
        bitrate = audio_bitrate if audio_bitrate is not None else default_bitrate
        if codec:
            argv += ["-c:a", codec]
        if bitrate:
            argv += ["-b:a", bitrate]
        if Path(output_path).suffix.lower() in _FASTSTART_SUFFIXES:
            argv += ["-movflags", "+faststart"]
        argv.append(str(output_path))
        return argv

    def build_copy_argv(self, *, input_path: str | Path, output_path: str | Path) -> list[str]:
        """Remux without touching any stream (fast path when nothing matched)."""
        return [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(input_path),
            "-map",
            "0",
            "-c",
            "copy",
            str(output_path),
        ]

    # ------------------------------------------------------------------- runs
    def _execute(self, argv: list[str], *, timeout: float | None) -> CommandResult:
        try:
            completed = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout, check=False
            )
        except FileNotFoundError as exc:
            raise FFmpegError(
                f"{self.ffmpeg!r} was not found; install FFmpeg and make sure it is on PATH"
            ) from exc
        except subprocess.TimeoutExpired as exc:  # pragma: no cover - defensive
            raise FFmpegError(f"FFmpeg timed out after {timeout} seconds") from exc
        result = CommandResult(
            argv=argv,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            returncode=completed.returncode,
        )
        if completed.returncode != 0:
            tail = "\n".join(result.stderr.strip().splitlines()[-12:])
            raise FFmpegError(
                f"FFmpeg failed with exit code {completed.returncode}",
                command=argv,
                stderr=tail,
            )
        return result

    def run(
        self,
        *,
        input_path: str | Path,
        output_path: str | Path,
        media: MediaInfo,
        graph: str,
        audio_codec: str | None = None,
        audio_bitrate: str | None = None,
        keep_script: str | Path | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        """Render ``graph`` and mux it with the untouched video stream."""
        option = self.script_option
        workdir: str | None = None
        script_path: str | Path | None = None
        if option:
            if keep_script is not None:
                script_path = keep_script
            else:
                workdir = tempfile.mkdtemp(prefix="audio-censor-")
                script_path = Path(workdir) / "filter_complex.txt"
            Path(script_path).write_text(graph, encoding="utf-8")
        try:
            argv = self.build_argv(
                input_path=input_path,
                output_path=output_path,
                media=media,
                graph=graph,
                script_path=script_path,
                audio_codec=audio_codec,
                audio_bitrate=audio_bitrate,
            )
            return self._execute(argv, timeout=timeout)
        finally:
            if workdir:
                shutil.rmtree(workdir, ignore_errors=True)

    def remux(
        self,
        *,
        input_path: str | Path,
        output_path: str | Path,
        timeout: float | None = None,
    ) -> CommandResult:
        argv = self.build_copy_argv(input_path=input_path, output_path=output_path)
        return self._execute(argv, timeout=timeout)


def format_argv(argv: list[str]) -> str:
    """Render an argument list the way a user would type it."""
    return shlex.join(argv)
