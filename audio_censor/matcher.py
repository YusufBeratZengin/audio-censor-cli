"""Match bad words against word level transcript timings.

The matcher works on *normalised* token text (see :mod:`audio_censor.text`)
joined with single spaces.  Tokens that are far apart in time (or that belong
to different cues separated by a long gap) are separated by a ``NUL`` sentinel
so a multi word phrase can never match across a pause.

Two matching strategies are supported:

* literal words / phrases (default) - escaped and boundary anchored
* raw regular expressions, opt in with a ``re:`` prefix in the word list

A bounded Levenshtein pass (``fuzzy=N``) catches elongated or slightly
misspelled shout-outs such as ``salllaaak``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError
from .parser import Word
from .text import collapse_repeats, levenshtein, normalize_token

SEPARATOR = "\x00"
DEFAULT_MAX_GAP = 1.5


@dataclass(frozen=True, slots=True)
class Match:
    """One matched word span in the transcript."""

    start: float
    end: float
    text: str
    pattern: str
    cue: int
    first_word: int
    last_word: int
    fuzzy: bool = False


@dataclass(frozen=True, slots=True, order=True)
class Interval:
    """A time range that must be masked."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True, slots=True)
class _Pattern:
    raw: str
    regex: re.Pattern[str]
    fuzzy_ok: bool


#: Suffixes that make an argument look like a word list file rather than an
#: inline comma separated list.
_PATH_SUFFIXES = frozenset({".txt", ".list", ".words", ".csv", ".dict"})


def words_from_file(path: str | Path) -> list[str]:
    """Read one word/phrase per line, whole-line ``#`` comments and blanks ignored.

    Trailing comments are deliberately *not* stripped: a ``#`` may be part of a
    regular expression, so the documentation keeps comments on their own line.
    """
    file_path = Path(path)
    try:
        raw = file_path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise ConfigError(f"bad word file not found: {file_path}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read bad word file {file_path}: {exc}") from exc
    words = []
    for line in raw.splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            words.append(entry)
    return words


def words_from_string(spec: str) -> list[str]:
    """Split a comma or semicolon separated inline word list."""
    return [item.strip() for item in re.split(r"[,;]", spec) if item.strip()]


def _looks_like_path(spec: str) -> bool:
    """Return True when ``spec`` is unambiguously meant as a word list file."""
    if spec.startswith("re:"):
        return False
    if "," in spec or ";" in spec:
        return False
    if "/" in spec or "\\" in spec or spec.startswith(("~", ".")):
        return True
    return Path(spec).suffix.lower() in _PATH_SUFFIXES


def resolve_words(spec: str) -> list[str]:
    """Accept either a path to a word list file or an inline list.

    Anything that is clearly a path (a known list suffix, a path separator or
    a ``~``/``.`` prefix) must exist; silently treating a typo such as
    ``badwords.txt`` as the literal word ``badwords.txt`` would censor nothing
    while still reporting success.  Ambiguous bare words stay inline so
    ``-w shit`` keeps working.
    """
    candidate = Path(spec).expanduser()
    if candidate.exists():
        if not candidate.is_file():
            raise ConfigError(f"bad word file is not a regular file: {candidate}")
        return words_from_file(candidate)
    if _looks_like_path(spec):
        raise ConfigError(
            f"bad word file not found: {candidate}; "
            'use an inline list such as "shit,fuck" if you meant words'
        )
    return words_from_string(spec)


def _literal_regex(word: str) -> str:
    tokens = [re.escape(normalize_token(part)) for part in word.split()]
    tokens = [token for token in tokens if token]
    if not tokens:
        raise ConfigError(f"bad word entry has no matchable characters: {word!r}")
    body = r"\s+".join(tokens)
    return rf"(?<!\w){body}(?!\w)"


