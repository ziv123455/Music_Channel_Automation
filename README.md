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

## Music Generation tab (ACE-Step 1.5)

The Music Generation tab supports a genre, custom description, instrumental/vocals, desired song duration (30-600 seconds), track prefix, and a batch of 1-10 songs.

It connects to **ACE-Step 1.5 native REST API** running locally (default: `http://127.0.0.1:8001`), submits `POST /release_task`, polls `POST /query_result`, and downloads generated MP3s into the song library. Failed tracks are reported separately. Batch progress is kept in memory, so restarting the Flask app clears the queue state but keeps saved songs.

**Set up:** Install ACE-Step 1.5 separately from its official repository and launch its API service. Verify its health endpoint in a browser at `http://127.0.0.1:8001/health`. If you launched it on another local port, set `ACESTEP_API_URL` before starting `start.bat`. The optional API token comes from the `ACESTEP_API_KEY` environment variable. Do not commit credentials to Git.

Example PowerShell:
```powershell
$env:ACESTEP_API_URL="http://127.0.0.1:8001"
.\start.bat
```

ACE-Step installation, model weights, and compatible GPU resources are not included in this project. Music durations are requested targets, not guarantees. This integration has not yet been tested against your local ACE-Step installation.
