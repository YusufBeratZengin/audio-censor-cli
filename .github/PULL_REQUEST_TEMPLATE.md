# Pull request

## What does this change?

<!-- One or two sentences. Link the issue if there is one: "Closes #12". -->

## Type

- [ ] Bug fix
- [ ] New feature
- [ ] Documentation
- [ ] Refactor / internal cleanup
- [ ] CI or packaging

## Checklist

- [ ] `bash scripts/check.sh` exits 0 locally (lint, format, MyPy, tests, coverage ≥ 85 %)
- [ ] New behaviour has a test that fails without this change
- [ ] Error paths introduced here are covered by a test
- [ ] No new runtime dependency (or the pull request explains why one is unavoidable)
- [ ] `CHANGELOG.md` has an entry under "Unreleased" for user visible changes
- [ ] Documentation updated (`README.md` and `README.tr.md`) when the interface changed
- [ ] No real personal data, credentials or copyrighted media in the diff
- [ ] FFmpeg is invoked with an argument list, never through a shell

## How was it verified?

<!-- Command lines, sample output, or a short description of the test run. -->
