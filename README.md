# audio-censor-cli

**Bleep, mute or reverse swear words in audio and video using the transcript you already have — no speech-to-text model, no re-encoding of the video stream.**

Point it at a podcast, a YouTube export or a call recording plus an SRT/VTT/Whisper-JSON transcript, and it replaces every dirty word with a 1 kHz beep, silence, or the word played backwards. The video stream is copied bit-for-bit (`-c:v copy`), so only the audio is decoded and encoded again: a measured run rendered 20 minutes of 48 kHz stereo audio in **37.5 s** (WAV in, M4A out, 120 masked words, Windows 11 laptop, Python 3.11, FFmpeg 9.0.1), and a 60 s 1080p clip in 4.1 s with a bit-identical video stream. `python3 scripts/benchmark.py` rebuilds that measurement on your machine.

```console
$ audio-censor podcast.mp4 -s podcast.vtt -w badwords.txt --mode bleep -o clean.mp4
info: transcript: podcast.vtt (vtt, 4 cues, 24 words)
info: media: 20.000s, 48000 Hz, 1 ch, video copied
info: 2 match(es) -> 2 masked range(s) in bleep mode
  #1   00:00:09.667 - 00:00:10.083 (0.417s)  'shit'  cue 2
  #2   00:00:15.355 - 00:00:16.258 (0.903s)  'fucking'  cue 3
ok: beeps mixed in: clean.mp4 (0.90s)
```

## Why another bleeper?

| | audio-censor-cli | Vosk/Whisper based tools |
|---|---|---|
| Input | your existing SRT / VTT / Whisper JSON | its own embedding of a speech model |
| Runtime deps | Python standard library only | model weights, native libs, GPU optional |
| Video | `-c:v copy`, untouched bitstream | usually re-encodes |
| Install size | a few kB | hundreds of MB |

If you already ran Whisper (or your editor exported subtitles), you do not need a second speech recogniser — you need accurate time ranges, click-free edges and a fast mux.

## Install

```bash
pip install audio-censor-cli      # Python 3.10+
```

FFmpeg and FFprobe must be on `PATH`:

| Platform | Command |
|---|---|
| Debian/Ubuntu | `sudo apt install ffmpeg` |
| macOS | `brew install ffmpeg` |
| Windows | `winget install Gyan.FFmpeg` (or `choco install ffmpeg`) |

No Python runtime dependencies. `re`, `csv`, `wave` and `subprocess` cover
everything; the FFmpeg binary does the audio work.

## 30-second example

```bash
# 1. a word list — one entry per line, '#' for comments
cat > badwords.txt <<'EOF'
shit
re:fuck\w*
EOF

# 2. censor
audio-censor podcast.mp4 --subtitles podcast.vtt --words badwords.txt --mode bleep -o clean.mp4
```

The same two steps in PowerShell (Windows), where `cat`/heredocs are not
available. The single-quoted here-string keeps `\w` literal, so the regular
expression reaches the file unchanged:

```powershell
# 1. the word list
@'
shit
re:fuck\w*
'@ | Set-Content -Encoding utf8 badwords.txt

# 2. censor (identical arguments; quote paths that contain spaces)
audio-censor podcast.mp4 --subtitles podcast.vtt --words badwords.txt --mode bleep -o clean.mp4
```

`pip install audio-censor-cli` is the same command in PowerShell, and
`winget install Gyan.FFmpeg` opens the FFmpeg installer from the table above.

Real output from a synthetic 20 s clip (`bash examples/build_demo.sh` reproduces it):

```console
$ audio-censor podcast.mp4 -s podcast.vtt -w badwords.txt --mode bleep -o clean.mp4 \
    --audit audit.csv --clean-subtitles clean.srt
info: transcript: podcast.vtt (vtt, 4 cues, 24 words)
info: media: 20.000s, 48000 Hz, 1 ch, video copied
info: 2 match(es) -> 2 masked range(s) in bleep mode
  #1   00:00:09.667 - 00:00:10.083 (0.417s)  'shit'  cue 2
  #2   00:00:15.355 - 00:00:16.258 (0.903s)  'fucking'  cue 3
info: audit log: audit.csv
info: censored subtitles: clean.srt
ok: beeps mixed in: clean.mp4 (0.90s)
```

The video stream really is copied — same MD5 for the compressed video packets
(the hex value depends on the FFmpeg/x264 build; what matters is that both
lines match):

```console
$ ffmpeg -v error -i podcast.mp4 -map 0:v -c copy -f md5 -
MD5=aa1eebdecacd6370eecc3a934725419f
$ ffmpeg -v error -i clean.mp4 -map 0:v -c copy -f md5 -
MD5=aa1eebdecacd6370eecc3a934725419f
```

And the beep is measurable at 1 kHz where the word used to be:

