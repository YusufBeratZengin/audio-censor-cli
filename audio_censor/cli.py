"""Command line interface for audio-censor."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from . import __version__
from .audit import write_audit
from .engine import Engine, MediaInfo, format_argv
from .errors import AudioCensorError, ConfigError
from .filter_builder import AudioParams, build_filter_graph
from .matcher import BadWordMatcher, Interval, Match, build_intervals, resolve_words
from .parser import Transcript, load_transcript, shift_transcript
from .subtitles import DEFAULT_MASK, write_clean_srt
from .text import format_timecode

PROGRAM = "audio-censor"


@dataclass(frozen=True, slots=True)
class CliOptions:
    """Fully validated command line options."""

    input: Path
    subtitles: Path
    words: str
    mode: str
    output: Path
    fade_ms: float
    pad_ms: float
    offset_ms: float
    max_gap_ms: float
    fuzzy: int
    beep_freq: float
    beep_volume: float
    audit: Path | None
    clean_subtitles: Path | None
    mask: str
    audio_codec: str | None
    audio_bitrate: str | None
    ffmpeg: str
    ffprobe: str
    dry_run: bool
    json_output: bool
    quiet: bool
    keep_script: Path | None
    transcript_format: str | None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=(
            "Mask profanity or sensitive words in audio/video using a timestamped "
            "transcript (SRT, VTT or Whisper JSON) and FFmpeg."
        ),
        epilog="Example: audio-censor talk.mp4 -s talk.vtt --words badwords.txt --mode bleep",
    )
    parser.add_argument("input", type=Path, help="input audio or video file")
    parser.add_argument(
        "-s",
        "--subtitles",
        required=True,
        type=Path,
        help="timestamped transcript (.srt, .vtt or Whisper .json)",
    )
    parser.add_argument(
        "-w",
        "--words",
        "--bad-words",
        dest="words",
        required=True,
        help='word list file, or a comma separated list such as "fuck,shit"',
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=("bleep", "mute", "reverse"),
        default="bleep",
        help="masking mode (default: bleep, a 1 kHz sine tone)",
    )
    parser.add_argument("-o", "--output", type=Path, help="output file (default: <input>.censored)")
    parser.add_argument(
        "--fade-ms",
        type=float,
        default=5.0,
        help="micro fade length at every masking edge, in ms (default: 5)",
    )
    parser.add_argument(
        "--pad-ms",
        type=float,
        default=0.0,
        help="extend every masked range by this many ms on both sides (default: 0)",
    )
    parser.add_argument(
        "--offset-ms",
        type=float,
        default=0.0,
        help="shift the whole transcript by this many ms (negative values allowed)",
    )
    parser.add_argument(
        "--max-gap-ms",
        type=float,
        default=1500.0,
        help="maximum silence between words of a multi word phrase (default: 1500)",
    )
    parser.add_argument(
        "--fuzzy",
        type=int,
        default=0,
        metavar="N",
        help="also match words within N edits, e.g. --fuzzy 1 (default: 0, off)",
    )
    parser.add_argument("--beep-freq", type=float, default=1000.0, help="bleep frequency in Hz")
    parser.add_argument(
        "--beep-volume", type=float, default=0.3, help="bleep amplitude between 0 and 1"
    )
    parser.add_argument("--audit", type=Path, help="write a CSV audit log of every masked word")
    parser.add_argument(
        "--clean-subtitles",
        type=Path,
        help="write a censored copy of the subtitles (e.g. clean.srt)",
    )
    parser.add_argument(
        "--mask-text",
        default=DEFAULT_MASK,
        help=f"replacement text for --clean-subtitles (default: {DEFAULT_MASK})",
    )
    parser.add_argument("--audio-codec", help="override the output audio encoder")
    parser.add_argument("--audio-bitrate", help="override the output audio bitrate, e.g. 192k")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="path to the ffmpeg binary")
    parser.add_argument("--ffprobe", default="ffprobe", help="path to the ffprobe binary")
    parser.add_argument(
        "--transcript-format",
        choices=("srt", "vtt", "whisper"),
        help="force a transcript format instead of guessing from the file name",
    )
    parser.add_argument(
        "--keep-filter-script", type=Path, help="keep the generated filter graph at this path"
    )
    parser.add_argument("--dry-run", action="store_true", help="print the FFmpeg command only")
    parser.add_argument("--json", action="store_true", help="print a machine readable summary")
    parser.add_argument("-q", "--quiet", action="store_true", help="only print errors")
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {__version__}")
    return parser


def parse_args(argv: list[str] | None = None) -> CliOptions:
    """Parse ``argv`` into options, applying defaults."""
    args = build_parser().parse_args(argv)
    input_path: Path = args.input
    output: Path | None = args.output
    if output is None:
        output = input_path.with_name(f"{input_path.stem}.censored{input_path.suffix}")
    return CliOptions(
        input=input_path,
        subtitles=args.subtitles,
        words=args.words,
        mode=args.mode,
        output=output,
        fade_ms=float(args.fade_ms),
        pad_ms=float(args.pad_ms),
        offset_ms=float(args.offset_ms),
        max_gap_ms=float(args.max_gap_ms),
        fuzzy=int(args.fuzzy),
        beep_freq=float(args.beep_freq),
        beep_volume=float(args.beep_volume),
        audit=args.audit,
        clean_subtitles=args.clean_subtitles,
        mask=args.mask_text,
        audio_codec=args.audio_codec,
        audio_bitrate=args.audio_bitrate,
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
        dry_run=bool(args.dry_run),
        json_output=bool(args.json),
        quiet=bool(args.quiet),
        keep_script=args.keep_filter_script,
        transcript_format=args.transcript_format,
    )


class _Reporter:
    """Human output that stays out of the way of ``--json``."""

    def __init__(self, options: CliOptions, stdout: TextIO, stderr: TextIO) -> None:
        self.options = options
        self.stdout = stdout
        self.stderr = stderr
        self._stream = stderr if options.json_output else stdout

    @property
    def stream(self) -> TextIO:
        return self._stream

    def info(self, message: str) -> None:
        if not self.options.quiet:
            print(f"info: {message}", file=self._stream)

    def warn(self, message: str) -> None:
        print(f"warn: {message}", file=self.stderr)

    def ok(self, message: str) -> None:
        if not self.options.quiet:
            print(f"ok: {message}", file=self._stream)


def _check_output_paths(options: CliOptions) -> None:
    """Refuse to overwrite a source file or to reuse one output path twice."""
    sources = {"input path": options.input, "transcript path": options.subtitles}
    outputs: list[tuple[str, Path]] = [("output path", options.output)]
    if options.audit is not None:
        outputs.append(("--audit path", options.audit))
    if options.clean_subtitles is not None:
        outputs.append(("--clean-subtitles path", options.clean_subtitles))
    if options.keep_script is not None:
        outputs.append(("--keep-filter-script path", options.keep_script))

    seen: dict[Path, str] = {}
    for label, path in outputs:
        resolved = path.resolve()
        for source_label, source in sources.items():
            if resolved == source.resolve():
                raise ConfigError(f"{label} must differ from the {source_label}: {path}")
        if resolved in seen:
            raise ConfigError(f"{label} is the same path as the {seen[resolved]}: {path}")
        seen[resolved] = label


def _validate(options: CliOptions) -> None:
    if not options.input.exists():
        raise ConfigError(f"input file not found: {options.input}")
    if not options.input.is_file():
        raise ConfigError(f"input is not a file: {options.input}")
    if not options.subtitles.exists():
        raise ConfigError(f"transcript not found: {options.subtitles}")
    _check_output_paths(options)
    if options.fade_ms < 0:
        raise ConfigError("--fade-ms must be zero or positive")
    if options.pad_ms < 0:
        raise ConfigError("--pad-ms must be zero or positive")
    if not 0 < options.beep_volume <= 1:
        raise ConfigError("--beep-volume must be between 0 and 1")
    if options.fuzzy < 0:
        raise ConfigError("--fuzzy must be zero or positive")


def _describe_matches(matches: list[Match], intervals: list[Interval], stream: TextIO) -> None:
    for index, match in enumerate(matches, start=1):
        print(
            f"  #{index:<3d} {format_timecode(match.start)} - {format_timecode(match.end)} "
            f"({match.end - match.start:.3f}s)  {match.text!r}  cue {match.cue}"
            + ("  [fuzzy]" if match.fuzzy else ""),
            file=stream,
        )
    if len(intervals) != len(matches):
        print(
            f"  {len(matches)} match(es) merged into {len(intervals)} masked range(s)", file=stream
        )


def _summary(
    options: CliOptions,
    *,
    transcript: Transcript,
    matches: list[Match],
    intervals: list[Interval],
    output: Path | None,
    elapsed: float,
    command: list[str] | None,
    graph: str | None = None,
) -> dict[str, object]:
    return {
        "program": PROGRAM,
        "version": __version__,
        "input": str(options.input),
        "output": None if output is None else str(output),
        "subtitles": str(options.subtitles),
        "transcript_format": transcript.format,
        "mode": options.mode,
        "words": len(transcript.words),
        "matches": [
            {
                "start": round(match.start, 3),
                "end": round(match.end, 3),
                "text": match.text,
                "pattern": match.pattern,
                "cue": match.cue,
                "fuzzy": match.fuzzy,
            }
            for match in matches
        ],
        "intervals": [
            {"start": round(interval.start, 3), "end": round(interval.end, 3)}
            for interval in intervals
        ],
        "elapsed_seconds": round(elapsed, 3),
        "command": command,
        "filter_graph": graph,
    }


def _write_reports(
    options: CliOptions,
    *,
    transcript: Transcript,
    matches: list[Match],
    intervals: list[Interval],
    duration: float,
    reporter: _Reporter,
) -> None:
    """Write the optional audit log and censored subtitles."""
    if options.audit is not None:
        write_audit(
            options.audit,
            matches,
            intervals,
            mode=options.mode,
            pad=options.pad_ms / 1000.0,
            duration=duration,
        )
        reporter.info(f"audit log: {options.audit}")
    if options.clean_subtitles is not None:
        write_clean_srt(options.clean_subtitles, transcript, matches, mask=options.mask)
        reporter.info(f"censored subtitles: {options.clean_subtitles}")


def _report_planned_outputs(options: CliOptions, reporter: _Reporter) -> None:
    """Describe the files a real run would write, without creating them."""
    if options.audit is not None:
        reporter.info(f"dry run: would write audit log {options.audit}")
    if options.clean_subtitles is not None:
        reporter.info(f"dry run: would write censored subtitles {options.clean_subtitles}")
    if options.keep_script is not None:
        reporter.info(f"dry run: would keep the filter graph at {options.keep_script}")


def execute(options: CliOptions, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    """Run one censorship job. Returns the process exit code."""
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    reporter = _Reporter(options, out, err)
    started = time.monotonic()

    _validate(options)

    transcript = load_transcript(options.subtitles, format=options.transcript_format)
    if options.offset_ms:
        transcript = shift_transcript(transcript, options.offset_ms / 1000.0)
    reporter.info(
        f"transcript: {options.subtitles.name} ({transcript.format}, "
        f"{len(transcript.cues)} cues, {len(transcript.words)} words)"
    )

    matcher = BadWordMatcher(
        resolve_words(options.words),
        fuzzy=options.fuzzy,
        max_gap=options.max_gap_ms / 1000.0,
    )
    matches = matcher.find(transcript.words)

    engine = Engine(ffmpeg=options.ffmpeg, ffprobe=options.ffprobe)
    media: MediaInfo = engine.probe(options.input)
    reporter.info(
        f"media: {media.duration:.3f}s, {media.sample_rate} Hz, {media.channels} ch, "
        f"{'video copied' if media.has_video else 'audio only'}"
    )
    if transcript.duration > media.duration + 1.0:
        reporter.warn(
            f"transcript ends at {format_timecode(transcript.duration)} but the media is only "
            f"{format_timecode(media.duration)} long; check --offset-ms if subtitles are shifted"
        )

    intervals = build_intervals(
        matches,
        pad=options.pad_ms / 1000.0,
        fade=options.fade_ms / 1000.0,
        duration=media.duration,
    )

    if not intervals:
        reporter.info("no bad words matched")
        if options.dry_run:
            reporter.info("nothing to do (dry run)")
            _report_planned_outputs(options, reporter)
            if options.json_output:
                print(
                    json.dumps(
                        _summary(
                            options,
                            transcript=transcript,
                            matches=matches,
                            intervals=[],
                            output=None,
                            elapsed=time.monotonic() - started,
                            command=None,
                        ),
                        indent=2,
                    ),
                    file=out,
                )
            return 0
        command = engine.build_copy_argv(input_path=options.input, output_path=options.output)
        options.output.parent.mkdir(parents=True, exist_ok=True)
        engine.remux(input_path=options.input, output_path=options.output)
        _write_reports(
            options,
            transcript=transcript,
            matches=matches,
            intervals=[],
            duration=media.duration,
            reporter=reporter,
        )
        elapsed = time.monotonic() - started
        reporter.ok(f"nothing to mask, streams copied to {options.output} in {elapsed:.2f}s")
        if options.json_output:
            print(
                json.dumps(
                    _summary(
                        options,
                        transcript=transcript,
                        matches=matches,
                        intervals=[],
                        output=options.output,
                        elapsed=elapsed,
                        command=command,
                    ),
                    indent=2,
                ),
                file=out,
            )
        return 0

    reporter.info(
        f"{len(matches)} match(es) -> {len(intervals)} masked range(s) in {options.mode} mode"
    )
    if not options.quiet:
        _describe_matches(matches, intervals, reporter.stream)

    params = AudioParams(
        sample_rate=media.sample_rate,
        channels=media.channels,
        duration=media.duration,
    )
    graph = build_filter_graph(
        params,
        intervals,
        mode=options.mode,
        fade_ms=options.fade_ms,
        beep_freq=options.beep_freq,
        beep_volume=options.beep_volume,
    )

    if options.dry_run:
        # A dry run must not touch the filesystem: no audit CSV, no censored
        # subtitles and no retained filter script.  In --json mode stdout must
        # contain exactly one JSON document, so the human command listing is
        # suppressed and the graph travels inside the document instead.
        option = engine.script_option
        argv = engine.build_argv(
            input_path=options.input,
            output_path=options.output,
            media=media,
            graph=graph,
            script_path="<filter-script>" if option else None,
            audio_codec=options.audio_codec,
            audio_bitrate=options.audio_bitrate,
        )
        if options.json_output:
            print(
                json.dumps(
                    _summary(
                        options,
                        transcript=transcript,
                        matches=matches,
                        intervals=intervals,
                        output=options.output,
                        elapsed=time.monotonic() - started,
                        command=argv,
                        graph=graph,
                    ),
                    indent=2,
                ),
                file=out,
            )
        else:
            print(format_argv(argv), file=out)
            print("# filter_complex:", file=out)
            print(graph, file=out)
            _report_planned_outputs(options, reporter)
        return 0

    _write_reports(
        options,
        transcript=transcript,
        matches=matches,
        intervals=intervals,
        duration=media.duration,
        reporter=reporter,
    )

    options.output.parent.mkdir(parents=True, exist_ok=True)
    result = engine.run(
        input_path=options.input,
        output_path=options.output,
        media=media,
        graph=graph,
        audio_codec=options.audio_codec,
        audio_bitrate=options.audio_bitrate,
        keep_script=options.keep_script,
    )
    elapsed = time.monotonic() - started
    verb = {
        "bleep": "beeps mixed in",
        "mute": "silence applied",
        "reverse": "reversed audio applied",
    }[options.mode]
    reporter.ok(f"{verb}: {options.output} ({elapsed:.2f}s)")
    if options.json_output:
        print(
            json.dumps(
                _summary(
                    options,
                    transcript=transcript,
                    matches=matches,
                    intervals=intervals,
                    output=options.output,
                    elapsed=elapsed,
                    command=result.argv,
                ),
                indent=2,
            ),
            file=out,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point used by the ``audio-censor`` console script."""
    try:
        options = parse_args(argv)
    except SystemExit as exc:  # argparse already printed the message
        return int(exc.code or 0)
    try:
        return execute(options)
    except AudioCensorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if getattr(exc, "stderr", ""):
            print(exc.stderr, file=sys.stderr)  # type: ignore[attr-defined]
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
