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
import io

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


def _read_peaks(path, filter_audio=False, fps=FPS):
    """Read amplitude envelopes from decoded audio (low-pass for kicks)."""
    command = ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
               "-ar", "11025"]
    if filter_audio:
        command.extend(["-af", "lowpass=f=170"])
    command.extend(["-f", "s16le", "-"])
    result = subprocess.run(command, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=True)
    pcm = array("h")
    pcm.frombytes(result.stdout)
    if not pcm:
        raise ValueError("Audio is empty or unsupported.")
    chunk = max(1, round(11025 / fps))
    return [max((abs(v) for v in pcm[i:i + chunk]), default=0) / 32768.0
            for i in range(0, len(pcm), chunk)]


def _decode_levels(path):
    """Full spectrum drives spikes; kick-band transients drive image bounce."""
    peaks = _read_peaks(path)
    bass = _read_peaks(path, filter_audio=True)
    # Running background level helps kicks produce *pulses*, not a permanently enlarged image.
    full_smoothed = 0.0
    bass_baseline = 0.0
    kick_smoothed = 0.0
    result = []
    for raw, low in zip(peaks, bass):
        full_target = min(1.0, (raw * 2.1) ** 0.65)
        full_rate = 0.38 if full_target > full_smoothed else 0.18
        full_smoothed += full_rate * (full_target - full_smoothed)
        bass_baseline += 0.035 * (low - bass_baseline)
        transient = max(0.0, low - bass_baseline * 0.72)
        target_kick = min(1.0, transient * 9.0)
        # Fast attack and slower decay for a clean scale bounce.
        rate = 0.65 if target_kick > kick_smoothed else 0.16
        kick_smoothed += rate * (target_kick - kick_smoothed)
        result.append((full_smoothed, kick_smoothed))
    return result


def _background(image_path, color, crop):
    """Build a dark blurred background and the user's precise square crop."""
    with Image.open(image_path) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGB")
        w, h = image.size
        min_side = min(w, h)
        x_fraction, y_fraction, size_fraction = crop
        side = max(1, min(min_side, int(round(size_fraction * min_side))))
        x = max(0, min(w - side, int(round(x_fraction * w))))
        y = max(0, min(h - side, int(round(y_fraction * h))))
        artwork = image.crop((x, y, x + side, y + side))
        cover = artwork.resize((540, 540), Image.Resampling.LANCZOS)
        background = ImageOps.fit(image, SIZE, method=Image.Resampling.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(38))
    background = ImageEnhance.Brightness(background).enhance(0.47)
    return background, cover, (370, 90, 910, 630)


def _paint_cover(frame, cover, color, kick):
    """Composite square art on each frame, gently expanding with the bass beat."""
    side = int(round(540 * (1.0 + 0.065 * kick)))
    if side != 540:
        artwork = cover.resize((side, side), Image.Resampling.BILINEAR)
    else:
        artwork = cover
    x = (SIZE[0] - side) // 2
    y = (SIZE[1] - side) // 2
    frame.paste(artwork, (x, y))
    border = ImageDraw.Draw(frame)
    border.rectangle((x - 1, y - 1, x + side, y + side),
                     outline=color, width=2)


def _render(job_id, sound, image, title, color, sensitivity, crop):
    job = JOBS[job_id]
    output = OUTPUTS / (job_id + ".mp4")
    process = None
    try:
        levels = _decode_levels(sound)
        base, cover, (left, top, right, bottom) = _background(image, color, crop)
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
        for index, (level, kick) in enumerate(levels):
            if process.poll() is not None:
                raise RuntimeError("FFmpeg exited while rendering.")
            frame = base.copy()
            _paint_cover(frame, cover, color, kick)
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
        crop_x = float(request.form.get("crop_x", 0))
        crop_y = float(request.form.get("crop_y", 0))
        crop_size = float(request.form.get("crop_size", 1))
        if not all(math.isfinite(v) for v in (crop_x, crop_y, crop_size)):
            raise ValueError("Invalid crop coordinates.")
        if not (0 <= crop_x <= 1 and 0 <= crop_y <= 1 and 0 < crop_size <= 1):
            raise ValueError("Crop must be inside the source image.")
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
                _render(job_id, sound_path, image_path, job["title"], color, sensitivity,
                        (crop_x, crop_y, crop_size))
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
