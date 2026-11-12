"""Draft-first music compilations and locally generated reference-guided artwork."""
import base64
import io
import json
import os
import random
import re
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request, send_from_directory
from PIL import Image, ImageOps

from channel_features import DB, THUMBS, valid_account

bp = Blueprint("pipeline", __name__, url_prefix="/api")
REFERENCES = DB.parent / "references"
REFERENCES.mkdir(parents=True, exist_ok=True)
IMAGE_API = "http://127.0.0.1:7860"
OLLAMA_API = "http://127.0.0.1:11434"
VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "gemma3:4b")
render_callback = None
render_jobs = None

def connect():
    cn = sqlite3.connect(DB, timeout=20)
    cn.row_factory = sqlite3.Row
    cn.execute("PRAGMA busy_timeout=20000")
    return cn

def init():
    with connect() as cn:
        cn.execute("""CREATE TABLE IF NOT EXISTS thumbnail_library (
            id TEXT PRIMARY KEY, account_id TEXT NOT NULL, filename TEXT NOT NULL,
            title TEXT NOT NULL, method TEXT NOT NULL, reference_id TEXT,
            created TEXT NOT NULL)""")
        cn.execute("""CREATE TABLE IF NOT EXISTS thumbnail_references (
            id TEXT PRIMARY KEY, account_id TEXT NOT NULL, filename TEXT NOT NULL,
            title TEXT NOT NULL, created TEXT NOT NULL)""")
        columns = {r["name"] for r in cn.execute("PRAGMA table_info(compilations)")}
        for column, definition in (("track_ids", "TEXT NOT NULL DEFAULT '[]'"),
                                   ("status", "TEXT NOT NULL DEFAULT 'ready'"),
                                   ("duration", "REAL NOT NULL DEFAULT 0")):
            if column not in columns:
                cn.execute("ALTER TABLE compilations ADD COLUMN " + column + " " + definition)

def configure_render(callback, jobs):
    global render_callback, render_jobs
    render_callback = callback
    render_jobs = jobs

def current_account(data):
    account_id = data.get("account_id") or request.args.get("account_id", "default")
    return valid_account(account_id)

def available_songs(account_id):
    with connect() as cn:
        return [dict(r) for r in cn.execute(
            "SELECT id,title,duration FROM songs WHERE account_id=? AND duration>0 ORDER BY created",
            (account_id,))]

def chapters(songs):
    total = 0.0
    result = []
    for track in songs:
        seconds = int(total)
        result.append(f"{seconds//3600:02}:{(seconds//60)%60:02}:{seconds%60:02} {track['title']}")
        total += track["duration"]
    return "\n".join(result), total

