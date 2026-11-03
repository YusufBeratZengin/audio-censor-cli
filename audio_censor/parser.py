"""Parse SRT, WebVTT and Whisper style JSON transcripts into word tokens.

SRT and WebVTT only carry cue level timings.  To mask a single swear word the
tool needs word level spans, so cue text is split into tokens and the cue
duration is distributed proportionally to token length.  Whisper JSON usually
carries real word timings and those are used verbatim when present.

The public entry point is :func:`load_transcript`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .errors import TranscriptError
from .text import normalize_token

_TIMESTAMP_SHAPE = re.compile(r"^(?:\d+:){0,2}\d{1,3}(?:[.,]\d{1,3})?$")
_TIMING_LINE = re.compile(r"^\s*(\S+)\s*-->\s*(\S+)")
_TAG = re.compile(r"<[^>]*>|\{[^}]*\}")
_SUPPORTED_SUFFIXES = {".srt": "srt", ".vtt": "vtt", ".json": "whisper"}


@dataclass(frozen=True, slots=True)
class Word:
    """A single spoken token with its time span."""

    start: float
    end: float
    text: str
    norm: str
    cue: int

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True, slots=True)
class Cue:
    """One subtitle cue / transcript segment."""

    index: int
    start: float
    end: float
    text: str
    words: tuple[Word, ...]


@dataclass(frozen=True, slots=True)
class Transcript:
    """A parsed transcript."""

    cues: tuple[Cue, ...]
    words: tuple[Word, ...]
    source: str
    format: str

    @property
    def duration(self) -> float:
        return max((cue.end for cue in self.cues), default=0.0)


def parse_timestamp(value: str) -> float:
    """Parse ``HH:MM:SS,mmm`` / ``MM:SS.mmm`` / ``SS.mmm`` into seconds.

    The number of colon separated fields decides the unit of each field, so
    ``02:03.250`` is two minutes and change, while ``1:02:03`` is an hour.
    """
    text = value.strip()
    if not _TIMESTAMP_SHAPE.match(text):
        raise TranscriptError(f"invalid timestamp: {value!r}")
    fields = text.replace(",", ".").split(":")
    seconds_field = fields[-1]
    whole, _, fraction = seconds_field.partition(".")
    frac = float("0." + fraction) if fraction else 0.0
    if len(fields) == 3:
        hours, minutes = int(fields[0]), int(fields[1])
    elif len(fields) == 2:
        hours, minutes = 0, int(fields[0])
    else:
        hours, minutes = 0, 0
    total = hours * 3600 + minutes * 60 + int(whole) + frac
    return round(total, 6)


def _split_tokens(text: str) -> list[str]:
    return [token for token in re.split(r"\s+", text.strip()) if token]


def _distribute(text: str, start: float, end: float, cue_index: int) -> tuple[Word, ...]:
    """Spread ``text`` over ``[start, end]`` proportionally to token length."""
    tokens = _split_tokens(text)
    if not tokens:
        return ()
    span = max(0.0, end - start)
    weights = [len(token) + 1 for token in tokens]
    total_weight = float(sum(weights))
    words: list[Word] = []
    cursor = start
    last = len(tokens) - 1
    for position, (token, weight) in enumerate(zip(tokens, weights, strict=True)):
        share = span * weight / total_weight
        token_start = cursor
        token_end = end if position == last else cursor + share
        words.append(
            Word(
                start=round(token_start, 6),
                end=round(max(token_start, token_end), 6),
                text=token,
                norm=normalize_token(token),
                cue=cue_index,
            )
        )
        cursor = token_end
    return tuple(words)


def _make_cue(index: int, start: float, end: float, text: str) -> Cue:
    if end < start:
        raise TranscriptError(f"cue {index}: end {end} is before start {start}")
    clean = " ".join(_TAG.sub("", text).split())
    return Cue(
        index=index, start=start, end=end, text=clean, words=_distribute(clean, start, end, index)
    )


def _blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _timing_bounds(line: str, kind: str) -> tuple[float, float]:
    match = _TIMING_LINE.match(line)
    if not match:
        raise TranscriptError(f"invalid {kind} timing line: {line!r}")
    try:
        return parse_timestamp(match.group(1)), parse_timestamp(match.group(2))
    except TranscriptError as exc:
        raise TranscriptError(f"invalid {kind} timing line: {line!r}") from exc


def parse_srt(text: str) -> tuple[Cue, ...]:
    """Parse SubRip text into cues."""
    cues: list[Cue] = []
    for block in _blocks(text):
        timing_index = next((i for i, line in enumerate(block) if "-->" in line), None)
        if timing_index is None:
            raise TranscriptError(f"SRT block without a timing line: {block[0]!r}")
        start, end = _timing_bounds(block[timing_index], "SRT")
        body = "\n".join(block[timing_index + 1 :])
        cues.append(_make_cue(len(cues) + 1, start, end, body))
    if not cues:
        raise TranscriptError("no cues found in SRT input")
    return tuple(cues)


def parse_vtt(text: str) -> tuple[Cue, ...]:
    """Parse WebVTT text into cues."""
    normalised = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if not normalised.lstrip().startswith("WEBVTT"):
        raise TranscriptError("missing WEBVTT header")
    cues: list[Cue] = []
    for block in _blocks(normalised):
        head = block[0].strip()
        if head.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        timing_index = next((i for i, line in enumerate(block) if "-->" in line), None)
        if timing_index is None:
            continue
        start, end = _timing_bounds(block[timing_index], "VTT")
        body = "\n".join(block[timing_index + 1 :])
        cues.append(_make_cue(len(cues) + 1, start, end, body))
    if not cues:
        raise TranscriptError("no cues found in VTT input")
    return tuple(cues)


def _number(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            if ":" in text:
                return parse_timestamp(text)
            return float(text)
        except (ValueError, TranscriptError):
            return default
    return default


def _segment_bounds(segment: dict[str, Any]) -> tuple[float | None, float | None]:
    start = _number(segment.get("start"))
    end = _number(segment.get("end"))
    timestamps = segment.get("timestamps")
    if isinstance(timestamps, dict):
        start = start if start is not None else _number(timestamps.get("from"))
        end = end if end is not None else _number(timestamps.get("to"))
    offsets = segment.get("offsets")
    if isinstance(offsets, dict):
        raw_from = _number(offsets.get("from"))
        raw_to = _number(offsets.get("to"))
        start = (
            start if start is not None else (raw_from / 1000.0 if raw_from is not None else None)
        )
        end = end if end is not None else (raw_to / 1000.0 if raw_to is not None else None)
    return start, end


def _segment_words(segment: dict[str, Any], index: int) -> tuple[Word, ...] | None:
    raw_words = segment.get("words")
    if not isinstance(raw_words, list) or not raw_words:
        return None
    words: list[Word] = []
    for item in raw_words:
        if not isinstance(item, dict):
            return None
        raw_text = item.get("word", item.get("text"))
        start = _number(item.get("start"))
        end = _number(item.get("end"))
        if not isinstance(raw_text, str) or start is None or end is None:
            return None
        text = raw_text.strip()
        if not text:
            continue
        words.append(
            Word(
                start=start,
                end=max(start, end),
                text=text,
                norm=normalize_token(text),
                cue=index,
            )
        )
    return tuple(words) or None


def _whisper_segments(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        is_word_list = all(isinstance(item, dict) and "word" in item for item in data)
        if is_word_list:
            joined = " ".join(str(item.get("word", "")) for item in data)
            return [{"words": data, "text": joined}]
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("segments", "transcription", "chunks"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if isinstance(data.get("words"), list):
            return [{"words": data["words"], "text": str(data.get("text", ""))}]
    raise TranscriptError("unsupported Whisper JSON layout")


def parse_whisper_json(text: str) -> tuple[Cue, ...]:
    """Parse Whisper / whisper.cpp / faster-whisper JSON into cues."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TranscriptError(f"invalid JSON transcript: {exc}") from exc

    cues: list[Cue] = []
    for index, segment in enumerate(_whisper_segments(data), start=1):
        words = _segment_words(segment, index)
        raw_text = segment.get("text")
        body = raw_text.strip() if isinstance(raw_text, str) else ""
        start, end = _segment_bounds(segment)
        if words is not None:
            if start is None:
                start = words[0].start
            if end is None:
                end = max(word.end for word in words)
            if not body:
                body = " ".join(word.text for word in words)
            cues.append(Cue(index=index, start=start, end=max(start, end), text=body, words=words))
            continue
        if start is None or end is None:
            raise TranscriptError(f"segment {index} has no usable timestamps")
        cues.append(_make_cue(index, start, max(start, end), body))
    if not cues:
        raise TranscriptError("no segments found in JSON input")
    return tuple(cues)


