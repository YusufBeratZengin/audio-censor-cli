#!/usr/bin/env python3
"""Reproduce the numbers in the README's Performance table.

The script builds its own synthetic inputs with FFmpeg (no downloads, no
personal data), runs the CLI twice and prints a Markdown table in the same
shape as the README.  Everything it needs is in the standard library plus
FFmpeg/FFprobe on ``PATH`` (or ``$FFMPEG``/``$FFPROBE``).

Usage::

    python3 scripts/benchmark.py             # the README runs (20 min + 60 s)
    python3 scripts/benchmark.py --quick     # ~30 s smoke run, used by CI
    python3 scripts/benchmark.py --keep DIR  # keep the generated media in DIR
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def find_tool(variable: str, name: str) -> str:
    """Resolve one FFmpeg binary from the environment or ``PATH``."""
    candidate = os.environ.get(variable) or shutil.which(name)
    if not candidate:
        raise SystemExit(f"error: {name} not found; set {variable} or put it on PATH")
    return candidate


FFMPEG = find_tool("FFMPEG", "ffmpeg")


def run(argv: list[str]) -> None:
    completed = subprocess.run(argv, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"error: {argv[0]} failed:\n{completed.stderr.strip()[-1500:]}")


def make_wav(path: Path, seconds: float, rate: int = 48000) -> None:
    """Stereo 48 kHz WAV: a 300 Hz left / 440 Hz right bed to be masked."""
    run(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=300:sample_rate={rate}:duration={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate={rate}:duration={seconds}",
            "-filter_complex",
            "[0:a][1:a]join=inputs=2:channel_layout=stereo[a]",
            "-map",
            "[a]",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
    )


def make_video(path: Path, seconds: float, width: int, height: int) -> None:
    """H.264 + AAC clip; the picture is what must survive untouched."""
    run(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:rate=30:duration={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={seconds}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(path),
        ]
    )


def duration_label(seconds: float) -> str:
    """``1200`` -> ``20 min``; anything shorter stays in seconds."""
    return f"{seconds / 60:.0f} min" if seconds >= 120 else f"{seconds:.0f} s"


def write_transcript(path: Path, seconds: float, every: float = 10.0) -> int:
    """An SRT cue saying "bleep" every ``every`` seconds; returns the cue count."""

    def stamp(value: float) -> str:
        hours, rest = divmod(value, 3600)
        minutes, secs = divmod(rest, 60)
        return f"{int(hours):02d}:{int(minutes):02d}:{secs:06.3f}".replace(".", ",")

    cues: list[str] = []
    position = 2.0
    while position + 0.5 <= seconds - 1.0:
        cues.append(f"{len(cues) + 1}\n{stamp(position)} --> {stamp(position + 0.5)}\nbleep\n")
        position += every
    path.write_text("\n".join(cues), encoding="utf-8")
    return len(cues)


def stream_md5(path: Path, selector: str) -> str:
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
        check=True,
    )
    return completed.stdout.strip()


def file_fingerprint(path: Path) -> str:
    """Cheap identity for large files: size + first/last MiB."""
    size = path.stat().st_size
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        digest.update(handle.read(1 << 20))
        handle.seek(max(0, size - (1 << 20)))
        digest.update(handle.read(1 << 20))
    return f"{size}:{digest.hexdigest()[:16]}"


def timed_run(media: Path, transcript: Path, words: Path, output: Path, *extra: str) -> float:
    """Run the real CLI in a child process and return wall clock seconds."""
    command = [
        sys.executable,
        "-m",
        "audio_censor",
        str(media),
        "--subtitles",
        str(transcript),
        "--words",
        str(words),
        "-o",
        str(output),
        *extra,
    ]
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    started = time.monotonic()
    completed = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        raise SystemExit(f"error: audio_censor failed:\n{completed.stderr.strip()[-1500:]}")
    if not output.exists():
        raise SystemExit(f"error: no output written at {output}")
    return elapsed


def print_platform() -> None:
    """Print the machine line; the FFmpeg version comes from the binary itself."""
    version = subprocess.run([FFMPEG, "-version"], capture_output=True, text=True, check=False)
    first = (version.stdout or "").splitlines()
    label = first[0] if first else "unknown FFmpeg"
    print(f"# {platform.system()}, Python {sys.version.split()[0]}, {label}".strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quick", action="store_true", help="short runs for CI smoke tests")
    parser.add_argument("--keep", metavar="DIR", help="keep generated inputs in DIR")
    args = parser.parse_args()

    audio_seconds = 30.0 if args.quick else 1200.0
    video_seconds = 10.0 if args.quick else 60.0
    size = (640, 360) if args.quick else (1920, 1080)
    cue_gap = 4.0 if args.quick else 10.0

    workdir = Path(args.keep).resolve() if args.keep else Path(tempfile.mkdtemp(prefix="bench-"))
    workdir.mkdir(parents=True, exist_ok=True)
    words = workdir / "words.txt"
    words.write_text("# synthetic list used by scripts/benchmark.py\nbleep\n", encoding="utf-8")

    print_platform()
    print(f"# inputs: {workdir}")
    print()
    print("| Input | Masked ranges | Output | Time |")
    print("|---|---|---|---|")

    # --- audio only -------------------------------------------------------
    wav = workdir / "input.wav"
    srt = workdir / "input.srt"
    print("# generating a 48 kHz stereo WAV ...", file=sys.stderr)
    make_wav(wav, audio_seconds)
    cues = write_transcript(srt, audio_seconds, cue_gap)
    m4a = workdir / "out.m4a"
    elapsed = timed_run(wav, srt, words, m4a, "--mode", "bleep", "--fade-ms", "5")
    print(
        f"| {duration_label(audio_seconds)}, 48 kHz stereo WAV | {cues} beeps | "
        f"{m4a.stat().st_size / 1e6:.0f} MB M4A (AAC 192k) | {elapsed:.2f} s |"
    )

    # --- video: the point is that the picture is a stream copy -----------
    video = workdir / "input.mp4"
    vsrt = workdir / "video.srt"
    print("# generating an H.264/AAC clip ...", file=sys.stderr)
    make_video(video, video_seconds, *size)
    cues = write_transcript(vsrt, video_seconds, cue_gap)
    before = stream_md5(video, "0:v")
    out = workdir / "out.mp4"
    elapsed = timed_run(video, vsrt, words, out, "--mode", "bleep", "--fade-ms", "5")
    after = stream_md5(out, "0:v")
    height = "1080p" if size[1] == 1080 else f"{size[1]}p"
    print(
        f"| {video_seconds:.0f} s, {height} H.264 + AAC | {cues} beeps | "
        f"video MD5 {'same' if before == after else 'CHANGED'}, AAC audio | {elapsed:.2f} s |"
    )
    print()
    print(f"# video stream MD5: {before if before == after else before + ' -> ' + after}")
    print(f"# input WAV fingerprint: {file_fingerprint(wav)}")
    if before != after:
        raise SystemExit("error: the video stream changed; -c:v copy did not hold")

    if args.keep:
        print(f"# kept inputs in {workdir}")
    else:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
