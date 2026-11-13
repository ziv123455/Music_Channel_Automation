from flask import Flask, request, jsonify, render_template, send_from_directory
from pathlib import Path
from datetime import datetime
import sqlite3, subprocess, shutil, uuid, threading, json
from music_generation import check_connection, generate_one, base_url
from channel_features import bp as channel_blueprint, initialize as initialize_channels, valid_account, compose_music_prompt, connect as channel_db, THUMBS
from compilation_pipeline import bp as pipeline_blueprint, init as initialize_pipeline, configure_render, create_draft
from finished_media import bp as finished_media_blueprint, initialize as initialize_finished_media
from video_metadata import bp as video_metadata_blueprint, initialize as initialize_video_metadata

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
initialize_channels()
with db() as conn:
    conn.execute("""CREATE TABLE IF NOT EXISTS song_folders (
        id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
        name TEXT NOT NULL, created TEXT NOT NULL
    )""")
    existing_columns = {col["name"] for col in conn.execute("PRAGMA table_info(songs)")}
    if "folder_id" not in existing_columns:
        conn.execute("ALTER TABLE songs ADD COLUMN folder_id TEXT")
app.register_blueprint(channel_blueprint)
initialize_pipeline()
app.register_blueprint(pipeline_blueprint)
initialize_finished_media()
app.register_blueprint(finished_media_blueprint)
initialize_video_metadata()
app.register_blueprint(video_metadata_blueprint)

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
    account_id = request.args.get("account_id", "default")
    if not valid_account(account_id): return jsonify(error="Invalid channel"), 404
    with db() as conn:
        return jsonify([dict(row) for row in conn.execute("SELECT * FROM songs WHERE account_id=? ORDER BY created DESC", (account_id,))])

def _folder_name(value):
    name = str(value or "").strip()
    return name if 1 <= len(name) <= 80 else None


