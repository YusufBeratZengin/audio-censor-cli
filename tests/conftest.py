"""Shared fixtures and audio helpers for the test suite.

The suite never touches the network.  Integration tests need FFmpeg/FFprobe and
are skipped automatically when the binaries are missing.
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import wave
from collections.abc import Sequence
from pathlib import Path

import pytest

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = pytest.mark.skipif(
    FFMPEG is None or FFPROBE is None, reason="FFmpeg and FFprobe are required"
)

SAMPLE_RATE = 48000
DURATION = 6.0


def run_ffmpeg(*args: str) -> None:
    """Run FFmpeg and raise with its stderr when it fails."""
    assert FFMPEG is not None
    completed = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(f"ffmpeg failed: {completed.stderr}")


def read_wav(path: str | Path) -> tuple[list[float], int]:
    """Read a 16 bit PCM wav file as mono float samples in [-1, 1]."""
    with wave.open(str(path), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(frames)
    data = struct.unpack(f"<{frames * channels}h", raw)
    mono = [value / 32768.0 for value in data[::channels]]
    return mono, rate


def tone_amplitude(
    samples: Sequence[float],
    sample_rate: int,
    frequency: float,
    start: float,
    end: float,
) -> float:
    """Single bin DFT amplitude of ``frequency`` between ``start`` and ``end``."""
    first = max(0, round(start * sample_rate))
    last = min(len(samples), round(end * sample_rate))
    window = samples[first:last]
    if len(window) < 10:
        return 0.0
    real = 0.0
    imaginary = 0.0
    omega = 2.0 * math.pi * frequency / sample_rate
    for index, value in enumerate(window):
        real += value * math.cos(omega * index)
        imaginary += value * math.sin(omega * index)
    return 2.0 * math.hypot(real, imaginary) / len(window)


def rms(samples: Sequence[float], sample_rate: int, start: float, end: float) -> float:
    first = max(0, round(start * sample_rate))
    last = min(len(samples), round(end * sample_rate))
    window = samples[first:last]
    if not window:
        return 0.0
    return math.sqrt(sum(value * value for value in window) / len(window))


def max_step(samples: Sequence[float]) -> float:
    """Largest sample-to-sample jump, a proxy for click/pop artifacts."""
    return max((abs(samples[i + 1] - samples[i]) for i in range(len(samples) - 1)), default=0.0)


def md5_stream(path: str | Path, selector: str) -> str:
    """MD5 of one stream, copied bit for bit (used to prove -c:v copy)."""
    assert FFMPEG is not None
    completed = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            selector,
            "-c",
            "copy",
            "-f",
            "md5",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(f"ffmpeg md5 failed: {completed.stderr}")
    return completed.stdout.strip()


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    """Six seconds of three different tones (makes reversals detectable)."""
    path = tmp_path / "input.wav"
    run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate={SAMPLE_RATE}:duration=2",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=880:sample_rate={SAMPLE_RATE}:duration=2",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=300:sample_rate={SAMPLE_RATE}:duration=2",
        "-filter_complex",
        "[0:a][1:a][2:a]concat=n=3:v=0:a=1,volume=4[a]",
        "-map",
        "[a]",
        "-c:a",
        "pcm_s16le",
        str(path),
    )
    return path


@pytest.fixture
def video_file(tmp_path: Path) -> Path:
    """Small H.264 + AAC clip used to prove the video stream is copied."""
    path = tmp_path / "input.mp4"
    run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=160x120:rate=15:duration={DURATION}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate={SAMPLE_RATE}:duration={DURATION}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    )
    return path


@pytest.fixture
def srt_file(tmp_path: Path) -> Path:
    path = tmp_path / "subs.srt"
    path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nhello fucking world\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nshit happens here\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def words_file(tmp_path: Path) -> Path:
    path = tmp_path / "badwords.txt"
    path.write_text("# demo list\nshit\nfuck\n", encoding="utf-8")
    return path
