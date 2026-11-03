"""Small text helpers: Turkish aware normalisation and edit distance.

Everything here is dependency free on purpose: the tool must run with the
standard library only.
"""

from __future__ import annotations

import re
import unicodedata

#: Characters that must be folded before case folding so that ``İ``/``I``/``ı``
#: all compare equal. Python's ``str.lower`` maps ``İ`` to ``i̇`` (two code
#: points) which never matches a plain ``i``, hence the explicit table.  The
#: remaining Turkish letters are folded to their ASCII base so that a
#: transcript written with or without diacritics matches the same word list.
_TURKISH_FOLD = str.maketrans(
    {
        "İ": "i",
        "I": "i",
        "ı": "i",
        "Ğ": "g",
        "ğ": "g",
        "Ş": "s",
        "ş": "s",
        "Ç": "c",
        "ç": "c",
        "Ö": "o",
        "ö": "o",
        "Ü": "u",
        "ü": "u",
        "Â": "a",
        "â": "a",
        "Î": "i",
        "î": "i",
        "Û": "u",
        "û": "u",
    }
)

_PUNCT_EDGES = re.compile(r"^[^\w]+|[^\w]+$", re.UNICODE)
_REPEAT = re.compile(r"(.)\1{2,}", re.UNICODE)


def fold(text: str) -> str:
    """Case fold ``text`` and normalise Turkish dotted/dotless i variants."""
    return unicodedata.normalize("NFC", text).translate(_TURKISH_FOLD).casefold()


def normalize_token(raw: str) -> str:
    """Return the comparison form of a single transcript token.

    Leading and trailing punctuation is removed, inner apostrophes are kept
    (``fuckin'`` stays one word) and Turkish ``I``/``İ``/``ı`` are folded.
    """
    stripped = _PUNCT_EDGES.sub("", raw.strip())
    return fold(stripped)


def collapse_repeats(text: str) -> str:
    """Collapse runs of three or more identical characters (``salllaaak``)."""
    return _REPEAT.sub(r"\1", text)


def levenshtein(a: str, b: str, max_distance: int) -> int:
    """Bounded Levenshtein distance.

    Returns ``max_distance + 1`` as soon as the real distance is known to be
    larger than ``max_distance``, which keeps fuzzy matching cheap.
    """
    if max_distance < 0:
        return 0
    if a == b:
        return 0
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1
    if not a:
        return len(b)
    if not b:
        return len(a)

    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i] + [0] * len(b)
        row_best = i
        for j, char_b in enumerate(b, start=1):
            cost = 0 if char_a == char_b else 1
            value = min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + cost,
            )
            current[j] = value
            if value < row_best:
                row_best = value
        if row_best > max_distance:
            return max_distance + 1
        previous = current
    return previous[-1]


def format_timecode(seconds: float, *, millis: bool = True) -> str:
    """Format ``seconds`` as ``HH:MM:SS.mmm`` (or SRT's comma variant)."""
    if seconds < 0:
        seconds = 0.0
    total_ms = round(seconds * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    separator = "." if millis else ","
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{ms:03d}"