@app.get("/api/song-folders")
def list_song_folders():
    account_id = request.args.get("account_id", "default")
    if not valid_account(account_id):
        return jsonify(error="Invalid channel"), 404
    with db() as conn:
        rows = conn.execute(
            "SELECT id, name FROM song_folders WHERE account_id=? ORDER BY name COLLATE NOCASE, created",
            (account_id,)
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/song-folders")
def create_song_folder():
    data = request.get_json(silent=True) or {}
    account_id = str(data.get("account_id") or "default")
    if not valid_account(account_id):
        return jsonify(error="Invalid channel"), 404
    name = _folder_name(data.get("name"))
    if not name:
        return jsonify(error="Folder name must be 1 to 80 characters."), 400
    folder_id = uuid.uuid4().hex
    with db() as conn:
        duplicate = conn.execute(
            "SELECT id FROM song_folders WHERE account_id=? AND name=? COLLATE NOCASE",
            (account_id, name)
        ).fetchone()
        if duplicate:
            return jsonify(error="A folder with that name already exists."), 409
        conn.execute(
            "INSERT INTO song_folders (id, account_id, name, created) VALUES (?,?,?,?)",
            (folder_id, account_id, name, datetime.now().isoformat())
        )
    return jsonify(id=folder_id, name=name), 201


@app.patch("/api/song-folders/<folder_id>")
def rename_song_folder(folder_id):
    data = request.get_json(silent=True) or {}
    account_id = str(data.get("account_id") or "default")
    if not valid_account(account_id):
        return jsonify(error="Invalid channel"), 404
    name = _folder_name(data.get("name"))
    if not name:
        return jsonify(error="Folder name must be 1 to 80 characters."), 400
    with db() as conn:
        exists = conn.execute(
            "SELECT id FROM song_folders WHERE id=? AND account_id=?", (folder_id, account_id)
        ).fetchone()
        if not exists:
            return jsonify(error="Folder not found."), 404
        duplicate = conn.execute(
            "SELECT id FROM song_folders WHERE account_id=? AND name=? COLLATE NOCASE AND id<>?",
            (account_id, name, folder_id)
        ).fetchone()
        if duplicate:
            return jsonify(error="A folder with that name already exists."), 409
        conn.execute(
            "UPDATE song_folders SET name=? WHERE id=? AND account_id=?",
            (name, folder_id, account_id)
        )
    return jsonify(id=folder_id, name=name)


@app.delete("/api/song-folders/<folder_id>")
def delete_song_folder(folder_id):
    account_id = request.args.get("account_id", "default")
    if not valid_account(account_id):
        return jsonify(error="Invalid channel"), 404
    with db() as conn:
        exists = conn.execute(
            "SELECT id FROM song_folders WHERE id=? AND account_id=?", (folder_id, account_id)
        ).fetchone()
        if not exists:
            return jsonify(error="Folder not found."), 404
        conn.execute(
            "UPDATE songs SET folder_id=NULL WHERE account_id=? AND folder_id=?",
            (account_id, folder_id)
        )
        conn.execute("DELETE FROM song_folders WHERE account_id=? AND id=?", (account_id, folder_id))
    return jsonify(deleted=True)


@app.patch("/api/songs/<song_id>")
def edit_song_metadata(song_id):
    data = request.get_json(silent=True) or {}
    account_id = str(data.get("account_id") or "default")
    if not valid_account(account_id):
        return jsonify(error="Invalid channel"), 404
    updates = []
    args = []
    if "title" in data:
        title = str(data["title"] or "").strip()
        if not 1 <= len(title) <= 150:
            return jsonify(error="Song name must be 1 to 150 characters."), 400
        updates.append("title=?")
        args.append(title)
    if "folder_id" in data:
        folder_id = str(data["folder_id"] or "").strip() or None
        if folder_id:
            with db() as conn:
                folder = conn.execute(
                    "SELECT id FROM song_folders WHERE id=? AND account_id=?",
                    (folder_id, account_id)
                ).fetchone()
            if not folder:
                return jsonify(error="Folder does not belong to this channel."), 400
        updates.append("folder_id=?")
        args.append(folder_id)
    if not updates:
        return jsonify(error="No song changes supplied."), 400
    with db() as conn:
        result = conn.execute(
            "UPDATE songs SET " + ", ".join(updates) + " WHERE id=? AND account_id=?",
            args + [song_id, account_id]
        )
        if result.rowcount != 1:
            return jsonify(error="Song not found in this channel."), 404
        song = conn.execute(
            "SELECT id, title, folder_id FROM songs WHERE id=? AND account_id=?",
            (song_id, account_id)
        ).fetchone()
    return jsonify(dict(song))


@app.post("/api/upload")
def upload():
    uploaded = request.files.get("audio")
    if not uploaded or not uploaded.filename.lower().endswith((".mp3", ".wav", ".flac", ".m4a", ".ogg")):
        return jsonify(error="Select an MP3, WAV, FLAC, M4A, or OGG audio file."), 400
    account_id = request.form.get("account_id", "default")
    if not valid_account(account_id): return jsonify(error="Invalid channel"), 404
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
        conn.execute("INSERT INTO songs (id,title,filename,duration,created,account_id,prompt) VALUES (?,?,?,?,?,?,?)", (song_id, title, filename, duration, datetime.now().isoformat(), account_id, ""))
    return jsonify(id=song_id, title=title, duration=duration), 201

@app.delete("/api/songs/<song_id>")
def delete_song(song_id):
    account_id = request.args.get("account_id", "default")
    if not valid_account(account_id):
        return jsonify(error="Invalid channel"), 404
    with db() as conn:
        song = conn.execute("SELECT filename FROM songs WHERE id=? AND account_id=?", (song_id, account_id)).fetchone()
        if not song:
            return jsonify(error="Song not found in this channel."), 404
        for compilation in conn.execute("SELECT title, track_ids, status FROM compilations WHERE account_id=?", (account_id,)):
            if compilation["status"] not in ("draft", "rendering"):
                continue
            try:
                ids = json.loads(compilation["track_ids"] or "[]")
            except (ValueError, TypeError):
                continue
            if song_id in ids:
                return jsonify(error="This song belongs to the active compilation '" + compilation["title"] + "'. Finish or remove its draft before deleting it."), 409
        conn.execute("DELETE FROM songs WHERE id=? AND account_id=?", (song_id, account_id))
    (SONGS / song["filename"]).unlink(missing_ok=True)
    return jsonify(deleted=True, id=song_id)


@app.get("/api/song/<song_id>")
def song_file(song_id):
    with db() as conn:
        song = conn.execute("SELECT filename FROM songs WHERE id=? AND account_id=?", (song_id,request.args.get("account_id","default"))).fetchone()
    if not song: return jsonify(error="Not found"), 404
    return send_from_directory(SONGS, song["filename"])

@app.get("/api/compilations")
def compilations():
    account_id = request.args.get("account_id", "default")
    if not valid_account(account_id): return jsonify(error="Invalid channel"), 404
    with db() as conn:
        return jsonify([dict(row) for row in conn.execute("SELECT * FROM compilations WHERE account_id=? ORDER BY created DESC", (account_id,))])

@app.get("/api/video/<compilation_id>")
def video_file(compilation_id):
    with db() as conn:
        item = conn.execute("SELECT filename FROM compilations WHERE id=? AND account_id=?", (compilation_id,request.args.get("account_id","default"))).fetchone()
    if not item: return jsonify(error="Not found"), 404
    if not item["filename"]: return jsonify(error="This compilation has not been rendered yet"), 409
    return send_from_directory(VIDEOS, item["filename"], as_attachment=True)

@app.get("/api/tasks/<task_id>")
def task_status(task_id):
    if task_id not in jobs: return jsonify(error="Not found"), 404
    return jsonify(jobs[task_id])

def timestamp(seconds):
    value = int(seconds)
    return f"{value//3600:02}:{value//60%60:02}:{value%60:02}"

def render_job(task_id, track_ids, title, account_id="default", thumbnail=""):
    jobs[task_id] = {"status": "running"}
    try:
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise RuntimeError("FFmpeg and ffprobe must be installed and on PATH.")
        with db() as conn:
            records = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM songs WHERE account_id=?", (account_id,))}
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
        if thumbnail:
            import re
            if not re.fullmatch(r"[a-f0-9]{32}\.png", thumbnail) or not (THUMBS / thumbnail).is_file():
                raise ValueError("Invalid saved thumbnail")
            cover = THUMBS / thumbnail
        output = VIDEOS / (task_id + ".mp4")
        jobs[task_id]["progress"] = "Encoding final video"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", "1", "-i", str(cover), "-i", str(joined), "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p", "-r", "24", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(output)], check=True)
        with db() as conn:
            existing=conn.execute("SELECT id FROM compilations WHERE id=? AND account_id=?",(task_id,account_id)).fetchone()
            if existing:
                conn.execute("UPDATE compilations SET filename=?, chapters=?, thumbnail=?, status='ready' WHERE id=? AND account_id=?",
                             (output.name, "\n".join(chapters), thumbnail, task_id, account_id))
            else:
                conn.execute("INSERT INTO compilations (id,title,filename,chapters,created,account_id,thumbnail,status,track_ids,duration) VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (task_id,title,output.name,"\n".join(chapters),datetime.now().isoformat(),account_id,thumbnail,"ready",json.dumps(track_ids),seconds))
        jobs[task_id] = {"status": "done", "id": task_id}
    except Exception as error:
        jobs[task_id] = {"status": "error", "error": str(error)}
        with db() as conn:
            conn.execute("UPDATE compilations SET status='draft' WHERE id=? AND status='rendering'",(task_id,))
    finally:
        shutil.rmtree(VIDEOS / (task_id + "_parts"), ignore_errors=True)

@app.post("/api/compilations")
def create_compilation():
    payload = request.get_json(silent=True) or {}
    ids = payload.get("track_ids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 30 or not all(isinstance(i, str) for i in ids):
        return jsonify(error="Select 1-30 songs."), 400
    account_id = payload.get("account_id", "default")
    if not valid_account(account_id): return jsonify(error="Select a valid channel"), 404
    with db() as conn:
        count = conn.execute(f"SELECT COUNT(*) FROM songs WHERE account_id=? AND id IN ({','.join('?' for _ in ids)})", [account_id]+ids).fetchone()[0]
    if count != len(set(ids)) or len(set(ids)) != len(ids):
        return jsonify(error="Invalid or duplicate song selection."), 400
    title = str(payload.get("title") or "AI Music Compilation").strip()[:100]
    task_id = uuid.uuid4().hex
    jobs[task_id] = {"status": "queued"}
    thumbnail = str(payload.get("thumbnail") or "")
    threading.Thread(target=render_job, args=(task_id, ids, title, account_id, thumbnail), daemon=True).start()
    return jsonify(task_id=task_id), 202

generation_jobs = {}
generation_lock = threading.Lock()

@app.get("/api/generation/connection")
def generation_connection():
    try:
        check_connection()
        return jsonify(connected=True, url=base_url())
    except Exception as error:
        return jsonify(connected=False, error=str(error), url=base_url() if "Invalid" not in str(error) else "invalid")

@app.get("/api/generation/jobs")
def generation_job_list():
    return jsonify(list(generation_jobs.values())[-30:][::-1])

@app.get("/api/generation/jobs/<job_id>")
def generation_job_status(job_id):
    if job_id not in generation_jobs:
        return jsonify(error="Job not found"), 404
    return jsonify(generation_jobs[job_id])

def generation_worker(job_id, payload):
    job = generation_jobs[job_id]
    account = valid_account(payload["account_id"])
    prompts = []
    generated_ids = []
    try:
        for index in range(payload["count"]):
            job["current"] = index + 1
            job["progress"] = "Starting song " + str(index + 1)
            song_id = uuid.uuid4().hex
            filename = song_id + ".mp3"
            path = SONGS / filename
            genre = payload["genre"].strip()
            base_prompt = payload["prompt"].strip()
            if payload.get("reference_prompt"):
                base_prompt = (
                    "Create an ORIGINAL song with similar genre, instruments, tempo feel and mood. "
                    "Invent entirely new melodies, harmonies, phrases and arrangements; never copy "
                    "the reference recording. Source production description: " +
                    payload["reference_prompt"] + ". Additional direction: " + base_prompt
                )
            if payload.get("auto_prompts"):
                job["progress"] = "AI writing distinct prompt for song " + str(index+1)
                try:
                    description = compose_music_prompt(account, base_prompt, index+1, prompts)
                except Exception as exc:
                    job["failures"].append({"song": index+1, "error": str(exc)[:400]})
                    job["status"] = "error"
                    job["progress"] = "AI prompt generation failed; batch stopped"
                    return
            else:
                description = ", ".join(part for part in (genre, base_prompt) if part)
            prompts.append(description)
            title = f"{payload['title_prefix']} {index+1:02d}"
            try:
                generate_one(description, payload["duration"], payload["instrumental"], path,
                             on_progress=lambda message: job.update(progress=message))
                duration = probe(path)
                if duration <= 0:
                    raise ValueError("Generated file has no audio.")
                with db() as conn:
                    conn.execute("INSERT INTO songs (id,title,filename,duration,created,account_id,prompt) VALUES (?,?,?,?,?,?,?)",
                                 (song_id, title, filename, duration, datetime.now().isoformat(), payload["account_id"], description))
                generated_ids.append(song_id)
                job["completed"] += 1
                job["results"].append({"id": song_id, "title": title, "duration": duration})
            except Exception as error:
                path.unlink(missing_ok=True)
                job["failures"].append({"song": index + 1, "error": str(error)[:400]})
                job["progress"] = "Song failed; continuing with next"
        job["status"] = "done" if not job["failures"] else ("partial" if job["completed"] else "error")
        job["progress"] = "Batch finished"
        if payload.get("auto_compile") and generated_ids:
            name = payload.get("compilation_title") or account["name"] + " - Music Compilation"
            with db() as conn:
                song_map={r["id"]:dict(r) for r in conn.execute(
                    "SELECT id,title,duration FROM songs WHERE account_id=?",(payload["account_id"],))}
            draft=create_draft(account,[song_map[i] for i in generated_ids],name,"generated")
            job["draft_id"]=draft["id"]
            job["progress"]="Batch finished; compilation draft created. Choose artwork before rendering."
    except Exception as error:
        job["status"] = "error"
        job["progress"] = str(error)
    finally:
        generation_lock.release()

@app.post("/api/generate")
def generate():
    payload = request.get_json(silent=True) or {}
    try:
        count = int(payload.get("count", 10))
        duration = int(payload.get("duration", 360))
    except (TypeError, ValueError):
        return jsonify(error="Count and duration must be integers."), 400
    if not 1 <= count <= 10 or not 30 <= duration <= 600:
        return jsonify(error="Select 1-10 songs and 30-600 seconds per song."), 400
    account_id = payload.get("account_id", "default")
    account = valid_account(account_id)
    if not account: return jsonify(error="Select a valid channel"), 404
    genre = str(payload.get("genre") or account["genre"]).strip()[:80]
    prompt = str(payload.get("prompt") or "").strip()[:1000]
    title_prefix = str(payload.get("title_prefix") or genre + " Track").strip()[:90]
    reference_song_id = str(payload.get("reference_song_id") or "").strip()
    reference_prompt = ""
    if reference_song_id:
        with db() as conn:
            reference = conn.execute("SELECT prompt FROM songs WHERE id=? AND account_id=?", (reference_song_id, account_id)).fetchone()
        if not reference:
            return jsonify(error="Reference song not found in this channel."), 404
        reference_prompt = str(reference["prompt"] or "").strip()
        if not reference_prompt:
            return jsonify(error="This track has no saved generation prompt. Enter a style description manually."), 400
    if not prompt and not reference_prompt:
        return jsonify(error="Enter a musical description."), 400
    if not shutil.which("ffprobe"):
        return jsonify(error="Install FFmpeg and ffprobe first."), 503
    try:
        check_connection()
    except Exception as error:
        return jsonify(error="ACE-Step is not connected: " + str(error)), 503
    if not generation_lock.acquire(blocking=False):
        return jsonify(error="A generation batch is already running."), 409
    job_id = uuid.uuid4().hex
    generation_jobs[job_id] = {"id": job_id, "status": "running", "current": 0,
                                "count": count, "account_id": account_id, "completed": 0, "results": [], "failures": [],
                                "progress": "Queued", "started": datetime.now().isoformat()}
    settings = {"count": count, "duration": duration, "genre": genre,
                "prompt": prompt, "reference_prompt": reference_prompt, "title_prefix": title_prefix,
                "instrumental": bool(payload.get("instrumental", True)),
                "account_id": account_id, "auto_prompts": bool(payload.get("auto_prompts", False)),
                "auto_compile": bool(payload.get("auto_compile", False)),
                "compilation_title": str(payload.get("compilation_title") or "")[:100],
                "thumbnail": str(payload.get("thumbnail") or "")}
    try:
        threading.Thread(target=generation_worker, args=(job_id, settings), daemon=True).start()
    except Exception:
        generation_lock.release()
        raise
    return jsonify(job_id=job_id), 202

configure_render(render_job,jobs)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
