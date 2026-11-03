"""Unit tests for bad word matching and interval building."""

from __future__ import annotations

from pathlib import Path

import pytest

from audio_censor.errors import ConfigError
from audio_censor.matcher import (
    BadWordMatcher,
    Interval,
    Match,
    build_intervals,
    resolve_words,
    words_from_file,
    words_from_string,
)
from audio_censor.parser import Word
from audio_censor.text import normalize_token


def make_words(*pairs: tuple[float, float, str], cue: int = 1) -> tuple[Word, ...]:
    return tuple(
        Word(start=start, end=end, text=text, norm=normalize_token(text), cue=cue)
        for start, end, text in pairs
    )


class TestWordSources:
    def test_file_with_comments(self, tmp_path: Path) -> None:
        target = tmp_path / "words.txt"
        target.write_text("# comment\n\nshit\n  fuck  \n", encoding="utf-8")
        assert words_from_file(target) == ["shit", "fuck"]

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not found"):
            words_from_file(tmp_path / "nope.txt")

    def test_inline_list(self) -> None:
        assert words_from_string(" shit, fuck ;; ass ") == ["shit", "fuck", "ass"]

    def test_resolve_file(self, tmp_path: Path) -> None:
        target = tmp_path / "words.txt"
        target.write_text("shit\n", encoding="utf-8")
        assert resolve_words(str(target)) == ["shit"]

    def test_resolve_inline(self) -> None:
        assert resolve_words("shit,fuck") == ["shit", "fuck"]

    def test_bare_word_stays_inline(self) -> None:
        assert resolve_words("shit") == ["shit"]

    def test_missing_path_like_entry_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="bad word file not found"):
            resolve_words(str(tmp_path / "badwords.txt"))

    def test_directory_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not a regular file"):
            resolve_words(str(tmp_path))

    def test_regex_is_not_mistaken_for_a_path(self) -> None:
        assert resolve_words(r"re:fuck\w*") == [r"re:fuck\w*"]

    def test_documented_example_list_is_usable(self, tmp_path: Path) -> None:
        # Mirrors the README example: comments live on their own line, so a
        # trailing ``#`` can never become part of a regular expression.
        target = tmp_path / "badwords.txt"
        target.write_text(
            "# obvious demo words only\n"
            "shit\n"
            "son of a bitch\n"
            "# regular expression, opt in with the re: prefix\n"
            "re:fuck\\w*\n"
            "# stretched spellings\n"
            "re:s+h+i+t+\n",
            encoding="utf-8",
        )
        matcher = BadWordMatcher(resolve_words(str(target)))
        words = make_words((0.0, 0.3, "fucking"), (0.4, 0.7, "shiiit"))
        assert [match.text for match in matcher.find(words)] == ["fucking", "shiiit"]


class TestMatching:
    def test_matches_whole_word_only(self) -> None:
        words = make_words((0.0, 0.3, "fuck"), (0.4, 0.8, "fucking"), (0.9, 1.2, "FUCK,"))
        matches = BadWordMatcher(["fuck"]).find(words)
        assert [match.text for match in matches] == ["fuck", "FUCK,"]

    def test_turkish_case_folding(self) -> None:
        words = make_words((0.0, 0.4, "SİKTİR"), (0.5, 0.9, "sıktır"))
        matcher = BadWordMatcher(["siktir"])
        assert len(matcher.find(words)) == 2

    def test_multi_word_phrase(self) -> None:
        words = make_words(
            (0.0, 0.3, "son"), (0.35, 0.6, "of"), (0.65, 1.0, "a"), (1.05, 1.3, "bitch")
        )
        matches = BadWordMatcher(["son of a bitch"]).find(words)
        assert len(matches) == 1
        assert matches[0].start == pytest.approx(0.0)
        assert matches[0].end == pytest.approx(1.3)
        assert matches[0].text == "son of a bitch"

    def test_phrase_does_not_cross_long_pause(self) -> None:
        words = make_words((0.0, 0.3, "son"), (9.0, 9.3, "of"))
        assert BadWordMatcher(["son of"]).find(words) == []

    def test_regex_pattern(self) -> None:
        words = make_words((0.0, 0.3, "sh1t"), (0.4, 0.6, "shit"))
        matches = BadWordMatcher([r"re:sh[i1]t"]).find(words)
        assert len(matches) == 2

    def test_invalid_regex(self) -> None:
        with pytest.raises(ConfigError, match="invalid regular expression"):
            BadWordMatcher(["re:("])

    def test_empty_regex(self) -> None:
        with pytest.raises(ConfigError, match="empty regular expression"):
            BadWordMatcher(["re:   "])

    def test_no_words(self) -> None:
        with pytest.raises(ConfigError, match="no usable"):
            BadWordMatcher([])

    def test_word_without_characters(self) -> None:
        with pytest.raises(ConfigError, match="no matchable"):
            BadWordMatcher(["..."])

    def test_invalid_options(self) -> None:
        with pytest.raises(ConfigError, match="fuzzy"):
            BadWordMatcher(["fuck"], fuzzy=-1)
        with pytest.raises(ConfigError, match="max_gap"):
            BadWordMatcher(["fuck"], max_gap=0)

    def test_empty_transcript(self) -> None:
        assert BadWordMatcher(["fuck"]).find(()) == []

    def test_deduplicates_overlapping_patterns(self) -> None:
        words = make_words((0.0, 0.3, "fuck"))
        matches = BadWordMatcher(["fuck", r"re:f.ck"]).find(words)
        assert len(matches) == 1
        assert matches[0].pattern == "fuck"

    def test_results_are_sorted(self) -> None:
        words = make_words((2.0, 2.3, "shit"), (0.0, 0.3, "fuck"))
        ordered = sorted(words, key=lambda word: word.start)
        matches = BadWordMatcher(["fuck", "shit"]).find(ordered)
        assert [match.start for match in matches] == [0.0, 2.0]

    def test_punctuation_inside_word_list(self) -> None:
        words = make_words((0.0, 0.3, "fuck!"))
        assert len(BadWordMatcher(["fuck!"]).find(words)) == 1

    def test_cue_is_reported(self) -> None:
        words = (Word(start=0.0, end=0.3, text="fuck", norm="fuck", cue=7),)
        assert BadWordMatcher(["fuck"]).find(words)[0].cue == 7

    def test_match_fields(self) -> None:
        words = make_words((1.0, 1.3, "shit"))
        match = BadWordMatcher(["shit"]).find(words)[0]
        assert (match.first_word, match.last_word) == (0, 0)
        assert match.fuzzy is False


