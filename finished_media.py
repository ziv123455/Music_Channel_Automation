"""Finished MP3 mixes and repeat-to-duration thumbnail videos."""
from datetime import datetime
from pathlib import Path
import json
import shutil
import sqlite3
import subprocess
import threading
import tempfile
import time
import uuid

from flask import Blueprint, jsonify, request, send_from_directory
from channel_features import DB, THUMBS, valid_account
from particle_effects import build_particle_frames, STYLES, LEVELS, FPS
from video_editor_effects import normalize_editor, cover_frame

bp = Blueprint("finished_media", __name__, url_prefix="/api")
DATA = DB.parent
SONGS = DATA / "songs"
MP3S = DATA / "finished_mp3s"
OUTPUT_VIDEOS = DATA / "finished_videos"
MP3S.mkdir(parents=True, exist_ok=True)
OUTPUT_VIDEOS.mkdir(parents=True, exist_ok=True)
jobs = {}
lock = threading.Lock()


def connect():
    cn = sqlite3.connect(DB, timeout=20)
    cn.row_factory = sqlite3.Row
    cn.execute("PRAGMA busy_timeout=20000")
    return cn


def initialize():
    with connect() as cn:
        cn.execute("""CREATE TABLE IF NOT EXISTS finished_mp3s (
            id TEXT PRIMARY KEY, account_id TEXT NOT NULL, title TEXT NOT NULL,
            filename TEXT NOT NULL, duration REAL NOT NULL, track_ids TEXT NOT NULL,
            crossfade REAL NOT NULL, created TEXT NOT NULL)""")
        cn.execute("""CREATE TABLE IF NOT EXISTS finished_videos (
            id TEXT PRIMARY KEY, account_id TEXT NOT NULL, title TEXT NOT NULL,
            filename TEXT NOT NULL, mp3_id TEXT NOT NULL, thumbnail_id TEXT NOT NULL,
            duration REAL NOT NULL, created TEXT NOT NULL)""")
        columns = {row["name"] for row in cn.execute("PRAGMA table_info(finished_videos)")}
        if "cover_type" not in columns:
            cn.execute("ALTER TABLE finished_videos ADD COLUMN cover_type TEXT NOT NULL DEFAULT 'thumbnail'")
        if "visualizer" not in columns:
            cn.execute("ALTER TABLE finished_videos ADD COLUMN visualizer INTEGER NOT NULL DEFAULT 0")
        if "particles" not in columns:
            cn.execute("ALTER TABLE finished_videos ADD COLUMN particles TEXT NOT NULL DEFAULT 'none'")
        if "particle_intensity" not in columns:
            cn.execute("ALTER TABLE finished_videos ADD COLUMN particle_intensity TEXT NOT NULL DEFAULT 'normal'")
        if "editor_settings" not in columns:
            cn.execute("ALTER TABLE finished_videos ADD COLUMN editor_settings TEXT NOT NULL DEFAULT '{}'")


def current_account(data=None):
    data = data or {}
    return valid_account(data.get("account_id") or request.args.get("account_id", "default"))


def safe_title(value, default):
    return str(value or default).strip()[:120] or default


def new_job(kind, account_id, output_id):
    job_id = uuid.uuid4().hex
    with lock:
        jobs[job_id] = {"id": job_id, "kind": kind, "account_id": account_id,
                        "output_id": output_id, "status": "queued", "progress": "Queued"}
    return job_id


def job_change(job_id, **fields):
    with lock:
        jobs[job_id].update(fields)


@bp.get("/finished-media/jobs")
def list_jobs():
    account = current_account()
    if not account:
        return jsonify(error="Invalid channel"), 404
    with lock:
        result = [dict(j) for j in jobs.values() if j["account_id"] == account["id"]]
    return jsonify(result[-40:][::-1])


@bp.get("/finished-mp3s")
def list_mp3s():
    account = current_account()
    if not account:
        return jsonify(error="Invalid channel"), 404
    with connect() as cn:
        result = [dict(r) for r in cn.execute(
            "SELECT * FROM finished_mp3s WHERE account_id=? ORDER BY created DESC",
            (account["id"],))]
    return jsonify(result)