```console
$ ffmpeg -v error -i clean.mp4 -vn -ac 1 -ar 48000 -f s16le - | ...
masked 9.70-10.00 s : 1000 Hz = 0.300, 440 Hz = 0.000
normal 12.00-14.00 s: 1000 Hz = 0.000, 440 Hz = 0.125
largest sample step : 0.039  (a 1 kHz sine at 0.3 amplitude is 0.039)
```

That last line is the point: the fades are smooth enough that the biggest
sample-to-sample jump in the whole file is the natural slope of the beep, not a
click.

## Masking modes

| `--mode` | What replaces the word | When to use |
|---|---|---|
| `bleep` (default) | 1 kHz sine, 5 ms fade in/out | YouTube/TV style bleep |
| `mute` | digital silence, 5 ms fade in/out | call-centre recordings, PII |
| `reverse` | the same slice played backwards, 5 ms fades | comedy effect, kids' content |

Every mode replaces the audio inside the masked ranges and leaves the rest in
place. The audio stream itself is re-encoded — AAC, Opus or MP3 depending on the
container — so the whole track is decoded and encoded again and lossy formats
lose a little quality everywhere, not only inside the masked ranges. The video
stream is still copied bit-for-bit. When you need the untouched parts to stay
bit-exact, write to `.wav` or `.flac` (or pass a lossless `--audio-codec`).

## Transcript formats

| Format | Detection | Word timings |
|---|---|---|
| SubRip (`.srt`) | suffix or content | cue text is split into words, the cue duration is distributed by word length |
| WebVTT (`.vtt`) | suffix or `WEBVTT` header | same as SRT |
| Whisper JSON (`.json`) | suffix or content | real word timings from `segments[].words[]` are used verbatim |

Supported JSON shapes: `faster-whisper` / `openai-whisper` (`segments` with or
without `words`), flat `{"words": [...]}`, a bare list of segments, and
`whisper.cpp` output (`transcription[].offsets` in milliseconds).

Timestamps must be real times: `null`, negative, `NaN`/`Infinity` (which JSON
parsers happily accept) and non-numeric strings are treated as missing. A word
without a usable range falls back to the segment's distributed timing; a segment
without any usable start/end is reported as an error instead of masking the
wrong place in the file.

SRT/VTT only carry cue level timing, so per-word ranges are an approximation:
the cue duration is split between its words in proportion to word length. Real
speech has pauses, uneven pacing and long words, so the estimated boundaries can
be off by much more than a few milliseconds. That is usually good enough for a
bleep, but treat the timings as approximate and use Whisper JSON word timings
when the placement needs to be precise.

### Exact word timings, and looking before you publish

Generate the transcript once with word level timestamps, then check the ranges
before anything is rendered. `--dry-run` prints the FFmpeg command and the match
table and writes no files at all:

```bash
whisper podcast.mp3 --model small --word_timestamps True --output_format json

audio-censor podcast.mp4 -s podcast.json -w badwords.txt --dry-run   # what would be masked?
audio-censor podcast.mp4 -s podcast.json -w badwords.txt -o clean.mp4
```

`examples/whisper-demo.json` is a complete sample of that JSON shape (fake
transcript, shipped with the repo). Run against the clip produced by
`examples/build_demo.sh`, it reports the boundaries written in the file:

```console
$ audio-censor podcast.mp4 -s podcast.json -w badwords.txt -o clean.mp4
info: transcript: podcast.json (whisper, 4 cues, 19 words)
info: media: 20.000s, 48000 Hz, 1 ch, video copied
info: 2 match(es) -> 2 masked range(s) in bleep mode
  #1   00:00:09.620 - 00:00:10.020 (0.400s)  'shit'  cue 2
  #2   00:00:15.320 - 00:00:16.200 (0.880s)  'fucking'  cue 3
ok: beeps mixed in: clean.mp4 (1.03s)
```

Those two ranges are the ones stored in the JSON -- `9.62` s became `9.620` s,
not an estimate. With an SRT or VTT transcript the same command prints wider,
guessed ranges: run the dry run first, listen at the printed timecodes, and add
`--pad-ms 50` when a beep starts just after the word does.

## Options