class TestFuzzy:
    def test_elongated_word(self) -> None:
        words = make_words((0.0, 0.4, "salllaaak"))
        match = BadWordMatcher(["sallak"], fuzzy=1).find(words)
        assert len(match) == 1
        assert match[0].fuzzy is True

    def test_disabled_by_default(self) -> None:
        words = make_words((0.0, 0.4, "salllaaak"))
        assert BadWordMatcher(["sallak"]).find(words) == []

    def test_too_far_away(self) -> None:
        words = make_words((0.0, 0.4, "banana"))
        assert BadWordMatcher(["sallak"], fuzzy=1).find(words) == []

    def test_already_matched_words_are_not_repeated(self) -> None:
        words = make_words((0.0, 0.3, "shit"))
        matches = BadWordMatcher(["shit"], fuzzy=1).find(words)
        assert len(matches) == 1
        assert matches[0].fuzzy is False

    def test_short_patterns_are_ignored(self) -> None:
        words = make_words((0.0, 0.3, "abc"))
        assert BadWordMatcher(["ab"], fuzzy=2).find(words) == []


class TestIntervals:
    @staticmethod
    def match(start: float, end: float, text: str = "shit") -> Match:
        return Match(
            start=start,
            end=end,
            text=text,
            pattern=text,
            cue=1,
            first_word=0,
            last_word=0,
        )

    def test_pad_and_offset(self) -> None:
        intervals = build_intervals([self.match(2.0, 3.0)], pad=0.05, offset=0.5)
        assert intervals[0].start == pytest.approx(2.45)
        assert intervals[0].end == pytest.approx(3.55)

    def test_clamped_to_zero(self) -> None:
        intervals = build_intervals([self.match(0.02, 1.0)], pad=0.2)
        assert intervals[0].start == pytest.approx(0.0)

    def test_clamped_to_duration(self) -> None:
        intervals = build_intervals([self.match(4.8, 5.0)], pad=0.5, duration=5.0)
        assert intervals[0].end == pytest.approx(5.0)

    def test_interval_outside_media_is_dropped(self) -> None:
        assert build_intervals([self.match(10.0, 11.0)], duration=5.0) == []

    def test_merges_close_intervals(self) -> None:
        matches = [self.match(1.0, 1.2), self.match(1.21, 1.5)]
        intervals = build_intervals(matches, fade=0.005)
        assert len(intervals) == 1
        assert intervals[0].end == pytest.approx(1.5)

    def test_keeps_distant_intervals(self) -> None:
        matches = [self.match(1.0, 1.2), self.match(2.0, 2.2)]
        assert len(build_intervals(matches, fade=0.005)) == 2

    def test_zero_length_dropped(self) -> None:
        assert build_intervals([self.match(1.0, 1.0)]) == []

    def test_negative_pad_rejected(self) -> None:
        with pytest.raises(ConfigError, match="pad"):
            build_intervals([self.match(1.0, 2.0)], pad=-0.1)

    def test_negative_fade_rejected(self) -> None:
        with pytest.raises(ConfigError, match="fade"):
            build_intervals([self.match(1.0, 2.0)], fade=-0.1)

    def test_empty(self) -> None:
        assert build_intervals([]) == []

    def test_explicit_merge_gap(self) -> None:
        matches = [self.match(1.0, 1.2), self.match(1.3, 1.5)]
        assert len(build_intervals(matches, merge_gap=0.0)) == 2

    def test_interval_duration(self) -> None:
        assert Interval(start=1.0, end=2.5).duration == pytest.approx(1.5)
        assert Interval(start=2.0, end=1.0).duration == 0.0