@bp.get("/finished-mp3s/<item_id>/audio")
def get_mp3(item_id):
    account = current_account()
    if not account:
        return jsonify(error="Invalid channel"), 404
    with connect() as cn:
        item = cn.execute("SELECT filename FROM finished_mp3s WHERE id=? AND account_id=?",
                          (item_id, account["id"])).fetchone()
    if not item:
        return jsonify(error="Finished MP3 not found."), 404
    return send_from_directory(MP3S, item["filename"], as_attachment=request.args.get("download") == "1")


def build_mp3(job_id, account_id, output_id, title, tracks, crossfade):
    output = MP3S / (output_id + ".mp3")
    try:
        job_change(job_id, status="running", progress="Joining tracks with smooth transitions")
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        for track in tracks:
            source = SONGS / track["filename"]
            if not source.is_file():
                raise FileNotFoundError("Missing audio file: " + track["title"])
            args.extend(["-i", str(source)])
        filters = []
        for index in range(len(tracks)):
            filters.append(
                f"[{index}:a:0]aresample=44100,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                f"asetpts=PTS-STARTPTS[a{index}]"
            )
        if len(tracks) == 1:
            output_label = "[a0]"
        elif crossfade > 0:
            previous = "[a0]"
            for index in range(1, len(tracks)):
                label = f"[mix{index}]"
                filters.append(
                    f"{previous}[a{index}]acrossfade=d={crossfade:.3f}:c1=tri:c2=tri{label}"
                )
                previous = label
            output_label = previous
        else:
            filters.append("".join(f"[a{i}]" for i in range(len(tracks))) +
                           f"concat=n={len(tracks)}:v=0:a=1[mix]")
            output_label = "[mix]"
        args.extend(["-filter_complex", ";".join(filters), "-map", output_label,
                     "-vn", "-codec:a", "libmp3lame", "-b:a", "192k", str(output)])
        proc = subprocess.run(args, capture_output=True, text=True, timeout=7200)
        if proc.returncode != 0:
            raise RuntimeError("FFmpeg audio mix failed: " + proc.stderr[-1100:])
        duration = float(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(output)], text=True).strip())
        if duration <= 0:
            raise RuntimeError("Finished MP3 is empty.")
        with connect() as cn:
            cn.execute("""INSERT INTO finished_mp3s
                (id,account_id,title,filename,duration,track_ids,crossfade,created)
                VALUES (?,?,?,?,?,?,?,?)""",
                (output_id,account_id,title,output.name,duration,
                 json.dumps([r["id"] for r in tracks]),crossfade,datetime.now().isoformat()))
        job_change(job_id, status="done", progress="Finished MP3 saved", duration=duration)
    except Exception as exc:
        output.unlink(missing_ok=True)
        job_change(job_id, status="error", progress=str(exc)[:1400])


@bp.post("/finished-mp3s")
def create_mp3():
    data = request.get_json(silent=True) or {}
    account = current_account(data)
    if not account:
        return jsonify(error="Invalid channel"), 404
    ids = data.get("track_ids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(
        not isinstance(i, str) for i in ids
    ) or len(set(ids)) != len(ids):
        return jsonify(error="Select 1-100 distinct songs in playback order."), 400
    try:
        crossfade = float(data.get("crossfade", 3))
    except (TypeError, ValueError):
        return jsonify(error="Invalid transition length."), 400
    if not 0 <= crossfade <= 12:
        return jsonify(error="Choose 0-12 seconds of crossfade."), 400
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return jsonify(error="Install FFmpeg and ffprobe first."), 503
    with connect() as cn:
        rows = cn.execute(
            "SELECT id,title,filename,duration FROM songs WHERE account_id=?",
            (account["id"],)).fetchall()
    library = {r["id"]: dict(r) for r in rows}
    if any(i not in library for i in ids):
        return jsonify(error="One or more songs are missing from this channel."), 400
    tracks = [library[i] for i in ids]
    if len(tracks) > 1 and crossfade >= min(t["duration"] for t in tracks):
        return jsonify(error="Crossfade must be shorter than every selected song."), 400
    output_id = uuid.uuid4().hex
    title = safe_title(data.get("title"), "Finished Music Mix")
    job_id = new_job("mp3", account["id"], output_id)
    threading.Thread(target=build_mp3,
                     args=(job_id,account["id"],output_id,title,tracks,crossfade),
                     daemon=True).start()
    return jsonify(job_id=job_id, id=output_id), 202


