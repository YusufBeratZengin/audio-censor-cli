"""Unit tests for the FFmpeg filter graph builder."""

from __future__ import annotations

import re

import pytest

from audio_censor.errors import ConfigError
from audio_censor.filter_builder import AudioParams, FilterBuilder, build_filter_graph
from audio_censor.matcher import Interval

PARAMS = AudioParams(sample_rate=48000, channels=1, duration=10.0)


def intervals(*ranges: tuple[float, float]) -> list[Interval]:
    return [Interval(start=start, end=end) for start, end in ranges]


class TestAudioParams:
    def test_rejects_bad_sample_rate(self) -> None:
        with pytest.raises(ConfigError):
            AudioParams(sample_rate=0, channels=1, duration=1.0)

    def test_rejects_bad_channels(self) -> None:
        with pytest.raises(ConfigError):
            AudioParams(sample_rate=48000, channels=0, duration=1.0)

    @pytest.mark.parametrize("duration", [0.0, -1.0, float("inf")])
    def test_rejects_bad_duration(self, duration: float) -> None:
        with pytest.raises(ConfigError):
            AudioParams(sample_rate=48000, channels=1, duration=duration)


class TestBuilderValidation:
    def test_unknown_mode(self) -> None:
        with pytest.raises(ConfigError, match="unknown mode"):
            FilterBuilder(PARAMS, mode="explode")

    def test_negative_fade(self) -> None:
        with pytest.raises(ConfigError, match="fade"):
            FilterBuilder(PARAMS, fade_ms=-1)

    def test_huge_fade(self) -> None:
        with pytest.raises(ConfigError, match="500"):
            FilterBuilder(PARAMS, fade_ms=501)

    def test_bad_frequency(self) -> None:
        with pytest.raises(ConfigError, match="frequency"):
            FilterBuilder(PARAMS, beep_freq=0)

    def test_non_finite_fade_and_frequency(self) -> None:
        # ``nan`` slips through every ``<``/``>`` comparison, so these need an
        # explicit finiteness check instead of a range check.
        with pytest.raises(ConfigError, match="fade must be a finite"):
            FilterBuilder(PARAMS, fade_ms=float("nan"))
        with pytest.raises(ConfigError, match="beep frequency must be a finite"):
            FilterBuilder(PARAMS, beep_freq=float("nan"))

    @pytest.mark.parametrize("volume", [0.0, 1.5])
    def test_bad_volume(self, volume: float) -> None:
        with pytest.raises(ConfigError, match="volume"):
            FilterBuilder(PARAMS, beep_volume=volume)

    def test_bad_chunk_size(self) -> None:
        with pytest.raises(ConfigError, match="chunk"):
            FilterBuilder(PARAMS, chunk_size=1)

    def test_no_intervals(self) -> None:
        with pytest.raises(ConfigError, match="at least one interval"):
            FilterBuilder(PARAMS).build([])


class TestGraphShape:
    def test_bleep_graph(self) -> None:
        graph = build_filter_graph(PARAMS, intervals((1.0, 2.0)), mode="bleep")
        assert graph.rstrip().endswith("[aout]")
        assert "aevalsrc=exprs=-1" in graph
        assert "sin(2*PI*1000.0*t)" in graph
        assert "amultiply" in graph
        assert "amix=inputs=2:normalize=0:duration=first" in graph
        assert "between(" not in graph
        assert graph.count(";\n") >= 4

    def test_mute_graph_has_no_beep(self) -> None:
        graph = build_filter_graph(PARAMS, intervals((1.0, 2.0)), mode="mute")
        assert "sin(2*PI" not in graph
        assert "amultiply" in graph
        assert graph.count("amix=inputs=2") == 1  # only the gate mix

    def test_reverse_graph(self) -> None:
        graph = build_filter_graph(PARAMS, intervals((1.0, 2.0), (4.0, 4.5)), mode="reverse")
        assert "asplit=3" in graph
        assert graph.count("areverse") == 2
        assert "atrim=start=1:end=2" in graph

    def test_fade_is_applied_at_both_edges(self) -> None:
        graph = build_filter_graph(PARAMS, intervals((1.0, 3.0)), mode="mute", fade_ms=5)
        assert "afade=t=in:st=0:d=0.005" in graph
        assert "afade=t=out:st=1.995:d=0.005" in graph

    def test_short_interval_clamps_fade(self) -> None:
        graph = build_filter_graph(PARAMS, intervals((1.0, 1.002)), mode="mute", fade_ms=50)
        assert "afade=t=in:st=0:d=0.001" in graph

    def test_zero_fade(self) -> None:
        graph = build_filter_graph(PARAMS, intervals((1.0, 2.0)), mode="mute", fade_ms=0)
        assert "afade" not in graph

    def test_delay_uses_milliseconds(self) -> None:
        graph = build_filter_graph(PARAMS, intervals((1.25, 2.0)), mode="mute")
        assert "adelay=delays=1250.000:all=1" in graph

    def test_stereo_pans_generated_tracks(self) -> None:
        params = AudioParams(sample_rate=44100, channels=2, duration=5.0)
        graph = build_filter_graph(params, intervals((1.0, 2.0)), mode="bleep")
        assert "pan=2c|c0=c0|c1=c0" in graph
        assert "sample_rates=44100" in graph

    def test_surround_channels(self) -> None:
        params = AudioParams(sample_rate=48000, channels=6, duration=5.0)
        graph = build_filter_graph(params, intervals((1.0, 2.0)), mode="mute")
        assert "pan=6c|" in graph
        assert "c5=c0" in graph

    def test_dc_track_covers_media_duration(self) -> None:
        graph = build_filter_graph(PARAMS, intervals((9.0, 12.0)), mode="mute")
        assert "aevalsrc=exprs=1:s=48000:d=13" in graph

    def test_custom_beep(self) -> None:
        graph = build_filter_graph(
            PARAMS, intervals((1.0, 2.0)), mode="bleep", beep_freq=800, beep_volume=0.5
        )
        assert "0.5*sin(2*PI*800" in graph

    def test_graph_is_reusable(self) -> None:
        builder = FilterBuilder(PARAMS, mode="mute")
        first = builder.build(intervals((1.0, 2.0)))
        second = builder.build(intervals((3.0, 4.0)))
        assert "adelay=delays=1000.000" in first
        assert "adelay=delays=3000.000" in second
        assert first.count("pulse") >= second.count("pulse")

    def test_unsorted_intervals_are_sorted(self) -> None:
        graph = build_filter_graph(PARAMS, intervals((4.0, 5.0), (1.0, 2.0)), mode="mute")
        assert graph.index("delays=1000.000") < graph.index("delays=4000.000")


class TestChunking:
    def test_many_intervals_are_chunked(self) -> None:
        ranges = [(index * 0.5 + 0.1, index * 0.5 + 0.3) for index in range(40)]
        graph = build_filter_graph(PARAMS, intervals(*ranges), mode="bleep")
        inputs = [int(value) for value in re.findall(r"amix=inputs=(\d+)", graph)]
        assert inputs, "expected amix nodes"
        assert max(inputs) <= 8
        assert inputs.count(8) >= 4
        assert len(graph) > 3000

    def test_single_interval_is_not_mixed_with_itself(self) -> None:
        graph = build_filter_graph(PARAMS, intervals((1.0, 2.0)), mode="mute")
        assert "amix=inputs=1" not in graph