class BadWordMatcher:
    """Compile a bad word list and find it inside a transcript."""

    def __init__(
        self,
        words: list[str] | tuple[str, ...],
        *,
        fuzzy: int = 0,
        max_gap: float = DEFAULT_MAX_GAP,
    ) -> None:
        if fuzzy < 0:
            raise ConfigError("fuzzy distance must be zero or positive")
        if max_gap <= 0:
            raise ConfigError("max_gap must be positive")
        self.fuzzy = fuzzy
        self.max_gap = max_gap
        self.patterns = self._compile(list(words))
        if not self.patterns:
            raise ConfigError("no usable bad words given")

    @staticmethod
    def _compile(words: list[str]) -> list[_Pattern]:
        patterns: list[_Pattern] = []
        for word in words:
            entry = word.strip()
            if not entry:
                continue
            if entry.startswith("re:"):
                body = entry[3:].strip()
                if not body:
                    raise ConfigError(f"empty regular expression in word list: {word!r}")
                try:
                    regex = re.compile(body, re.IGNORECASE)
                except re.error as exc:
                    raise ConfigError(f"invalid regular expression {body!r}: {exc}") from exc
                patterns.append(_Pattern(raw=entry, regex=regex, fuzzy_ok=False))
            else:
                source = _literal_regex(entry)
                regex = re.compile(source, re.IGNORECASE)
                single = len(entry.split()) == 1
                patterns.append(_Pattern(raw=entry, regex=regex, fuzzy_ok=single))
        return patterns

    def _joined_text(self, words: tuple[Word, ...]) -> tuple[str, list[int]]:
        chunks: list[str] = []
        char_map: list[int] = []
        previous: Word | None = None
        for index, word in enumerate(words):
            if previous is not None:
                gap = word.start - previous.end
                if gap > self.max_gap:
                    chunks.append(SEPARATOR)
                    char_map.append(-1)
                else:
                    chunks.append(" ")
                    char_map.append(-1)
            chunks.append(word.norm)
            char_map.extend([index] * len(word.norm))
            previous = word
        return "".join(chunks), char_map

    def find(self, words: tuple[Word, ...]) -> list[Match]:
        """Return every matched span, sorted by start time."""
        if not words:
            return []
        joined, char_map = self._joined_text(words)
        found: dict[tuple[int, int], Match] = {}

        for pattern in self.patterns:
            for hit in pattern.regex.finditer(joined):
                if hit.end() <= hit.start():
                    continue
                first = char_map[hit.start()] if hit.start() < len(char_map) else -1
                last = char_map[hit.end() - 1] if hit.end() - 1 < len(char_map) else -1
                if first < 0 or last < 0:
                    continue
                key = (first, last)
                if key in found:
                    continue
                found[key] = Match(
                    start=words[first].start,
                    end=words[last].end,
                    text=" ".join(word.text for word in words[first : last + 1]),
                    pattern=pattern.raw,
                    cue=words[first].cue,
                    first_word=first,
                    last_word=last,
                )

        if self.fuzzy > 0:
            covered = {index for key in found for index in range(key[0], key[1] + 1)}
            for pattern in self.patterns:
                if not pattern.fuzzy_ok:
                    continue
                target = collapse_repeats(normalize_token(pattern.raw))
                if len(target) < 3:
                    continue
                for index, word in enumerate(words):
                    if index in covered or not word.norm:
                        continue
                    candidate = collapse_repeats(word.norm)
                    if abs(len(candidate) - len(target)) > self.fuzzy:
                        continue
                    if levenshtein(candidate, target, self.fuzzy) <= self.fuzzy:
                        found[(index, index)] = Match(
                            start=word.start,
                            end=word.end,
                            text=word.text,
                            pattern=pattern.raw,
                            cue=word.cue,
                            first_word=index,
                            last_word=index,
                            fuzzy=True,
                        )
                        covered.add(index)

        return sorted(found.values(), key=lambda match: (match.start, match.end))


def build_intervals(
    matches: list[Match] | tuple[Match, ...],
    *,
    offset: float = 0.0,
    pad: float = 0.0,
    fade: float = 0.0,
    duration: float | None = None,
    merge_gap: float | None = None,
) -> list[Interval]:
    """Turn matches into merged, padded and clamped intervals.

    Intervals closer than the fade length are merged, otherwise the two fade
    ramps would overlap and the gain envelope would never reach zero.
    """
    if pad < 0:
        raise ConfigError("pad must be zero or positive")
    if fade < 0:
        raise ConfigError("fade must be zero or positive")

    raw: list[Interval] = []
    for match in matches:
        start = max(0.0, match.start + offset - pad)
        end = match.end + offset + pad
        if duration is not None:
            end = min(end, duration)
        if end <= start:
            continue
        raw.append(Interval(start=round(start, 6), end=round(end, 6)))

    if not raw:
        return []

    gap = merge_gap if merge_gap is not None else 2.0 * fade
    raw.sort()
    merged: list[Interval] = [raw[0]]
    for interval in raw[1:]:
        current = merged[-1]
        if interval.start <= current.end + gap:
            merged[-1] = Interval(start=current.start, end=max(current.end, interval.end))
        else:
            merged.append(interval)
    return merged
