# Music Channel Automation

Local Windows music-compilation studio built with Flask and FFmpeg.

## Quick start

1. Install Python 3.11+ and FFmpeg (both `ffmpeg` and `ffprobe` must be on PATH).
2. Clone this repository.
3. Double-click `start.bat`.
4. Open http://127.0.0.1:5050.
5. Upload WAV/MP3 songs, select tracks, provide a title, and render an MP4 compilation.

Generated files are stored in `data/`, excluded from Git.

## Auto Git sync

Double-click `start-auto-sync.bat` to sync source changes every 30 seconds. Close that window or press Ctrl+C to stop. Sync fetches, commits, rebases and pushes, but will **not** force-push. Do not enable on a repository containing secrets.

## Scope

Initial version: audio import, 10-track compilation rendering, static cover, chapter timestamps, and local song library.

Planned: local ACE-Step music generation, AI artwork, smoother transitions, direct YouTube upload, automatic duration targeting. Generated tracks need human review and commercial rights verification before publishing.
