"""Create audio-reactive square-art videos from an uploaded song and image."""
from array import array
from datetime import datetime
from pathlib import Path
import math
import shutil
import subprocess
import threading
import uuid
import zipfile

from flask import Blueprint, jsonify, request, send_from_directory
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageChops

bp = Blueprint("visualizer", __name__)
ROOT = Path(__file__).resolve().parent
BASE = ROOT / "data" / "visualizers"
OUTPUTS = BASE / "outputs"
INPUTS = BASE / "uploads"
ZIV_LOGO = ROOT / "assets" / "ziv-logo.webp"
for folder in (INPUTS, OUTPUTS):
    folder.mkdir(parents=True, exist_ok=True)

JOBS = {}
LOCK = threading.Lock()
SIZE = (1280, 720)
COVER_SIZE = 540
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
    """Preserve the exact original crop and produce a clean darkened backdrop."""
    with Image.open(image_path) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGB")
        w, h = image.size
        shorter = min(w, h)
        x_fraction, y_fraction, size_fraction = crop
        side = max(1, min(shorter, int(round(size_fraction * shorter))))
        x = max(0, min(w - side, int(round(x_fraction * w))))
        y = max(0, min(h - side, int(round(y_fraction * h))))
        artwork = image.crop((x, y, x + side, y + side))
        # Render a slightly oversized backdrop once, then pan a 1280x720
        # viewing window over it. This adds motion without reblurring frames.
        background = ImageOps.fit(image, (1408, 792), method=Image.Resampling.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(22))
    background = ImageEnhance.Brightness(background).enhance(.40)
    background = ImageEnhance.Color(background).enhance(.88)
    return background, artwork


def _drifting_background(background, frame_number):
    """Slow, seamless, sinusoidal Ken Burns drift with no uncovered edges."""
    seconds = frame_number / FPS
    # Overscan is 128px horizontal and 72px vertical.
    # Motion stays safely away from source edges at every frame.
    x = 64 + round(31 * math.sin(seconds * 2 * math.pi / 19))
    y = 36 + round(16 * math.sin(seconds * 2 * math.pi / 15 + .6))
    return background.crop((x, y, x + SIZE[0], y + SIZE[1]))


def _paint_cover(frame, cover, kick, resized_cache):
    """Scale from the original crop, never from a previously resized preview."""
    side = round(COVER_SIZE * (1.0 + .045 * kick))
    if side not in resized_cache:
        # Gentle sharpening offsets resampling blur; no fake detail is added.
        artwork = cover.resize((side, side), Image.Resampling.LANCZOS)
        artwork = artwork.filter(ImageFilter.UnsharpMask(radius=1.1, percent=115, threshold=3))
        resized_cache[side] = artwork
        if len(resized_cache) > 48:
            resized_cache.pop(next(iter(resized_cache)))
    artwork = resized_cache[side]
    left = (SIZE[0] - side) // 2
    top = (SIZE[1] - side) // 2
    frame.paste(artwork, (left, top))
    # Subtle bright frame remains attached to the beat-bouncing image.
    border = ImageDraw.Draw(frame)
    border.rectangle((left, top, left + side - 1, top + side - 1),
                     outline=(247, 246, 250), width=2)
    return (left, top, left + side, top + side)


def _chaikin_smooth(points, iterations=3):
    """Round corners without changing the first or last waveform point."""
    if len(points) < 3:
        return list(points)
    result = list(points)
    for _ in range(iterations):
        smoother = [result[0]]
        for first, second in zip(result, result[1:]):
            x1, y1 = first
            x2, y2 = second
            smoother.append((.75*x1 + .25*x2, .75*y1 + .25*y2))
            smoother.append((.25*x1 + .75*x2, .25*y1 + .75*y2))
        smoother.append(result[-1])
        result = smoother
    return result