def create_draft(account, songs, title, selection):
    if not songs:
        raise ValueError("No songs selected.")
    draft_id = uuid.uuid4().hex
    chapter_text, total = chapters(songs)
    with connect() as cn:
        cn.execute("""INSERT INTO compilations
            (id,title,filename,chapters,created,account_id,thumbnail,track_ids,status,duration)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (draft_id, title[:100], "", chapter_text, datetime.now().isoformat(),
             account["id"], "", json.dumps([r["id"] for r in songs]), "draft", total))
    return {"id":draft_id,"status":"draft","title":title[:100],"track_ids":[r["id"] for r in songs],
            "chapters":chapter_text,"duration":total,"selection":selection}

@bp.post("/mixes/drafts")
def draft():
    data = request.get_json(silent=True) or {}
    account = current_account(data)
    if not account:
        return jsonify(error="Unknown channel"), 404
    ids = data.get("track_ids") or []
    if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):
        return jsonify(error="Select 1-100 distinct tracks."), 400
    library = {s["id"]:s for s in available_songs(account["id"])}
    if any(i not in library for i in ids):
        return jsonify(error="One or more tracks do not belong to this channel."), 400
    title = str(data.get("title") or "Music Compilation")
    return jsonify(create_draft(account, [library[i] for i in ids], title, "manual")), 201

@bp.post("/mixes/random")
def random_mix():
    data = request.get_json(silent=True) or {}
    account = current_account(data)
    if not account:
        return jsonify(error="Unknown channel"), 404
    try:
        minutes = float(data.get("target_minutes",60))
        limit = int(data.get("max_tracks",30))
    except (ValueError,TypeError):
        return jsonify(error="Invalid duration or track count"),400
    if not 1 <= minutes <= 240 or not 1 <= limit <= 100:
        return jsonify(error="Choose 1-240 minutes and 1-100 tracks"),400
    pool = available_songs(account["id"])
    if not pool:
        return jsonify(error="The selected channel has no songs."), 400
    goal = minutes*60
    best = []
    best_gap = float("inf")
    # Randomized greedy selection prioritizing closeness to the target without repeats.
    for _ in range(80):
        candidates = pool[:]
        random.shuffle(candidates)
        selected = []
        total = 0.0
        for song in candidates:
            if len(selected)>=limit: break
            new_total = total+song["duration"]
            if not selected or abs(goal-new_total)<abs(goal-total):
                selected.append(song)
                total = new_total
                if abs(total-goal)<1: break
        gap=abs(goal-total)
        if gap<best_gap:
            best,best_gap=selected,gap
    title=str(data.get("title") or f"{account['genre']} Music Mix - {int(minutes)} Minutes")
    result=create_draft(account,best,title,"random")
    result["target_seconds"]=goal
    result["difference_seconds"]=round(result["duration"]-goal,2)
    return jsonify(result),201

@bp.get("/mixes/<draft_id>")
def mix_detail(draft_id):
    account=current_account({})
    if not account:return jsonify(error="Unknown channel"),404
    with connect() as cn:
        row=cn.execute("SELECT * FROM compilations WHERE id=? AND account_id=?",
                       (draft_id,account["id"])).fetchone()
    if not row:return jsonify(error="Compilation not found"),404
    return jsonify(dict(row))

@bp.post("/mixes/<draft_id>/render")
def render_mix(draft_id):
    data=request.get_json(silent=True) or {}
    account=current_account(data)
    if not account:return jsonify(error="Unknown channel"),404
    with connect() as cn:
        row=cn.execute("SELECT * FROM compilations WHERE id=? AND account_id=?",
                       (draft_id,account["id"])).fetchone()
        if not row:return jsonify(error="Compilation not found"),404
        if row["status"]=="rendering":return jsonify(error="Video is already rendering."),409
        ids=json.loads(row["track_ids"] or "[]")
        if not ids:return jsonify(error="Old compilation has no saved track list; create a new draft."),400
        library={s["id"] for s in available_songs(account["id"])}
        if any(i not in library for i in ids):
            return jsonify(error="Some compilation songs are missing."),400
        thumb_id=str(data.get("thumbnail_id") or "")
        thumbnail=cn.execute("SELECT filename FROM thumbnail_library WHERE id=? AND account_id=?",
                             (thumb_id,account["id"])).fetchone()
        if not thumbnail:return jsonify(error="Select a thumbnail from this channel's library."),400
        name=thumbnail["filename"]
        if not (THUMBS/name).is_file():
            return jsonify(error="Selected thumbnail file is missing."),400
        cn.execute("UPDATE compilations SET status='rendering',thumbnail=? WHERE id=?",
                   (name,draft_id))
    render_jobs[draft_id]={"status":"queued"}
    try:
        threading.Thread(target=render_callback,args=(draft_id,ids,row["title"],account["id"],name),daemon=True).start()
    except Exception:
        with connect() as cn:
            cn.execute("UPDATE compilations SET status='draft' WHERE id=?",(draft_id,))
        raise
    return jsonify(task_id=draft_id,status="rendering"),202

@bp.get("/thumbnails/library")
def thumbnails():
    account=current_account({})
    if not account:return jsonify(error="Unknown channel"),404
    with connect() as cn:
        return jsonify([dict(r) for r in cn.execute(
            "SELECT * FROM thumbnail_library WHERE account_id=? ORDER BY created DESC",(account["id"],))])

@bp.get("/references")
def references():
    account=current_account({})
    if not account:return jsonify(error="Unknown channel"),404
    with connect() as cn:
        return jsonify([dict(r) for r in cn.execute(
            "SELECT * FROM thumbnail_references WHERE account_id=? ORDER BY created DESC",(account["id"],))])

def process_uploaded_image(upload):
    if not upload or not upload.filename:
        raise ValueError("Choose a reference image.")
    raw=upload.read(8*1024*1024+1)
    if len(raw)>8*1024*1024:
        raise ValueError("Image must be smaller than 8 MB.")
    try:
        img=Image.open(io.BytesIO(raw))
        img.verify()
        img=Image.open(io.BytesIO(raw)).convert("RGB")
        if img.width>6000 or img.height>6000:
            raise ValueError("Image dimensions exceed 6000 pixels.")
        return img
    except Exception as exc:
        raise ValueError("Upload a valid PNG/JPG/WebP image.") from exc

@bp.post("/references")
def upload_reference():
    account=current_account(request.form)
    if not account:return jsonify(error="Unknown channel"),404
    try:im=process_uploaded_image(request.files.get("image"))
    except ValueError as exc:return jsonify(error=str(exc)),400
    image_id=uuid.uuid4().hex
    filename=image_id+".png"
    im.thumbnail((1280,720))
    im.save(REFERENCES/filename,optimize=True)
    title=str(request.form.get("title") or "Reference thumbnail")[:100]
    with connect() as cn:
        cn.execute("INSERT INTO thumbnail_references VALUES (?,?,?,?,?)",
                   (image_id,account["id"],filename,title,datetime.now().isoformat()))
    return jsonify(id=image_id,url="/api/reference/"+filename),201

@bp.get("/reference/<filename>")
def reference_file(filename):
    if not re.fullmatch(r"[a-f0-9]{32}\.png",filename):
        return jsonify(error="Invalid image name"),400
    account=current_account({})
    if not account:return jsonify(error="Unknown channel"),404
    with connect() as cn:
        found=cn.execute("SELECT id FROM thumbnail_references WHERE account_id=? AND filename=?",
                         (account["id"],filename)).fetchone()
    if not found:return jsonify(error="Reference not found"),404
    return send_from_directory(REFERENCES,filename)

@bp.get("/thumbnail-ai/status")
def image_status():
    try:
        response=requests.get(IMAGE_API+"/sdapi/v1/options",timeout=4)
        response.raise_for_status()
        return jsonify(connected=True,engine="Forge / A1111-compatible API")
    except requests.RequestException as exc:
        return jsonify(connected=False,error=str(exc))


@bp.get("/thumbnail-ai/vision-status")
def vision_status():
    try:
        response = requests.get(OLLAMA_API + "/api/tags", timeout=6)
        response.raise_for_status()
        installed = [m.get("name", "") for m in response.json().get("models", [])]
        ready = any(m == VISION_MODEL or m.split(":")[0] == VISION_MODEL.split(":")[0] for m in installed)
        return jsonify(connected=True, model=VISION_MODEL, model_ready=ready)
    except requests.RequestException as exc:
        return jsonify(connected=False, model=VISION_MODEL, model_ready=False, error=str(exc))


@bp.post("/thumbnail-ai/suggest-prompt")
def suggest_thumbnail_prompt():
    data = request.get_json(silent=True) or {}
    account = current_account(data)
    if not account:
        return jsonify(error="Unknown channel"), 404
    ref_id = str(data.get("reference_id") or "")
    with connect() as cn:
        ref = cn.execute(
            "SELECT filename FROM thumbnail_references WHERE id=? AND account_id=?",
            (ref_id, account["id"])
        ).fetchone()
    if not ref:
        return jsonify(error="Choose a reference from this channel first."), 400
    source = REFERENCES / ref["filename"]
    if not source.is_file():
        return jsonify(error="Reference image file is missing."), 404
    with Image.open(source) as original:
        image = original.convert("RGB")
        image.thumbnail((768, 768))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
    image_b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    direction = str(data.get("direction") or "").strip()[:600]
    instruction = (
        "You are an expert art director for ORIGINAL YouTube music thumbnail artwork. "
        "Look at the attached reference image and analyze its artistic medium, era, "
        "palette, lighting, mood, visual texture and framing. "
        "Use its STYLE as inspiration, NOT its exact scene, figures, poses, "
        "object placements or iconic composition. Design a DISTINCT original "
        "scene fitting the user's channel and creative direction. "
        "For a classical music channel, music-related subjects such as a large "
        "grand piano or concert hall can be appropriate when requested. "
        "Create a polished SDXL / Juggernaut XL txt2img prompt. "
        "Require one large unmistakable focal subject, readable at small mobile "
        "thumbnail size, realistic object geometry, clear 16:9 cinematic composition, "
        "rich intentional lighting, no text in the AI artwork. "
        "Do not describe the reference as the intended generated image. "
        "Return ONLY JSON with exactly three strings: "
        "prompt (50-110 words describing your NEW image), "
        "negative_prompt (visual defects and unwanted elements, <=65 words), "
        "style_summary (1-2 sentences explaining the reference's visual style). "
        f"Channel genre: {account.get('genre', 'Music')}. "
        f"Channel direction: {str(account.get('description') or '')[:300]}. "
        f"Requested new artwork: {direction or 'Original thumbnail influenced by the reference style'}."
    )
    try:
        response = requests.post(
            OLLAMA_API + "/api/generate",
            json={"model": VISION_MODEL, "prompt": instruction,
                  "images": [image_b64], "stream": False, "format": "json",
                  "options": {"temperature": 0.5, "num_predict": 380}, "keep_alive": 0},
            timeout=240
        )
        response.raise_for_status()
        result = json.loads(response.json().get("response") or "{}")
        prompt = str(result.get("prompt") or "").strip()[:950]
        negative = str(result.get("negative_prompt") or "").strip()[:500]
        summary = str(result.get("style_summary") or "").strip()[:500]
        if len(prompt) < 35:
            raise ValueError("Vision model returned an incomplete image prompt.")
        if not negative:
            negative = "blurry, distorted objects, duplicate subjects, extra limbs, text, watermark, logo"
        return jsonify(prompt=prompt, negative_prompt=negative, style_summary=summary, model=VISION_MODEL)
    except (requests.RequestException, ValueError, TypeError, json.JSONDecodeError) as exc:
        return jsonify(error=(
            f"Ollama vision analysis failed ({VISION_MODEL}). "
            "Run 'ollama pull " + VISION_MODEL + "' once, confirm Ollama is running, "
            "then retry. Details: " + str(exc)[:240]
        )), 503

@bp.post("/thumbnail-ai/generate")
def generate_art():
    data = request.get_json(silent=True) or {}
    account = current_account(data)
    if not account:
        return jsonify(error="Unknown channel"), 404

    mode = str(data.get("mode") or "style").strip()
    if mode not in ("style", "img2img", "text"):
        return jsonify(error="Invalid thumbnail generation mode."), 400

    ref_id = str(data.get("reference_id") or "")
    reference_path = None
    if mode != "text":
        with connect() as cn:
            ref = cn.execute(
                "SELECT filename FROM thumbnail_references WHERE id=? AND account_id=?",
                (ref_id, account["id"])
            ).fetchone()
        if not ref:
            return jsonify(error="Select a saved reference image first."), 400
        reference_path = REFERENCES / ref["filename"]
        if not reference_path.is_file():
            return jsonify(error="Reference image is missing."), 404

    description = str(data.get("prompt") or "").strip()[:1200]
    negative = str(data.get("negative_prompt") or "").strip()[:650]
    title = str(data.get("title") or "AI Music Thumbnail").strip()[:100]
    if not description:
        return jsonify(error="Enter a thumbnail design prompt."), 400

    negative_base = (
        "blurry, duplicate people, duplicate objects, malformed anatomy, "
        "distorted instruments, warped perspective, bad composition, low quality, "
        "text, watermark, logo, signature"
    )
    payload = {
        "prompt": description + ", original professional cinematic music thumbnail artwork, "
                  "clear prominent focal point, wide 16:9 composition, no text",
        "negative_prompt": (negative + ", " + negative_base).strip(", "),
        "width": 1024, "height": 576, "steps": 30,
        "cfg_scale": 5, "sampler_name": "DPM++ 2M",
        "scheduler": "Karras", "batch_size": 1, "n_iter": 1,
        "restore_faces": False, "save_images": False,
        "send_images": True
    }

    if mode == "img2img":
        try:
            strength = float(data.get("strength", 0.45))
        except (TypeError, ValueError):
            return jsonify(error="Invalid img2img transformation strength."), 400
        if not 0.2 <= strength <= 0.95:
            return jsonify(error="Strength must be between 0.2 and 0.95."), 400
        with Image.open(reference_path) as original:
            base = ImageOps.fit(original.convert("RGB"), (1024, 576))
        buffer = io.BytesIO()
        base.save(buffer, format="PNG")
        payload["init_images"] = [base64.b64encode(buffer.getvalue()).decode("ascii")]
        payload["denoising_strength"] = strength
        endpoint = "/sdapi/v1/img2img"
    else:
        endpoint = "/sdapi/v1/txt2img"

    try:
        # Only request Juggernaut if Forge actually lists that checkpoint.
        # Otherwise keep Forge's selected model and report which was used.
        model_response = requests.get(IMAGE_API + "/sdapi/v1/sd-models", timeout=15)
        model_response.raise_for_status()
        models = model_response.json()
        preferred = next(
            (m for m in models if "juggernaut-xl_v9_rundiffusionphoto_v2" in
             str(m.get("filename") or m.get("title") or "").lower()),
            None
        )
        if preferred:
            payload["override_settings"] = {
                "sd_model_checkpoint": preferred.get("title") or preferred.get("model_name")
            }
            payload["override_settings_restore_afterwards"] = True

        response = requests.post(IMAGE_API + endpoint, json=payload, timeout=900)
        response.raise_for_status()
        images = response.json().get("images") or []
        if not images:
            raise ValueError("Forge returned no image.")
        image_bytes = base64.b64decode(images[0].split(",", 1)[-1], validate=True)
        with Image.open(io.BytesIO(image_bytes)) as generated:
            out = generated.convert("RGB")
            out = ImageOps.fit(out, (1280, 720), method=Image.Resampling.LANCZOS)
    except (requests.RequestException, ValueError, OSError, KeyError) as exc:
        return jsonify(error=(
            "Forge thumbnail generation failed. Check Forge at port 7860, "
            "confirm Juggernaut XL is installed, and try again. "
            + str(exc)[:280]
        )), 503

    filename = uuid.uuid4().hex + ".png"
    out.save(THUMBS / filename, optimize=True)
    thumb_id = uuid.uuid4().hex
    with connect() as cn:
        cn.execute("INSERT INTO thumbnail_library VALUES (?,?,?,?,?,?,?)",
                   (thumb_id, account["id"], filename, title,
                    "ai_" + mode, ref_id if mode != "text" else None,
                    datetime.now().isoformat()))
        cn.execute("UPDATE accounts SET thumbnail=? WHERE id=?",
                   (filename, account["id"]))
    return jsonify(id=thumb_id, filename=filename, url="/api/thumbnail/" + filename,
                   mode=mode, model=(preferred or {}).get("title") or "Forge selected checkpoint"), 201
