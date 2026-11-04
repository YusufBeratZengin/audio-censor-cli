"""Build the FFmpeg ``filter_complex`` graph that masks time intervals.

Design notes
------------
Nothing here evaluates a per-sample expression with hundreds of branches.
Instead a *gain envelope* is assembled from constant sources:

``pulse_i``  ``aevalsrc=-1`` with a 5 ms fade in/out, delayed to the interval
             start, so summing every pulse gives ``-gate``;
``gate``     ``1 + sum(pulses)`` -- one DC source mixed with the pulses;
``duck``     ``main * gate`` through ``amultiply`` (sample accurate);
``replacement`` a sine tone (bleep) or reversed slices (reverse), mixed on top.

Every ramp is produced by ``afade`` on a constant signal, which is sample
accurate and costs O(1) per sample, unlike nested ``if(between(...))``
expressions that FFmpeg also limits to roughly 4 KiB of source text.

All generated streams are mono and panned to the source channel count at the
end, which keeps ``amix``/``amultiply`` layouts consistent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .errors import ConfigError
from .matcher import Interval

MODES = ("bleep", "mute", "reverse")

#: FFmpeg's expression parser rejects very long expressions, so generated
#: graphs are split into chunks of this many inputs per ``amix`` node.
DEFAULT_CHUNK = 8


@dataclass(frozen=True, slots=True)
class AudioParams:
    """Properties of the source audio stream."""

    sample_rate: int
    channels: int
    duration: float

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ConfigError("sample rate must be positive")
        if self.channels <= 0:
            raise ConfigError("channel count must be positive")
        if not math.isfinite(self.duration) or self.duration <= 0:
            raise ConfigError("duration must be a positive number")


def _seconds(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


@dataclass
class FilterBuilder:
    """Assemble a filter graph for one masking mode."""

    params: AudioParams
    mode: str = "bleep"
    fade_ms: float = 5.0
    beep_freq: float = 1000.0
    beep_volume: float = 0.3
    chunk_size: int = DEFAULT_CHUNK
    _lines: list[str] = field(default_factory=list, init=False, repr=False)
    _counter: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ConfigError(f"unknown mode {self.mode!r}; expected one of {', '.join(MODES)}")
        if not math.isfinite(self.fade_ms):
            raise ConfigError("fade must be a finite number of milliseconds")
        if self.fade_ms < 0:
            raise ConfigError("fade must be zero or positive")
        if self.fade_ms > 500:
            raise ConfigError("fade must not exceed 500 ms")
        if not math.isfinite(self.beep_freq):
            raise ConfigError("beep frequency must be a finite number of Hz")
        if self.beep_freq <= 0:
            raise ConfigError("beep frequency must be positive")
        if not 0 < self.beep_volume <= 1:
            raise ConfigError("beep volume must be inside (0, 1]")
        if self.chunk_size < 2:
            raise ConfigError("chunk size must be at least 2")

    # ------------------------------------------------------------------ utils
    @property
    def fade(self) -> float:
        return self.fade_ms / 1000.0

    def _label(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}{self._counter}"

    def _pan(self) -> str:
        if self.params.channels == 1:
            return ""
        taps = "|".join(f"c{index}=c0" for index in range(self.params.channels))
        return f",pan={self.params.channels}c|{taps}"

    def _segment_fade(self, length: float) -> str:
        fade = min(self.fade, length / 2.0)
        if fade <= 0.0001:
            return ""
        return (
            f",afade=t=in:st=0:d={_seconds(fade)}"
            f",afade=t=out:st={_seconds(max(0.0, length - fade))}:d={_seconds(fade)}"
        )

    def _split_source(self, intervals: int) -> list[str]:
        """Fan the source out once so every reverse slice gets its own copy.

        A filter output label can only be consumed once, so reverse mode needs
        ``asplit``: index 0 stays the ducked main stream, the rest feed the
        per-interval ``atrim`` branches.
        """
        labels = [f"src{index}" for index in range(intervals + 1)]
        outputs = "".join(f"[{label}]" for label in labels)
        self._lines.append(f"[src]asplit={len(labels)}{outputs}")
        return labels[1:]

    def _mix(self, labels: list[str], prefix: str) -> str:
        """Mix labels in chunks so no ``amix`` node gets hundreds of inputs."""
        if not labels:
            raise ConfigError("nothing to mix")
        if len(labels) == 1:
            return labels[0]
        results: list[str] = []
        for start in range(0, len(labels), self.chunk_size):
            group = labels[start : start + self.chunk_size]
            if len(group) == 1:
                results.append(group[0])
                continue
            out = self._label(prefix)
            self._lines.append(
                f"{''.join(group)}amix=inputs={len(group)}:normalize=0:duration=longest[{out}]"
            )
            results.append(f"[{out}]")
        return results[0] if len(results) == 1 else self._mix(results, prefix)

    # ---------------------------------------------------------------- building
    def build(self, intervals: list[Interval] | tuple[Interval, ...]) -> str:
        """Return the complete filter graph, ending in ``[aout]``."""
        ordered = sorted(intervals)
        if not ordered:
            raise ConfigError("at least one interval is required to build a filter graph")

        rate = self.params.sample_rate
        duration = self.params.duration
        self._lines = []
        self._counter = 0

        self._lines.append(f"[0:a]aformat=sample_fmts=fltp:sample_rates={rate}[src]")

        source = "src"
        reverse_sources: list[str] = []
        if self.mode == "reverse":
            reverse_sources = self._split_source(len(ordered))
            source = "src0"

        gate = self._gate(ordered, duration)
        duck_label = self._label("duck")
        self._lines.append(f"[{source}][{gate}]amultiply[{duck_label}]")

        if self.mode == "mute":
            replacement: str | None = None
        elif self.mode == "bleep":
            replacement = self._beep_track(ordered)
        else:
            replacement = self._reverse_track(ordered, reverse_sources)

        if replacement is None:
            self._lines.append(f"[{duck_label}]aformat=sample_fmts=fltp:sample_rates={rate}[aout]")
        else:
            self._lines.append(
                f"[{duck_label}][{replacement}]"
                f"amix=inputs=2:normalize=0:duration=first,"
                f"aformat=sample_fmts=fltp:sample_rates={rate}[aout]"
            )
        return ";\n".join(self._lines) + "\n"

    def _gate(self, intervals: list[Interval], duration: float) -> str:
        """Build a 0/1 gain envelope with sample accurate 5 ms ramps."""
        rate = self.params.sample_rate
        dc_duration = max(duration, intervals[-1].end) + 1.0
        pulses: list[str] = []
        for interval in intervals:
            length = interval.duration
            if length <= 0:
                continue
            label = self._label("pulse")
            delay_ms = interval.start * 1000.0
            self._lines.append(
                f"aevalsrc=exprs=-1:s={rate}:d={_seconds(length)}"
                f"{self._segment_fade(length)}"
                f",adelay=delays={delay_ms:.3f}:all=1[{label}]"
            )
            pulses.append(f"[{label}]")
        if not pulses:
            raise ConfigError("no usable intervals to mask")

        summed = self._mix(pulses, "gsum")
        self._lines.append(f"aevalsrc=exprs=1:s={rate}:d={_seconds(dc_duration)}[dc]")
        gate_label = self._label("gate")
        self._lines.append(
            f"[dc]{summed}amix=inputs=2:normalize=0:duration=first"
            f",aformat=sample_fmts=fltp{self._pan()}[{gate_label}]"
        )
        return gate_label

    def _beep_track(self, intervals: list[Interval]) -> str:
        rate = self.params.sample_rate
        expression = f"{self.beep_volume}*sin(2*PI*{self.beep_freq}*t)"
        labels: list[str] = []
        for interval in intervals:
            length = interval.duration
            if length <= 0:
                continue
            label = self._label("beep")
            self._lines.append(
                f"aevalsrc=exprs='{expression}':s={rate}:d={_seconds(length)}"
                f"{self._segment_fade(length)}"
                f",adelay=delays={interval.start * 1000.0:.3f}:all=1[{label}]"
            )
            labels.append(f"[{label}]")
        mixed = self._mix(labels, "bsum")
        out = self._label("beepmix")
        self._lines.append(f"{mixed}aformat=sample_fmts=fltp{self._pan()}[{out}]")
        return out

    def _reverse_track(self, intervals: list[Interval], sources: list[str]) -> str:
        """Reverse each interval in place.

        ``areverse`` buffers a whole slice, so slices are taken with ``atrim``
        first: memory stays proportional to the masked speech, never to the
        length of the file.
        """
        segments: list[str] = []
        for interval, source in zip(intervals, sources, strict=True):
            length = interval.duration
            if length <= 0:
                continue
            label = self._label("rev")
            self._lines.append(
                f"[{source}]atrim=start={_seconds(interval.start)}:end={_seconds(interval.end)}"
                f",asetpts=PTS-STARTPTS,areverse,asetpts=PTS-STARTPTS"
                f"{self._segment_fade(length)}"
                f",adelay=delays={interval.start * 1000.0:.3f}:all=1[{label}]"
            )
            segments.append(f"[{label}]")
        mixed = self._mix(segments, "rsum")
        out = self._label("revmix")
        self._lines.append(f"{mixed}aformat=sample_fmts=fltp{self._pan()}[{out}]")
        return out


def build_filter_graph(
    params: AudioParams,
    intervals: list[Interval] | tuple[Interval, ...],
    *,
    mode: str = "bleep",
    fade_ms: float = 5.0,
    beep_freq: float = 1000.0,
    beep_volume: float = 0.3,
) -> str:
    """Convenience wrapper around :class:`FilterBuilder`."""
    builder = FilterBuilder(
        params=params,
        mode=mode,
        fade_ms=fade_ms,
        beep_freq=beep_freq,
        beep_volume=beep_volume,
    )
    return builder.build(intervals)
