# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- All output paths (`--output`, `--audit`, `--clean-subtitles`,
  `--keep-filter-script`) are validated against the input, the transcript and
  each other before anything is written, so a collision can no longer overwrite
  the source media or subtitles.
- `--offset-ms` keeps the same word objects in `Transcript.cues` and
  `Transcript.words`, so `--clean-subtitles` masks the shifted words instead of
  writing the transcript unchanged.
- `--dry-run` no longer writes the audit CSV or the censored subtitles, and
  `--dry-run --json` prints a single JSON document (the graph is included as
  `filter_graph`) instead of a command listing followed by JSON.
- A word list argument that looks like a file (`.txt`, a path separator,
  `~`/`.`) must exist; a typo is reported instead of being used as a literal
  word and silently matching nothing.
- The README no longer claims millisecond-accurate SRT/VTT timing or unchanged
  audio outside the ranges; it states that word timings are approximate and
  that the audio stream is re-encoded.
- The documented word list example keeps `#` comments on their own line, so a
  comment can no longer become part of a regular expression.
- Prober tests mock `subprocess.run` instead of executing `#!/bin/sh` scripts,
  so they also run on Windows; the FFmpeg availability guard now performs a
  real check.

## [0.1.0] - 2026-11-03

First public release.

### Fixed

- The test suite no longer fails with `ModuleNotFoundError: No module named
  'audio_censor'` when pytest is started in a way that does not put the checkout
  on `sys.path` (a standalone pytest script bound to another interpreter, or
  `pytest` run from another directory): `pyproject.toml` now sets
  `pythonpath = ["."]` and `scripts/check.sh` exports the project root for the
  pytest step, so no editable install is required to run the tests.
- `scripts/check.sh` no longer stops with "ruff is not installed" on a fresh
  checkout: it resolves Ruff/MyPy/pytest through the active interpreter, `PATH`,
  `~/.local/bin` and a local `.venv`, installs a missing dev tool once
  (`CHECK_NO_INSTALL=1` opts out), and probes the resolved pytest for the
  coverage plugin instead of assuming the same interpreter.

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
