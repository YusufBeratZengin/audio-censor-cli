#!/usr/bin/env bash
#
# Reproduce the demo from README.md: a synthetic 20 s clip, a WebVTT transcript
# and a two-entry word list.  Everything is generated locally with FFmpeg; no
# download and no real people are involved.
#
# Usage: bash examples/build_demo.sh [output-dir]
# Requires: ffmpeg, ffprobe and audio-censor on PATH (or run from a checkout
# with `python3 -m audio_censor`).

set -euo pipefail

OUT_DIR="${1:-demo-out}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -n "${AUDIO_CENSOR:-}" ]; then
  COMMAND=("$AUDIO_CENSOR")
elif command -v audio-censor >/dev/null 2>&1; then
  COMMAND=(audio-censor)
else
  export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
  COMMAND=(python3 -m audio_censor)
fi

mkdir -p "$OUT_DIR"
cd "$OUT_DIR"

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=640x360:rate=25:duration=20" \
  -f lavfi -i "sine=frequency=440:sample_rate=48000:duration=20" \
  -c:v libx264 -preset veryfast -pix_fmt yuv420p -c:a aac -b:a 128k -shortest \
  podcast.mp4

cat > podcast.vtt <<'VTT'
WEBVTT

00:00:02.000 --> 00:00:05.000
welcome back to the show, today we talk about editing

00:00:08.500 --> 00:00:11.000
honestly this shit is tedious

00:00:14.000 --> 00:00:17.500
and that is fucking ridiculous

00:00:18.000 --> 00:00:19.500
see you next week
VTT

cat > badwords.txt <<'WORDS'
# demo list (fake sample words)
shit
re:fuck\w*
WORDS

"${COMMAND[@]}" podcast.mp4 \
  --subtitles podcast.vtt \
  --words badwords.txt \
  --mode bleep \
  -o clean.mp4 \
  --audit audit.csv \
  --clean-subtitles clean.srt

echo
echo "video packets identical:"
for file in podcast.mp4 clean.mp4; do
  printf '  %-12s ' "$file"
  ffmpeg -v error -i "$file" -map 0:v -c copy -f md5 -
done