def _draw_reactive_border(frame, box, color, level, kick, sensitivity, index):
    """Draw antialiased flowing contours, inset from the square's corners."""
    left, top, right, bottom = box
    height = bottom - top
    if height <= 70:
        return

    rgb = tuple(int(color[k:k + 2], 16) for k in (1, 3, 5))
    activity = max(0.0, min(1.0, level * .80 + kick * .35))
    pad = 165
    margin = 30  # Keep the effect away from the top and bottom corners.
    x0 = max(0, int(left - pad))
    y0 = max(0, int(top + margin - 12))
    x1 = min(SIZE[0], int(right + pad))
    y1 = min(SIZE[1], int(bottom - margin + 12))
    width, layer_height = x1 - x0, y1 - y0

    # Supersample only the transparent waveform overlay, not the entire video.
    # Downsampling with LANCZOS removes stairsteps from thin diagonal sections.
    aa = 2
    layer = Image.new("RGBA", (width * aa, layer_height * aa), (0, 0, 0, 0))
    ink = ImageDraw.Draw(layer)
    left_points, right_points = [], []
    span = height - 2 * margin

    for local_y in range(span + 1):
        y = margin + local_y
        t = local_y / span
        # Smoothly taper both ends right into the box edge without extending
        # a curve into the image's top/bottom corner.
        fade = min(1.0, t * 9.0, (1.0 - t) * 9.0)
        fade = max(0.0, fade)
        fade = fade * fade * (3.0 - 2.0 * fade)
        lobes = (
            .56 * ((1.0 + math.sin(t * math.pi * 6.0 + index * .043)) / 2.0) ** 2
            + .29 * ((1.0 + math.sin(t * math.pi * 10.0 - index * .052)) / 2.0) ** 3
            + .15 * ((1.0 + math.sin(t * math.pi * 16.0 + index * .070)) / 2.0) ** 3
        )
        reach = fade * (4.0 + 94.0 * sensitivity * activity * (.22 + .78 * lobes))
        yy = (top + y - y0) * aa
        left_points.append(((left - reach - x0) * aa, yy))
        right_points.append(((right + reach - x0) * aa, yy))

    for points in (left_points, right_points):
        ink.line(points, fill=(*rgb, 255), width=aa * 2, joint="curve")

    layer = layer.resize((width, layer_height), Image.Resampling.LANCZOS)
    alpha = layer.getchannel("A")
    shadow = Image.new("RGBA", layer.size, (9, 6, 15, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(5)).point(
        lambda v: round(v * .37)))
    shadow = ImageChops.offset(shadow, 2, 3)

    glow = Image.new("RGBA", layer.size, (*rgb, 0))
    glow.putalpha(alpha.filter(ImageFilter.GaussianBlur(7)).point(
        lambda v: round(v * .68)))
    bloom = Image.new("RGBA", layer.size, (*rgb, 0))
    bloom.putalpha(alpha.filter(ImageFilter.GaussianBlur(12)).point(
        lambda v: round(v * .17)))

    combined = Image.alpha_composite(shadow, bloom)
    combined = Image.alpha_composite(combined, glow)
    combined = Image.alpha_composite(combined, layer)
    # Hard-clip the glow, shadow, and line outside the bouncing cover.
    # A two-pixel safety inset prevents faint halo seams at the image edge.
    mask = combined.getchannel("A")
    cut = Image.new("L", combined.size, 255)
    cut_draw = ImageDraw.Draw(cut)
    cut_draw.rectangle((left - x0 - 2, top - y0 - 2,
                        right - x0 + 2, bottom - y0 + 2), fill=0)
    mask = ImageChops.multiply(mask, cut)
    frame.paste(combined.convert("RGB"), (x0, y0), mask)


def _apply_color_overlay(frame, color, opacity):
    """Tint the entire finished composition; opacity is a percentage 0..100."""
    if opacity <= 0:
        return frame
    rgb = tuple(int(color[k:k + 2], 16) for k in (1, 3, 5))
    return Image.blend(frame, Image.new("RGB", frame.size, rgb), opacity / 100.0)


def _ziv_watermark():
    """Load the supplied ZIV graphic as a semi-transparent, glowing logo."""
    if not ZIV_LOGO.exists():
        raise FileNotFoundError(f"Missing ZIV logo asset: {ZIV_LOGO}")
    with Image.open(ZIV_LOGO) as raw:
        logo = raw.convert("RGBA")
    logo.thumbnail((166, 94), Image.Resampling.LANCZOS)
    # Keep a crisp white original on top of a subtle purple halo.
    glow = Image.new("RGBA", logo.size, (176, 102, 216, 0))
    glow.putalpha(logo.getchannel("A").filter(
        ImageFilter.GaussianBlur(7)).point(lambda a: round(a * .65)))
    return Image.alpha_composite(glow, logo)


def _paint_ziv(frame, watermark):
    """A fixed upper-right watermark that doesn't obscure the centered artwork."""
    x = SIZE[0] - watermark.width - 34
    y = 23
    frame.paste(watermark, (x, y), watermark)


def _create_thumbnail(background, artwork, overlay_color, overlay_opacity, path):
    """Still cover matching the video composition, minus logo and waves."""
    thumbnail = _drifting_background(background, 0)
    _paint_cover(thumbnail, artwork, 0, {})
    thumbnail = _apply_color_overlay(thumbnail, overlay_color, overlay_opacity)
    thumbnail.save(path, format="JPEG", quality=95, subsampling=0, optimize=True)


