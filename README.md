## Moving the actual AI files into this project (not junctions)

Run `move-ai-folders.bat` **instead of** `setup-workspace.bat` if you want real ACE-Step / Forge folders inside the project. Stop the AI servers and Flask before the move. The script prompts separately before moving each installed engine. If `setup-workspace.bat` was already used, it detects the existing local junction, verifies that it points to an expected Desktop AI installation, and asks before converting it to a real directory. It does not delete AI models or overwrite an existing real folder. If anything fails, inspect both source and destination folders before trying again.

Target layout:

```text
Music_Channel_Automation/
  move-ai-folders.bat        # one-time physical mover
  start-all.bat              # daily start, after migration
  tools/
    ACE-Step-1.5/           # REAL files and its model checkpoints
    stable-diffusion-webui-forge/ # REAL files (only if installed)
  data/                      # local music/videos/library
```

**Ollama:** The Ollama Windows application stays installed normally, because moving its installation directory manually can break its configuration. You can optionally relocate Ollama's downloaded model storage using the `OLLAMA_MODELS` environment variable and the official Ollama setup instructions, but this is not performed by our move script. Our one-click launcher still starts it.

**Git:** The `tools/` directory is ignored. Moving the AI files into this project does **not** upload their enormous models, virtual environments or third-party repositories to GitHub. Sync continues to share our own application source only.

**Windows Python environment:** ACE-Step's existing `.venv` can contain absolute paths; if it fails after moving, run `uv sync` inside `tools\ACE-Step-1.5` (or rebuild its venv). Forge may need its own setup repair after moving. Never share a virtual environment between the apps.

## One-click Windows workspace

After a git pull, use the new entrypoints:

- `setup-workspace.bat` (optional, one time): creates `tools/ACE-Step-1.5` and `tools/stable-diffusion-webui-forge` as **Windows directory junctions** pointing to previously installed AI engines on the Desktop. No models are moved, copied, deleted, or uploaded. Ollama stays installed in its normal Windows location.
- `start-all.bat`: starts ACE-Step (8001), Ollama (11434), Forge (7860, if installed), and the Flask dashboard (5050). Services already listening on those ports are not launched twice. Each service runs in its own console and can be stopped independently. The launcher opens the dashboard in your browser.
- `start.bat`: dashboard alone (still supported).
- `start-auto-sync.bat`: optional Git sync as before; it is **not** started automatically by the all-in-one launcher.

Expected folder view:

```text
Music_Channel_Automation/
  start-all.bat
  setup-workspace.bat
  start.bat
  start-auto-sync.bat
  scripts/
  tools/                       # ignored by Git
    ACE-Step-1.5/              # junction to existing local install
    stable-diffusion-webui-forge/ # junction if Forge installed
  data/                        # ignored by Git; songs, artwork, SQLite
  app.py
  templates/
```

The folder junctions only organize files in Explorer and keep huge models out of Git. They are **not** a single Python runtime and should not share virtual environments: each AI still runs in its own process. You do not need to install Forge to run music; its unavailable status is reported instead. To use Forge's img2img API, ensure Forge is configured to launch with `--api` and a checkpoint is installed. If Forge or ACE-Step is installed somewhere other than the Desktop, create the corresponding junction manually in `tools/` or edit the candidate path list in `scripts/start-all.ps1`. The system listens on loopback only.

**First run:** double-click `setup-workspace.bat` then `start-all.bat`. The first launch may load large models. The one-click launcher does not download or update AI model weights; those must be installed in their individual applications first.

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

## New workflow: songs -> draft compilation -> thumbnail -> final MP4

The **Song Library** belongs to the selected channel. Generate songs with ACE-Step or import MP3/WAV tracks. In **Compilations**, either:
- Select songs in the Song Library, then click "Save draft from selected library tracks"; or
- Click "Pick random songs and create draft" and set a target of 60 minutes. Random selection does not repeat songs. The reported runtime may differ from exactly 60 minutes if the library cannot fill it.

A draft is saved in SQLite with track order and chapter timestamps. Drafts do not generate video until the next step.

### Free reference-guided AI thumbnails (separate installation)

This implementation uses **Stable Diffusion WebUI Forge**, running locally with its AUTOMATIC1111-compatible REST API. Forge and a **compatible image checkpoint/model** must be installed separately; the software does not download Forge or its model automatically. On the NVIDIA RTX 4060 Ti 8 GB, start with a lightweight SD 1.5 checkpoint. Avoid running Forge image generation simultaneously with ACE-Step song generation if VRAM is tight.

Official Forge repository: https://github.com/lllyasviel/stable-diffusion-webui-forge

Windows one-click setup: download the official Forge archive linked in its README, extract it, run `update.bat`, and run `run.bat`. Enable the API by launching with `--api`. For the source checkout, edit `webui-user.bat` to contain `set COMMANDLINE_ARGS=--api` and launch `webui-user.bat`. If using the one-click package, its `run.bat` launcher needs to pass `--api` to the web UI (for example the package's webui-user launch configuration); check that `http://127.0.0.1:7860/sdapi/v1/options` responds before generating. Add/select an appropriate checkpoint in Forge.

In **Thumbnail Studio**, upload example art (you must own it or have permission to use it), save examples to the reference library, choose **one** example image and describe the new artwork. The local Stable Diffusion image-to-image API uses the reference and your prompt to generate a new 16:9 image. It is not a pixel-perfect style clone and currently uses one selected reference per generation. For a lower-resource fallback, use the existing free template-based thumbnail designer. Both methods save results to each channel's **Thumbnail Library**.

Then, in **Compilations**, choose a saved thumbnail and click **Render video with selected thumbnail**. FFmpeg displays that exact image throughout the entire MP4 and joins the tracks in the saved draft order. The image is also downloadable as a YouTube thumbnail PNG.

The new workflow is **local-first**, with no per-image/song API bills. Storage, electricity, GPU and any third-party licensing costs still apply. Do not use other channels' artwork or copyrighted recordings without rights. AI output and account separation are not a substitute for YouTube ownership, disclosure, or monetization rules.

### Status / migration

Previous locally saved songs and compilations remain in SQLite; migration only adds columns and new tables. Old completed compilations are still downloadable. Existing channels, generated songs and older template artwork files are not deleted. Current implementation is committed but requires end-to-end testing on your Windows PC with ACE-Step, Ollama and Forge.
