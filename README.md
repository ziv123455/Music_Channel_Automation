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

## Multi-channel workflow (free local tools)

1. **Account Manager:** add channel name, email, genre and creative direction. Emails are stored as labels locally; this is **not** a YouTube account login or OAuth connection. Existing songs and compilations automatically migrate to `Default Channel`; they are not deleted.
2. **Ollama prompt AI:** install free Ollama from https://ollama.com/download/windows . Open PowerShell and run:
   ```powershell
   ollama pull llama3.2:3b
   ```
   Leave Ollama running in the background. The dashboard checks `http://127.0.0.1:11434/api/tags`. Change the model by setting the `OLLAMA_MODEL` environment variable before starting the dashboard. A 3B model is selected to fit alongside ACE-Step on an 8 GB RTX 4060 Ti; the service unloads the text model after each prompt request. GPU/CPU memory is still shared, so close other GPU-heavy apps while generating.
3. **Music generation:** select a channel, enable "Use free Ollama AI to write a different prompt for every song", choose a theme, song count (up to 10) and desired seconds per song. The model writes an original production prompt for every track and ACE-Step renders them sequentially. Each saved song stores the exact generated prompt and account ID.
4. **Thumbnail Studio:** generate a custom 1280 x 720 PNG using any of five color palettes, title and optional uploaded background image. This is a **free template-based graphic generator**, not a text-to-image AI model; it works entirely offline and requires no new GPU workload. The most recently generated thumbnail is remembered per channel and becomes the background of the compilation video. Download the PNG for the separate YouTube thumbnail upload.
5. **Automatic compilation:** tick the auto compilation option before generating a batch. Once the batch finishes, the app starts rendering all successfully generated tracks, in order, into a YouTube-ready MP4, plus chapter timestamps. The target of 10 x 360 seconds is approximately 60 minutes, but ACE-Step durations can vary; this version does **not** falsely stretch/pad/loop the video to exactly one hour.

**Important:** The local Flask server has no authentication; run it only on `127.0.0.1`. Your email is metadata only. No YouTube login/upload is included. Generated audio/video, database, artwork and local AI models remain in `data/` and are not synchronized by Git. Existing local data is preserved by non-destructive migrations. The ACE-Step API integration and local Ollama/thumbnail integration have not yet been end-to-end tested on your Windows PC.

**Suggested first test:** keep ACE-Step running, run `ollama pull llama3.2:3b`, restart Flask, add "Classical" account with your email, preview a unique AI prompt, generate one 60-second instrumental track, create an image in Thumbnail Studio and render a short compilation. Once this succeeds, scale to 10 tracks.