def _make_bundle(output_mp4, thumbnail_jpg, target_zip):
    """One browser download, containing both deliverables."""
    with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.write(output_mp4, arcname="music-video.mp4")
        archive.write(thumbnail_jpg, arcname="thumbnail.jpg")


def _render(job_id, sound, image, title, color, sensitivity, crop,
            overlay_color="#000000", overlay_opacity=0.0):
    job = JOBS[job_id]
    output = OUTPUTS / (job_id + ".mp4")
    thumbnail_path = OUTPUTS / (job_id + "-thumbnail.jpg")
    bundle_path = OUTPUTS / (job_id + "-bundle.zip")
    process = None
    try:
        levels = _decode_levels(sound)
        base, cover = _background(image, color, crop)
        # The still uses the same crop, background and tint as the export.
        _create_thumbnail(base, cover, overlay_color, overlay_opacity, thumbnail_path)
        watermark = _ziv_watermark()
        resized_cache = {}
        frames = len(levels)
        process = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1280x720",
             "-r", str(FPS), "-i", "pipe:0", "-i", str(sound),
             "-map", "0:v:0", "-map", "1:a:0",
             "-c:v", "libx264", "-preset", "medium", "-crf", "17",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
             "-movflags", "+faststart", "-shortest", str(output)],
            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        previous = 0.0
        for index, (level, kick) in enumerate(levels):
            if process.poll() is not None:
                raise RuntimeError("FFmpeg exited while rendering.")
            frame = _drifting_background(base, index)
            rect = _paint_cover(frame, cover, kick, resized_cache)
            previous = previous * 0.58 + level * 0.42
            _draw_reactive_border(frame, rect, color, previous, kick, sensitivity, index)
            _paint_ziv(frame, watermark)
            frame = _apply_color_overlay(frame, overlay_color, overlay_opacity)
            try:
                process.stdin.write(frame.tobytes())
            except BrokenPipeError as exc:
                raise RuntimeError("FFmpeg stopped accepting video frames.") from exc
            if index % (FPS * 2) == 0:
                job["progress"] = round(index * 100 / max(frames, 1))
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError("FFmpeg failed to encode the MP4.")
        _make_bundle(output, thumbnail_path, bundle_path)
        job.update(status="completed", progress=100,
                   url=f"/api/visualizers/{job_id}/download",
                   mp4_url=f"/api/visualizers/{job_id}/download/mp4",
                   thumbnail_url=f"/api/visualizers/{job_id}/download/thumbnail",
                   filename=output.name, thumbnail_file=thumbnail_path.name,
                   bundle_file=bundle_path.name)
    except Exception as exc:
        job.update(status="failed", error=str(exc)[:500])
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        output.unlink(missing_ok=True)
        thumbnail_path.unlink(missing_ok=True)
        bundle_path.unlink(missing_ok=True)
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
        overlay_color = str(request.form.get("overlay_color", "#000000"))
        if (len(overlay_color) != 7 or overlay_color[0] != "#"
                or any(ch not in "0123456789abcdefABCDEF"
                       for ch in overlay_color[1:])):
            raise ValueError("Invalid overlay color.")
        overlay_opacity = float(request.form.get("overlay_opacity", 0))
        if not math.isfinite(overlay_opacity) or not 0 <= overlay_opacity <= 100:
            raise ValueError("Overlay opacity must be between 0 and 100.")
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
                        (crop_x, crop_y, crop_size), overlay_color, overlay_opacity)
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
    """Primary download includes both MP4 and clean thumbnail in one ZIP."""
    job = JOBS.get(job_id)
    if not job or job.get("status") != "completed":
        return jsonify(error="Video not ready."), 404
    return send_from_directory(OUTPUTS, job["bundle_file"], as_attachment=True,
                               download_name=(job["title"] or "Visualizer")[:80] + ".zip")


@bp.get("/api/visualizers/<job_id>/download/mp4")
def download_mp4(job_id):
    job = JOBS.get(job_id)
    if not job or job.get("status") != "completed":
        return jsonify(error="Video not ready."), 404
    return send_from_directory(OUTPUTS, job["filename"], as_attachment=True,
                               download_name=(job["title"] or "Visualizer")[:80] + ".mp4")


@bp.get("/api/visualizers/<job_id>/download/thumbnail")
def download_thumbnail(job_id):
    job = JOBS.get(job_id)
    if not job or job.get("status") != "completed":
        return jsonify(error="Thumbnail not ready."), 404
    return send_from_directory(OUTPUTS, job["thumbnail_file"], as_attachment=True,
                               download_name=(job["title"] or "Visualizer")[:80] + "-thumbnail.jpg")
