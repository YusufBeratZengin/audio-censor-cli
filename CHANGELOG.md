# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-03

First public release.

### Added

- Three masking modes: `bleep` (1 kHz sine), `mute` (silence) and `reverse`
  (the word played backwards).
- Transcript parsing for SubRip (`.srt`), WebVTT (`.vtt`) and Whisper JSON
  (`segments[].words[]`, flat `words`, `whisper.cpp` offsets).
- Case-insensitive whole-word matching with Turkish `I/İ/ı` folding, `re:`
  regular-expression entries and optional Levenshtein matching (`--fuzzy N`).
- Sample-accurate 5 ms micro fades on every masking edge, so bleeps never click.
- Lossless video handling: `-c:v copy` for every container the source already
  fits, verified in tests by comparing stream MD5s.
- Filter graph is written to a file and passed with `-filter_complex_script`
  (or `-/filter_complex` on FFmpeg ≥ 7.1), so hundreds of matches never hit
  command-line or expression length limits. Wide graphs are reduced in chunks.
- `--pad-ms` to grow ranges, `--offset-ms` to fix drifting transcripts,
  `--max-gap-ms` for multi-word phrases.
- CSV audit log of every masked word (`--audit`).
- Censored subtitle export (`--clean-subtitles`, default mask `[BLEEP]`).
- `--dry-run`, `--json`, `--quiet`, `--keep-filter-script`, `--audio-codec`
  and `--audio-bitrate`.
- Fast path: when nothing matches, streams are remuxed with `-c copy`.
- 225 tests (94 % branch coverage) including DFT verification of the beep,
  click detection and video-stream identity.
