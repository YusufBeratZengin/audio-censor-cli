"""Export a censored copy of the transcript (``clean.srt``)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from .matcher import Match
from .parser import Transcript
from .text import format_timecode

DEFAULT_MASK = "[BLEEP]"


def masked_indices(transcript: Transcript, matches: Iterable[Match]) -> set[int]:
    """Return the indices (into ``transcript.words``) covered by any match."""
    covered: set[int] = set()
    for match in matches:
        covered.update(range(match.first_word, match.last_word + 1))
    return covered


def render_clean_srt(
    transcript: Transcript,
    matches: Sequence[Match] | set[int],
    *,
    mask: str = DEFAULT_MASK,
) -> str:
    """Render an SRT where every matched span is replaced by ``mask``.

    ``matches`` may be either the match objects or the precomputed set of
    masked word indices.  Consecutive masked words collapse into a single
    mask token, so a phrase like ``son of a bitch`` becomes one ``[BLEEP]``
    instead of four.
    """
    covered = set(matches) if isinstance(matches, set) else masked_indices(transcript, matches)

    identity = {id(word): index for index, word in enumerate(transcript.words)}
    blocks: list[str] = []
    for cue in transcript.cues:
        if cue.words:
            tokens: list[str] = []
            previous_masked = False
            for word in cue.words:
                masked = identity.get(id(word), -1) in covered
                if masked:
                    if not previous_masked:
                        tokens.append(mask)
                else:
                    tokens.append(word.text)
                previous_masked = masked
            body = " ".join(tokens)
        else:
            body = cue.text
        blocks.append(
            f"{cue.index}\n"
            f"{format_timecode(cue.start, millis=False)} --> "
            f"{format_timecode(cue.end, millis=False)}\n"
            f"{body}\n"
        )
    return "\n".join(blocks)


def write_clean_srt(
    path: str | Path,
    transcript: Transcript,
    matches: Sequence[Match] | set[int],
    *,
    mask: str = DEFAULT_MASK,
) -> Path:
    """Write the censored subtitle file and return its path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_clean_srt(transcript, matches, mask=mask), encoding="utf-8")
    return target