```
positional arguments:
  input                 input audio or video file

required:
  -s, --subtitles PATH  timestamped transcript (.srt, .vtt or Whisper .json)
  -w, --words SPEC      word list file, or a comma separated list "fuck,shit"
                        (--bad-words is an alias)

masking:
  -m, --mode {bleep,mute,reverse}
  --beep-freq HZ        bleep frequency (default 1000)
  --beep-volume V       bleep amplitude, 0-1 (default 0.3)
  --fade-ms MS          edge fade length (default 5)
  --pad-ms MS           grow every range on both sides (default 0)
  --offset-ms MS        shift the whole transcript, negative allowed (default 0)
  --max-gap-ms MS       max pause inside a multi-word phrase (default 1500)
  --fuzzy N             tolerate N edits per word, e.g. --fuzzy 1 (default 0)

output:
  -o, --output PATH     default: <input>.censored.<ext>
  --audit PATH          CSV audit log of every masked word
  --clean-subtitles PATH  censored copy of the subtitles (clean.srt)
  --mask-text TEXT      replacement used by --clean-subtitles (default [BLEEP])
  --audio-codec CODEC   override the encoder chosen from the container
  --audio-bitrate RATE  e.g. 192k
  --keep-filter-script PATH  keep the generated filter graph
  --dry-run             print the FFmpeg command, write nothing
  --json                machine readable summary on stdout
  -q, --quiet           errors only
```

Exit codes: `0` success (including "nothing matched", which remuxes the file
untouched), `1` runtime error, `2` bad command line.

Every numeric option must be a finite number. `nan`, `inf` and `-inf` parse as
floats but would silently defeat the checks that use them — `--max-gap-ms nan`
would let a multi-word phrase match across any pause, `--fade-ms inf` would
produce a graph FFmpeg cannot ramp — so the CLI rejects them with exit code `2`
and a message naming the option. The matcher, interval builder and filter
builder enforce the same rule for library callers.

`--dry-run` writes nothing at all: no output file, no audit CSV, no censored
subtitles and no retained filter script. With `--json` it prints exactly one
JSON document (the graph is in `filter_graph`), so `... --dry-run --json | jq`
works.

Every output path (`--output`, `--audit`, `--clean-subtitles`,
`--keep-filter-script`) is checked before anything is written. It must differ
from the input, from the transcript and from every other output, so a typo can
never overwrite the source media or subtitles.

## Word lists

One entry per line. A line that starts with `#` is a comment; comments must be
on their own line, because `#` can be part of a regular expression:

```
# obvious demo words only
shit
son of a bitch
# regular expression, opt in with the re: prefix
re:fuck\w*
# stretched spellings
re:s+h+i+t+
```

An argument that clearly looks like a file (`badwords.txt`, a path containing a
separator, or a `~`/`.` prefix) must exist; a typo is an error instead of being
silently used as a literal word. Bare comma-free words such as `-w shit` are
still treated as an inline list.

* Literal entries match **whole words**, case-insensitively, with Turkish
  `I/İ/ı` folding and `ğ/ş/ç/ö/ü` reduced to their ASCII base, so a transcript
  written with or without diacritics matches the same list. `fuck` does not
  match `fucking`; use `re:fuck\w*` for stems.
* `re:` entries are raw Python regular expressions (`re.IGNORECASE`) evaluated
  against the normalised token stream.
* `--fuzzy 1` additionally matches words within one edit, after collapsing
  stretched letters, so `salllaaak` still hits `sallak`.

## How it works

```
SRT/VTT/JSON ──► parser ──► word tokens (start, end, text)
                                │
                       matcher ─┴─► ranges ──► merge + pad + clamp
                                                  │
                                    filter_builder ──► filter_complex script
                                                  │
                                             engine ──► ffmpeg -c:v copy
```

The gain envelope is assembled from constant sources instead of one giant
`if(between(t,...))` expression: each masked range becomes an `aevalsrc=-1`
pulse with `afade` edges, the pulses are summed and added to a DC track to get a
0/1 gate, and `amultiply` applies that gate sample-accurately to the main audio.
The beep (or the reversed slice) is mixed on top with `amix`. Consequences:

* the fade is exact to the sample, not to the audio frame (no clicks);
* FFmpeg's ~4 KiB expression limit is never reached, and no command line grows
  past the OS limit — the graph is always written to a file and passed with
  `-filter_complex_script` (or `-/filter_complex` on FFmpeg ≥ 7.1, where the
  old flag was removed);
* wide graphs are reduced in chunks of eight `amix` inputs, so hundreds of
  matches do not produce one pathological node.

`reverse` takes `atrim` slices of an `asplit` copy before `areverse`, so memory
is proportional to the masked speech, not to the file length.

The output is all-or-nothing: FFmpeg writes to a hidden staging file next to the
target (`.<name>.<pid>-<token>.part<suffix>`) and the finished file is renamed
onto the requested path in one `os.replace` step. A missing binary, a timeout or
a failed encode therefore leaves the target untouched -- in particular, an
output file from an earlier run is never truncated by `-y` and then deleted.

## Performance

`python3 scripts/benchmark.py` builds its own synthetic inputs, times the real
CLI end to end (FFprobe and mux included) and prints this table; the run below
is from one machine (Windows 11, Python 3.11.9, FFmpeg n9.0.1):

