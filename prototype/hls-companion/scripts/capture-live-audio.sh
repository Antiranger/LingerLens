#!/usr/bin/env bash
# Capture ~2.5 minutes of live audio from the given URL and convert to 16k mono WAV.
set -u
cd "$(dirname "$0")/.."
URL="${1:-https://www.youtube.com/watch?v=JInec6ORhIk}"
SECONDS_TO_GRAB="${2:-150}"
rm -f runtime/jinec-live-audio.* runtime/jinec-live-16k.wav
./vendor/yt-dlp/yt-dlp.exe --no-warnings -f "wa/worstaudio" --downloader native \
  -o "runtime/jinec-live-audio.%(ext)s" "$URL" > runtime/capture.log 2>&1 &
YTPID=$!
sleep "$SECONDS_TO_GRAB"
kill "$YTPID" 2>/dev/null
sleep 2
taskkill //IM yt-dlp.exe //F > /dev/null 2>&1
ls -la runtime/jinec-live-audio.* 2>/dev/null
for f in runtime/jinec-live-audio.*; do
  case "$f" in
    *.m4a|*.mp4|*.ts|*.part)
      ffmpeg -y -hide_banner -loglevel error -i "$f" -ac 1 -ar 16000 runtime/jinec-live-16k.wav 2>/dev/null \
        && echo "CONVERTED_FROM=$f" && break
      ;;
  esac
done
ffprobe -v error -show_entries format=duration -of csv=p=0 runtime/jinec-live-16k.wav 2>/dev/null
