# Security policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | yes       |

## Reporting a vulnerability

Please report security issues privately through GitHub:
**Security → Advisories → Report a vulnerability** on
<https://github.com/YusufBeratZengin/audio-censor-cli/security/advisories/new>.

Do not open a public issue for a vulnerability. You can expect an initial
response within seven days and a fix or mitigation plan within thirty days for
issues that affect the released package.

## Scope

`audio-censor-cli` is a local command line tool. It has no network code, no
telemetry, no credentials and no runtime Python dependencies. The attack surface
is therefore narrow, but the following are still in scope:

* command injection or argument confusion when a crafted file path is passed to
  FFmpeg/FFprobe (the tool never uses a shell, so this should be impossible —
  a proof of concept would still be welcome);
* denial of service through a hostile transcript or media file (for example
  unbounded memory use while building a filter graph);
* unexpected file writes outside the requested `--output`, `--audit`,
  `--clean-subtitles` and `--keep-filter-script` paths;
* supply-chain issues in the development dependencies (`pytest`, `ruff`,
  `mypy`) — these are not installed for end users.

Out of scope: the behaviour of FFmpeg itself (report upstream), and any claim
that masking profanity guarantees platform approval — the tool makes no such
claim and the README says so explicitly.

## Handling of user data

The tool reads the files you point it at and writes only the paths you ask for.
Temporary filter graphs are written into the OS temporary directory and removed
after the FFmpeg run; `--keep-filter-script` keeps one on purpose. No audio,
transcript or metadata ever leaves the machine.