```
$ python3 scripts/benchmark.py
| Input | Masked ranges | Output | Time |
|---|---|---|---|
| 20 min, 48 kHz stereo WAV | 120 beeps | 29 MB M4A (AAC 192k) | 37.53 s |
| 60 s, 1080p H.264 + AAC | 6 beeps | video MD5 same, AAC audio | 4.12 s |
```

Repeat runs on the same machine land within about 10 % of these numbers, so
quote them as "around 38 s" and "around 4 s", not as guarantees. The script also
asserts that the video stream MD5 is unchanged, which is the measurable form of
the `-c:v copy` claim. `--quick` runs a ~30 s version of the same check (CI runs
that one); `--keep DIR` keeps the generated media.

Decoding and re-encoding the audio track dominates; the video is a stream copy,
so a longer picture costs a file copy rather than an encode. Your disk, CPU and
FFmpeg build will move these numbers. Time grows with audio duration, not with
resolution.

## Limitations (read this)

* **The transcript must be in sync.** If everything lands a second early or
  late, use `--offset-ms -1000`. The tool warns when the transcript runs past
  the end of the media.
* **No speech recognition.** Producing the transcript is your job (Whisper,
  YouTube auto-captions, Premiere/Resolve export, manual subtitles).
* **No guarantee about platform rules.** Masking profanity does not guarantee
  ad approval or monetisation on YouTube or anywhere else; platform policies
  change and are applied by humans and classifiers. This tool only edits audio.
* Video is **always** stream-copied. If the target container cannot hold the
  source codec (e.g. H.264 into `.webm`), FFmpeg will refuse — pick a matching
  container or remux first.
* `--mode reverse` reverses the masked slice only. For long slices the effect
  is obvious; that is the point of the mode.
* Multi-channel audio is ducked on every channel with the same envelope. There
  is no per-stem (dialogue/music) separation yet.

## Development

```bash
python3 -m pip install -e ".[dev]"
bash scripts/check.sh        # lint + format + types + tests + coverage gate
python3 scripts/benchmark.py # regenerate the Performance table (--quick for a smoke run)
```

FFmpeg and FFprobe are required — the gate stops with an explicit message
instead of letting the render tests skip and the coverage floor fail. If the
binaries are not on `PATH`, point at them with `FFMPEG=/path/to/ffmpeg` and
`FFPROBE=/path/to/ffprobe`; `scripts/check.sh` prepends their directory.
Dev tool versions are pinned in `requirements-dev.txt`, which CI installs. The
local gate reads the same pins: when `scripts/check.sh` has to bootstrap a
missing tool it installs the pinned version (`ruff==0.17.3` and so on) rather
than the newest release, so a green local run and a green CI run use identical
Ruff, MyPy and pytest versions. Update the pin file and the gate follows.

The tests import `audio_censor` from the checkout itself (`pythonpath = ["."]`
in `pyproject.toml`), so `python3 -m pytest` and `bash scripts/check.sh` work
even before the editable install — including when pytest is a standalone script
bound to a different interpreter.

`scripts/check.sh` runs Ruff, MyPy (strict), pytest and enforces 85 % branch
coverage (currently ~94 %). It finds the tools through the active interpreter,
`PATH`, `~/.local/bin` or a local `.venv`, and installs anything still missing
before it gives up — so it also works in a fresh checkout where
`pip install -e ".[dev]"` has not run yet. Set `CHECK_NO_INSTALL=1` to turn that
bootstrap off and fail fast instead. Integration tests render real audio with
FFmpeg and verify the 1 kHz beep with a DFT, that no sample step exceeds the
natural slope of the tone (click test), and that the video stream MD5 is
unchanged. The suite fails early if FFmpeg is not on `PATH` instead of silently
skipping every integration test; nothing in the test suite touches the network.

Layout:

```
audio_censor/
├── cli.py             # argument parsing, orchestration, reporting
├── parser.py          # SRT / VTT / Whisper JSON -> word tokens
├── text.py            # Turkish-aware normalisation, Levenshtein
├── matcher.py         # word list -> matches -> merged intervals
├── filter_builder.py  # filter_complex graph
├── engine.py          # ffprobe/ffmpeg invocation, -c:v copy
├── audit.py           # CSV audit log
└── subtitles.py       # censored SRT export
```

## Contributing and project docs

- [CONTRIBUTING.md](CONTRIBUTING.md) — development setup, the `scripts/check.sh`
  gate, coding rules and commit style.
- [CHANGELOG.md](CHANGELOG.md) — Keep a Changelog, 0.1.0 is the first release.
- [SECURITY.md](SECURITY.md) — private vulnerability reporting and threat model.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — Contributor Covenant 2.1.
- Issues labelled `good first issue` are scoped to about one sitting; new
  behaviour needs a test that fails without the change.

## License

MIT © 2026 Yusuf Berat Zengin. See [LICENSE](LICENSE).
