from flask import Flask, request, jsonify, render_template, send_from_directory
from pathlib import Path
from datetime import datetime
import sqlite3, subprocess, shutil, uuid, threading, json

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SONGS = DATA / "songs"
VIDEOS = DATA / "videos"
for folder in (SONGS, VIDEOS): folder.mkdir(parents=True, exist_ok=True)
DB = DATA / "studio.sqlite3"
app = Flask(__name__)
jobs = {}

def db():
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    return connection

with db() as conn:
    conn.execute("CREATE TABLE IF NOT EXISTS songs (id TEXT PRIMARY KEY, title TEXT NOT NULL, filename TEXT NOT NULL, duration REAL NOT NULL, created TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS compilations (id TEXT PRIMARY KEY, title TEXT NOT NULL, filename TEXT NOT NULL, chapters TEXT, created TEXT NOT NULL)")

def probe(path):
    output = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], text=True)
    return float(output.strip())

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/api/health")
def health():
    return jsonify(ffmpeg=bool(shutil.which("ffmpeg")), ffprobe=bool(shutil.which("ffprobe")))

@app.get("/api/songs")
def songs():
    with db() as conn:
        return jsonify([dict(row) for row in conn.execute("SELECT * FROM songs ORDER BY created DESC")])

@app.post("/api/upload")
def upload():
    uploaded = request.files.get("audio")
    if not uploaded or not uploaded.filename.lower().endswith((".mp3", ".wav", ".flac", ".m4a", ".ogg")):
        return jsonify(error="Select an MP3, WAV, FLAC, M4A, or OGG audio file."), 400
    song_id = uuid.uuid4().hex
    extension = Path(uploaded.filename).suffix.lower()
    filename = song_id + extension
    path = SONGS / filename
    uploaded.save(path)
    try:
        duration = probe(path)
        if duration <= 0: raise ValueError("Audio is empty")
    except Exception as error:
        path.unlink(missing_ok=True)
        return jsonify(error=f"Unable to read audio: {error}"), 400
    title = (request.form.get("title") or Path(uploaded.filename).stem)[:150]
    with db() as conn:
        conn.execute("INSERT INTO songs VALUES (?, ?, ?, ?, ?)", (song_id, title, filename, duration, datetime.now().isoformat()))
    return jsonify(id=song_id, title=title, duration=duration), 201

@app.get("/api/song/<song_id>")
def song_file(song_id):
    with db() as conn:
        song = conn.execute("SELECT filename FROM songs WHERE id=?", (song_id,)).fetchone()
    if not song: return jsonify(error="Not found"), 404
    return send_from_directory(SONGS, song["filename"])

@app.get("/api/compilations")
def compilations():
    with db() as conn:
        return jsonify([dict(row) for row in conn.execute("SELECT * FROM compilations ORDER BY created DESC")])

@app.get("/api/video/<compilation_id>")
def video_file(compilation_id):
    with db() as conn:
        item = conn.execute("SELECT filename FROM compilations WHERE id=?", (compilation_id,)).fetchone()
    if not item: return jsonify(error="Not found"), 404
    return send_from_directory(VIDEOS, item["filename"], as_attachment=True)

@app.get("/api/tasks/<task_id>")
def task_status(task_id):
    if task_id not in jobs: return jsonify(error="Not found"), 404
    return jsonify(jobs[task_id])

def timestamp(seconds):
    value = int(seconds)
    return f"{value//3600:02}:{value//60%60:02}:{value%60:02}"

def render_job(task_id, track_ids, title):
    jobs[task_id] = {"status": "running"}
    try:
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise RuntimeError("FFmpeg and ffprobe must be installed and on PATH.")
        with db() as conn:
            records = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM songs")}
        tracks = [records[id_] for id_ in track_ids]
        work = VIDEOS / (task_id + "_parts")
        work.mkdir(exist_ok=True)
        chapters = []
        seconds = 0
        segments = []
        for i, track in enumerate(tracks):
            chapters.append(f"{timestamp(seconds)} {track['title']}")
            seconds += track["duration"]
            segment = work / f"{i:03}.wav"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(SONGS / track["filename"]), "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(segment)], check=True)
            segments.append(segment)
            jobs[task_id]["progress"] = f"Converting {i+1}/{len(tracks)}"
        manifest = work / "list.txt"
        manifest.write_text("".join("file '" + str(p.resolve()).replace("'", "'\\''") + "'\n" for p in segments), encoding="utf-8")
        joined = work / "mix.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(manifest), "-c:a", "pcm_s16le", str(joined)], check=True)
        cover = work / "cover.png"
        from PIL import Image, ImageDraw, ImageFont
        im = Image.new("RGB", (1920, 1080), (23, 19, 39))
        draw = ImageDraw.Draw(im)
        draw.ellipse((620, 180, 1300, 860), outline=(173, 97, 232), width=12)
        draw.text((150, 900), title[:90], fill="white")
        im.save(cover)
        output = VIDEOS / (task_id + ".mp4")
        jobs[task_id]["progress"] = "Encoding final video"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", "1", "-i", str(cover), "-i", str(joined), "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p", "-r", "24", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(output)], check=True)
        with db() as conn:
            conn.execute("INSERT INTO compilations VALUES (?, ?, ?, ?, ?)", (task_id, title, output.name, "\n".join(chapters), datetime.now().isoformat()))
        jobs[task_id] = {"status": "done", "id": task_id}
    except Exception as error:
        jobs[task_id] = {"status": "error", "error": str(error)}
    finally:
        shutil.rmtree(VIDEOS / (task_id + "_parts"), ignore_errors=True)

@app.post("/api/compilations")
def create_compilation():
    payload = request.get_json(silent=True) or {}
    ids = payload.get("track_ids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 30 or not all(isinstance(i, str) for i in ids):
        return jsonify(error="Select 1-30 songs."), 400
    with db() as conn:
        count = conn.execute(f"SELECT COUNT(*) FROM songs WHERE id IN ({','.join('?' for _ in ids)})", ids).fetchone()[0]
    if count != len(set(ids)) or len(set(ids)) != len(ids):
        return jsonify(error="Invalid or duplicate song selection."), 400
    title = str(payload.get("title") or "AI Music Compilation").strip()[:100]
    task_id = uuid.uuid4().hex
    jobs[task_id] = {"status": "queued"}
    threading.Thread(target=render_job, args=(task_id, ids, title), daemon=True).start()
    return jsonify(task_id=task_id), 202

@app.post("/api/generate")
def generate():
    return jsonify(error="Local ACE-Step adapter is not configured in this GitHub version. Import audio first; AI generation will be added next."), 501

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
