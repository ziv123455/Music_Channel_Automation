"""Create audio-reactive square-art videos from an uploaded song and image."""
from array import array
from datetime import datetime
from pathlib import Path
import math
import shutil
import subprocess
import threading
import uuid

from flask import Blueprint, jsonify, request, send_from_directory
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

bp = Blueprint("visualizer", __name__)
ROOT = Path(__file__).resolve().parent
BASE = ROOT / "data" / "visualizers"
OUTPUTS = BASE / "outputs"
INPUTS = BASE / "uploads"
for folder in (INPUTS, OUTPUTS):
    folder.mkdir(parents=True, exist_ok=True)

JOBS = {}
LOCK = threading.Lock()
SIZE = (1280, 720)
FPS = 30


def _decode_levels(path, fps=FPS):
    """Decode a low-rate mono copy and measure short-time peak envelopes."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
         "-ar", "11025", "-f", "s16le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    pcm = array("h")
    pcm.frombytes(result.stdout)
    if not pcm:
        raise ValueError("This audio file has no sound.")
    chunk = max(1, round(11025 / fps))
    levels = []
    for start in range(0, len(pcm), chunk):
        samples = pcm[start:start + chunk]
        peak = max((abs(value) for value in samples), default=0) / 32768.0
        levels.append(peak)
    # Transients pulse; quiet sections retain subtle motion.
    smooth = 0.0
    shaped = []
    for level in levels:
        target = min(1.0, (level * 2.1) ** 0.65)
        smooth = (0.62 if target > smooth else 0.82) * smooth + (
            0.38 if target > smooth else 0.18) * target
        shaped.append(smooth)
    return shaped


def _background(image_path, color):
    w, h = SIZE
    with Image.open(image_path) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        background = ImageOps.fit(image, SIZE, method=Image.Resampling.LANCZOS)
        background = background.filter(ImageFilter.GaussianBlur(38))
        background = ImageEnhance.Brightness(background).enhance(0.47)
        cover = ImageOps.fit(image, (540, 540), method=Image.Resampling.LANCZOS)
    x, y = (w - 540) // 2, (h - 540) // 2
    background.paste(cover, (x, y))
    d = ImageDraw.Draw(background)
    d.rectangle((x-1, y-1, x+540, y+540), outline=color, width=2)
    return background, (x, y, x+540, y+540)


def _render(job_id, sound, image, title, color, sensitivity):
    job = JOBS[job_id]
    output = OUTPUTS / (job_id + ".mp4")
    process = None
    try:
        levels = _decode_levels(sound)
        base, (left, top, right, bottom) = _background(image, color)
        frames = len(levels)
        # Keep every spike within the blurred margin around the square.
        max_spike = 110
        spike_count = 84
        y_step = (bottom - top) / spike_count
        process = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1280x720",
             "-r", str(FPS), "-i", "pipe:0", "-i", str(sound),
             "-map", "0:v:0", "-map", "1:a:0",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
             "-movflags", "+faststart", "-shortest", str(output)],
            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        previous = 0.0
        for index, level in enumerate(levels):
            if process.poll() is not None:
                raise RuntimeError("FFmpeg exited while rendering.")
            frame = base.copy()
            draw = ImageDraw.Draw(frame)
            previous = previous * 0.64 + level * 0.36
            for pos in range(spike_count):
                y = round(top + (pos + 0.5) * y_step)
                # A symmetrical, organic spectral-looking pulse at each edge.
                phase = (pos / spike_count) * math.pi * 15
                ripple = (0.5 + 0.5 * math.sin(phase + index * 0.19)) ** 2
                shape = 0.25 + 0.75 * (
                    0.5 + 0.5 * math.sin(pos * 0.73 + index * 0.05)
                ) ** 2
                length = 3 + int(max_spike * sensitivity * previous *
                                 (0.23 + 0.77 * ripple) * shape)
                draw.line([(left, y), (left - length, y)], fill=color, width=2)
                draw.line([(right, y), (right + length, y)], fill=color, width=2)
            try:
                process.stdin.write(frame.tobytes())
            except BrokenPipeError as exc:
                raise RuntimeError("FFmpeg stopped accepting video frames.") from exc
            if index % (FPS * 2) == 0:
                job["progress"] = round(index * 100 / max(frames, 1))
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError("FFmpeg failed to encode the MP4.")
        job.update(status="completed", progress=100,
                   url=f"/api/visualizers/{job_id}/download", filename=output.name)
    except Exception as exc:
        job.update(status="failed", error=str(exc)[:500])
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        output.unlink(missing_ok=True)
    finally:
        sound.unlink(missing_ok=True)
        image.unlink(missing_ok=True)


@bp.post("/api/visualizers")
def create_visualizer():
    if not shutil.which("ffmpeg"):
        return jsonify(error="FFmpeg is required and must be on PATH."), 503
    audio = request.files.get("audio")
    image = request.files.get("image")
    if not audio or not image or not audio.filename or not image.filename:
        return jsonify(error="Choose an audio file and an image."), 400
    audio_ext = Path(audio.filename).suffix.lower()
    image_ext = Path(image.filename).suffix.lower()
    if audio_ext not in {".mp3", ".wav", ".flac", ".m4a", ".ogg"}:
        return jsonify(error="Use MP3, WAV, FLAC, M4A or OGG audio."), 400
    if image_ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        return jsonify(error="Use PNG, JPG or WEBP artwork."), 400
    try:
        color = str(request.form.get("color", "#ffe9df"))
        if len(color) != 7 or color[0] != "#" or any(
                ch not in "0123456789abcdefABCDEF" for ch in color[1:]):
            raise ValueError("Invalid waveform color.")
        sensitivity = float(request.form.get("sensitivity", 1))
        if not 0.2 <= sensitivity <= 2:
            raise ValueError("Sensitivity must be between 0.2 and 2.")
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400
    if not LOCK.acquire(blocking=False):
        return jsonify(error="A visualizer is already rendering. Try again after it finishes."), 409
    job_id = uuid.uuid4().hex
    sound_path = INPUTS / f"{job_id}{audio_ext}"
    image_path = INPUTS / f"{job_id}{image_ext}"
    try:
        audio.save(sound_path)
        image.save(image_path)
        if sound_path.stat().st_size > 350 * 1024 * 1024 or image_path.stat().st_size > 30 * 1024 * 1024:
            raise ValueError("File too large (350 MB audio / 30 MB image maximum).")
        with Image.open(image_path) as im:
            im.verify()
        job = dict(id=job_id, status="running", progress=0,
                   title=str(request.form.get("title") or "Visualizer")[:100],
                   created=datetime.now().isoformat())
        JOBS[job_id] = job

        def work():
            try:
                _render(job_id, sound_path, image_path, job["title"], color, sensitivity)
            finally:
                LOCK.release()

        threading.Thread(target=work, daemon=True).start()
        return jsonify(job_id=job_id), 202
    except Exception as exc:
        sound_path.unlink(missing_ok=True)
        image_path.unlink(missing_ok=True)
        LOCK.release()
        return jsonify(error=str(exc)[:300]), 400


@bp.get("/api/visualizers/<job_id>")
def get_visualizer(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(error="Render job not found; restart clears job history."), 404
    return jsonify(job)


@bp.get("/api/visualizers/<job_id>/download")
def download_visualizer(job_id):
    job = JOBS.get(job_id)
    if not job or job.get("status") != "completed":
        return jsonify(error="Video not ready."), 404
    return send_from_directory(OUTPUTS, job["filename"], as_attachment=True,
                               download_name=(job["title"] or "Visualizer")[:80] + ".mp4")