def parse_transcript_text(text: str, *, format: str) -> tuple[Cue, ...]:
    """Parse ``text`` using an explicit format name (``srt``/``vtt``/``whisper``)."""
    if format == "srt":
        return parse_srt(text)
    if format == "vtt":
        return parse_vtt(text)
    if format == "whisper":
        return parse_whisper_json(text)
    raise TranscriptError(f"unknown transcript format: {format!r}")


def detect_format(path: Path, text: str) -> str:
    """Guess the transcript format from the suffix, falling back to content."""
    suffix = path.suffix.lower()
    if suffix in _SUPPORTED_SUFFIXES:
        return _SUPPORTED_SUFFIXES[suffix]
    stripped = text.lstrip("\ufeff \t\r\n")
    if stripped.startswith("WEBVTT"):
        return "vtt"
    if stripped.startswith(("{", "[")):
        return "whisper"
    if "-->" in stripped:
        return "srt"
    raise TranscriptError(
        f"cannot detect transcript format for {path.name!r}; use a .srt, .vtt or .json file"
    )


def load_transcript(path: str | Path, *, format: str | None = None) -> Transcript:
    """Read and parse a transcript file."""
    transcript_path = Path(path)
    try:
        raw = transcript_path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise TranscriptError(f"transcript not found: {transcript_path}") from exc
    except OSError as exc:
        raise TranscriptError(f"cannot read transcript {transcript_path}: {exc}") from exc
    fmt = format or detect_format(transcript_path, raw)
    cues = parse_transcript_text(raw, format=fmt)
    words = tuple(word for cue in cues for word in cue.words)
    ordered = tuple(sorted(words, key=lambda word: (word.start, word.end)))
    return Transcript(cues=cues, words=ordered, source=str(transcript_path), format=fmt)


def shift_transcript(transcript: Transcript, offset: float) -> Transcript:
    """Return a copy of ``transcript`` moved by ``offset`` seconds.

    The shifted cue words are the very same objects that make up the returned
    ``transcript.words``.  :func:`audio_censor.subtitles.render_clean_srt`
    maps masked word indices by object identity, so shifting the two views
    independently would silently leave every subtitle word unmasked.
    """
    if offset == 0:
        return transcript

    def shifted(word: Word) -> Word:
        return replace(word, start=word.start + offset, end=word.end + offset)

    cues = tuple(
        replace(
            cue,
            start=cue.start + offset,
            end=cue.end + offset,
            words=tuple(shifted(word) for word in cue.words),
        )
        for cue in transcript.cues
    )
    words = tuple(
        sorted(
            (word for cue in cues for word in cue.words),
            key=lambda word: (word.start, word.end),
        )
    )
    return replace(transcript, cues=cues, words=words)
