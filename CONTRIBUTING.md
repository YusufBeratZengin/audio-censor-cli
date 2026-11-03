# Contributing to audio-censor-cli

Thanks for taking the time to help. This project is deliberately small: pure
standard-library Python plus the FFmpeg binary, so almost every change is a few
dozen lines and easy to review.

## Quick start

```bash
git clone https://github.com/YusufBeratZengin/audio-censor-cli.git
cd audio-censor-cli
python3 -m pip install -e ".[dev]"
bash scripts/check.sh
```

`scripts/check.sh` is the same gate CI runs: Ruff (lint + format), MyPy in
strict mode, pytest with a coverage floor of 85 %. A pull request is ready when
that script exits 0. It locates the tools through the active interpreter,
`PATH`, `~/.local/bin` and a local `.venv`, installing whichever dev tool is
still missing, so it is safe to run in a fresh checkout; export
`CHECK_NO_INSTALL=1` to disable that and get a fail-fast error instead. Tests
import the package straight from the checkout (`pythonpath = ["."]`), so they
work without `pip install -e .`, even when pytest runs from another interpreter.

FFmpeg and FFprobe must be installed for the integration tests. Without them the
suite still runs and skips the render tests, but coverage will drop below the
gate — install FFmpeg before running the script.

## What to work on

Look for issues labelled `good first issue`. They are scoped to roughly one
sitting and include the expected behaviour and the files involved. If you want
to add a feature that is not listed, open an issue first so we can agree on the
interface before you write code.

## Coding rules

* Python 3.10+ syntax only, no runtime dependencies. If a change needs a new
  library, explain why in the pull request — the answer is usually "we can do it
  with the standard library".
* Type annotations everywhere; MyPy runs in strict mode.
* Formatting is `ruff format`; imports and lint rules come from `pyproject.toml`.
* Keep functions testable: parsing, matching and graph building must stay free
  of `subprocess` calls so they can be unit tested without FFmpeg.
* Never build a shell string. Pass argument lists to `subprocess.run`.
* New behaviour needs a test that fails before the change and passes after. New
  error paths need a test too — the CLI's exit codes are part of the contract.

## Tests

```bash
python3 -m pytest tests/ -v                        # everything
python3 -m pytest tests/test_parser.py -v          # one module
python3 -m pytest -k reverse -v                    # one topic
```

Integration tests generate their own media with FFmpeg in a temporary
directory; nothing touches the network and no fixture files are committed.

## Commit messages

Short imperative subject lines, English, no trailing period:

```
Add 800 Hz TV bleep preset
Fix fade clamp for intervals shorter than 10 ms
```

Group unrelated changes into separate commits. Rebase on `main` before opening
the pull request.

## Reporting bugs

Use the bug report template. The most useful report contains:

* `audio-censor --version`, `ffmpeg -version | head -1`, OS and Python version,
* the command line (without private paths),
* the transcript snippet around the problem,
* what happened and what you expected.

Please do not attach copyrighted audio or personal recordings. A synthetic
FFmpeg command that reproduces the problem is usually enough.

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