@bp.get("/finished-videos")
def list_videos():
    account = current_account()
    if not account:
        return jsonify(error="Invalid channel"), 404
    with connect() as cn:
        result = [dict(r) for r in cn.execute(
            "SELECT * FROM finished_videos WHERE account_id=? ORDER BY created DESC",
            (account["id"],))]
    return jsonify(result)


@bp.get("/finished-videos/<item_id>/video")
def get_video(item_id):
    account = current_account()
    if not account:
        return jsonify(error="Invalid channel"), 404
    with connect() as cn:
        item = cn.execute("SELECT filename FROM finished_videos WHERE id=? AND account_id=?",
                          (item_id, account["id"])).fetchone()
    if not item:
        return jsonify(error="Video not found."), 404
    return send_from_directory(OUTPUT_VIDEOS, item["filename"], as_attachment=True)


def render_video(job_id, account_id, output_id, title, mp3, thumb, duration, cover_type, visualizer, particles, particle_intensity, editor_settings):
    output = OUTPUT_VIDEOS / (output_id + ".mp4")
    particle_dir = None
    preview_dir = None
    progress_path = None
    try:
        audio_path = MP3S / mp3["filename"]
        image_path = (THUMBS if cover_type == "thumbnail" else DATA / "references") / thumb["filename"]
        if not audio_path.is_file() or not image_path.is_file():
            raise FileNotFoundError("MP3 or thumbnail file is missing.")
        job_change(job_id, status="running", progress="Preparing music video effects",
                   percent=0.0, rendered_seconds=0.0, total_seconds=duration,
                   elapsed_seconds=0.0, eta_seconds=None, speed=0.0)
        preview_dir = tempfile.mkdtemp(prefix="music_video_cover_")
        prepared_image = cover_frame(image_path, Path(preview_dir) / "cover.png", editor_settings["cover"])
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-loop", "1", "-framerate", "24", "-i", str(prepared_image),
                "-stream_loop", "-1", "-i", str(audio_path)]
        background = ("scale=1920:1080:force_original_aspect_ratio=decrease,"
                      "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p")
        filters = []
        video_label = "[bg]"
        if particles != "none":
            particle_dir = tempfile.mkdtemp(prefix="music_video_particles_")
            job_change(job_id, progress="Drawing looping " + particles + " particle animation")
            pattern = build_particle_frames(particle_dir, particles, particle_intensity)
            args.extend(["-stream_loop", "-1", "-framerate", str(FPS * editor_settings["particles"]["speed"] / 100), "-i", pattern])
        if visualizer or particles != "none":
            filters.append("[0:v]" + background + "[bg]")
            if particles != "none":
                fx = editor_settings["particles"]
                pw = int(1920 * fx["size"] / 100)
                ph = int(1080 * fx["size"] / 100)
                p_opacity = fx["opacity"] / 100
                px = int(1920 * fx["x"] / 100 - pw / 2)
                py = int(1080 * fx["y"] / 100 - ph / 2)
                filters.append("[2:v]fps=24,scale=" + str(pw) + ":" + str(ph) +
                               ":flags=bilinear,format=rgba,colorchannelmixer=aa=" +
                               str(p_opacity) + (",gblur=sigma=" + str(fx["blur"])
                               if fx["blur"] else "") + "[fx]")
                filters.append(video_label + "[fx]overlay=x=" + str(px) + ":y=" + str(py) +
                               ":format=auto:shortest=1[scene]")
                video_label = "[scene]"
            if visualizer:
                radial = (
                    "if(between(hypot(X-256\\,Y-256)\\,138\\,142)\\,210\\,"
                    "if(between(hypot(X-256\\,Y-256)\\,142\\,205)\\,"
                    "lum(512*(atan2(Y-256\\,X-256)+PI)/(2*PI)\\,"
                    "512-(hypot(X-256\\,Y-256)-142)*8)\\,0))"
                )
                filters.append("[1:a]asplit=2[aout][viz]")
                filters.append(
                    "[viz]showfreqs=s=512x512:mode=bar:ascale=lin:fscale=log:"
                    "colors=white:rate=24,format=gray,geq=lum=" + radial +
                    ",format=rgba,colorkey=0x000000:0.06:0.1[ring]"
                )
                ring = editor_settings["ring"]
                rr = int(ring["color"][1:3], 16) / 255
                gg = int(ring["color"][3:5], 16) / 255
                bb = int(ring["color"][5:7], 16) / 255
                rw = max(64, int(1920 * ring["size"] / 100))
                ring_x = int(1920 * ring["x"] / 100 - rw / 2)
                ring_y = int(1080 * ring["y"] / 100 - rw / 2)
                filters.append("[ring]scale=" + str(rw) + ":" + str(rw) +
                               ",colorchannelmixer=rr=" + str(rr) + ":gg=" + str(gg) +
                               ":bb=" + str(bb) + ":aa=" + str(ring["opacity"] / 100) + "[ringedit]")
                filters.append(video_label + "[ringedit]overlay=" + str(ring_x) + ":" +
                               str(ring_y) + ":shortest=1[v]")
                video_label = "[v]"
            args.extend(["-filter_complex", ";".join(filters),
                         "-map", video_label, "-map", "[aout]" if visualizer else "1:a:0"])
        else:
            args.extend(["-vf", background, "-map", "0:v:0", "-map", "1:a:0"])
        job_change(job_id, progress="Rendering video with " +
                   ("particles and " if particles != "none" else "") +
                   ("audio spectrum ring" if visualizer else "background image"))
        args.extend(["-t", f"{duration:.3f}",
                     "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
                     "-r", "24", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-b:a", "192k",
                     "-movflags", "+faststart", str(output)])
        # Parse real FFmpeg video timestamps; progress is independent of output file size.
        progress_file = tempfile.NamedTemporaryFile(prefix="music_video_progress_", suffix=".txt", delete=False)
        progress_path = Path(progress_file.name)
        progress_file.close()
        args[1:1] = ["-nostats", "-progress", str(progress_path)]
        render_start = time.monotonic()
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as error_log:
            proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=error_log)
            last_rendered = 0.0
            while proc.poll() is None:
                if time.monotonic() - render_start > 14400:
                    proc.kill()
                    proc.wait()
                    raise TimeoutError("Video rendering exceeded four hours.")
                try:
                    values = {}
                    for line in progress_path.read_text(encoding="utf-8", errors="replace").splitlines():
                        if "=" in line:
                            key, value = line.split("=", 1)
                            values[key] = value.strip()
                    # FFmpeg reports microseconds in out_time_us and out_time_ms.
                    if values.get("out_time_us") or values.get("out_time_ms"):
                        rendered = max(0.0, float(values.get("out_time_us") or values["out_time_ms"]) / 1000000.0)
                    elif values.get("out_time") and values["out_time"] != "N/A":
                        hh, mm, ss = values["out_time"].split(":")
                        rendered = int(hh) * 3600 + int(mm) * 60 + float(ss)
                    else:
                        rendered = last_rendered
                    last_rendered = min(duration, max(last_rendered, rendered))
                    elapsed = max(0.001, time.monotonic() - render_start)
                    speed = last_rendered / elapsed
                    eta = max(0.0, (duration - last_rendered) / speed) if speed > 0.01 else None
                    job_change(job_id, percent=round(min(99.9, 100 * last_rendered / duration), 1),
                               rendered_seconds=round(last_rendered, 1),
                               elapsed_seconds=round(elapsed, 1),
                               eta_seconds=round(eta) if eta is not None else None,
                               speed=round(speed, 2))
                except (OSError, ValueError, TypeError, UnicodeError):
                    pass
                time.sleep(1)
            if proc.returncode != 0:
                error_log.seek(0)
                raise RuntimeError("FFmpeg video render failed: " + error_log.read()[-1100:])
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError("No rendered video was created.")
        with connect() as cn:
            cn.execute("""INSERT INTO finished_videos
                (id,account_id,title,filename,mp3_id,thumbnail_id,duration,created,
                 cover_type,visualizer,particles,particle_intensity,editor_settings)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (output_id,account_id,title,output.name,mp3["id"],thumb["id"],
                 duration,datetime.now().isoformat(),cover_type,int(visualizer),
                 particles,particle_intensity,json.dumps(editor_settings)))
        job_change(job_id, status="done", progress="Finished video saved", percent=100.0,
                   rendered_seconds=duration, total_seconds=duration,
                   elapsed_seconds=round(time.monotonic() - render_start, 1), eta_seconds=0)
    except Exception as exc:
        output.unlink(missing_ok=True)
        job_change(job_id, status="error", progress=str(exc)[:1400])
    finally:
        if progress_path is not None:
            progress_path.unlink(missing_ok=True)
        if particle_dir is not None:
            shutil.rmtree(particle_dir, ignore_errors=True)
        if preview_dir is not None:
            shutil.rmtree(preview_dir, ignore_errors=True)


@bp.post("/finished-videos")
def create_video():
    data = request.get_json(silent=True) or {}
    account = current_account(data)
    if not account:
        return jsonify(error="Invalid channel"), 404
    try:
        duration = float(data.get("duration_seconds"))
    except (TypeError, ValueError):
        return jsonify(error="Enter a valid video duration."), 400
    if not 1 <= duration <= 86400:
        return jsonify(error="Video duration must be between 1 second and 24 hours."), 400
    if not shutil.which("ffmpeg"):
        return jsonify(error="FFmpeg is required to render video."), 503
    with connect() as cn:
        mp3_row = cn.execute("SELECT * FROM finished_mp3s WHERE id=? AND account_id=?",
                            (data.get("mp3_id"), account["id"])).fetchone()
        cover_type = str(data.get("cover_type") or "thumbnail")
        if cover_type not in ("thumbnail", "reference"):
            return jsonify(error="Invalid cover type."), 400
        cover_table = "thumbnail_library" if cover_type == "thumbnail" else "thumbnail_references"
        thumb_row = cn.execute(
            "SELECT * FROM " + cover_table + " WHERE id=? AND account_id=?",
            (data.get("thumbnail_id"), account["id"])
        ).fetchone()
    if not mp3_row or not thumb_row:
        return jsonify(error="Choose a finished MP3 and saved thumbnail for this channel."), 400
    mp3 = dict(mp3_row)
    thumb = dict(thumb_row)
    if duration <= mp3["duration"]:
        return jsonify(error="Video duration must be longer than the selected MP3."), 400
    output_id = uuid.uuid4().hex
    title = safe_title(data.get("title"), "Music Video")
    visualizer = data.get("visualizer") is True
    particles = str(data.get("particles") or "none")
    particle_intensity = str(data.get("particle_intensity") or "normal")
    if particles not in STYLES or particle_intensity not in LEVELS:
        return jsonify(error="Invalid particle effect or intensity."), 400
    try:
        editor_settings = normalize_editor(data.get("editor_settings") or {})
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    job_id = new_job("video", account["id"], output_id)
    threading.Thread(target=render_video,
                     args=(job_id, account["id"], output_id, title, mp3, thumb, duration, cover_type,
                           visualizer, particles, particle_intensity, editor_settings),
                     daemon=True).start()
    return jsonify(job_id=job_id, id=output_id), 202
