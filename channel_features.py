"""Channel accounts, local LLM prompts and free custom thumbnail generation."""
import io
import os
import random
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Blueprint, jsonify, request, send_from_directory
from PIL import Image, ImageDraw, ImageFont, ImageFilter

bp = Blueprint("channels", __name__, url_prefix="/api")
DATA = Path(__file__).resolve().parent / "data"
THUMBS = DATA / "thumbnails"
THUMBS.mkdir(parents=True, exist_ok=True)
DB = DATA / "studio.sqlite3"
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")

def connect():
    cn = sqlite3.connect(DB, timeout=20)
    cn.row_factory = sqlite3.Row
    cn.execute("PRAGMA busy_timeout=20000")
    return cn

def initialize():
    """Non-destructive migrations; legacy songs/compilations are assigned to the default channel."""
    with connect() as cn:
        cn.execute("""CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL,
            genre TEXT NOT NULL DEFAULT 'Classical', description TEXT NOT NULL DEFAULT '',
            created TEXT NOT NULL)""")
        existing = cn.execute("SELECT id FROM accounts ORDER BY created LIMIT 1").fetchone()
        if not existing:
            cn.execute("INSERT INTO accounts (id,name,email,genre,description,created) VALUES (?, ?, ?, ?, ?, ?)",
                       ("default", "Default Channel", "", "Lo-fi", "", datetime.now().isoformat()))
        for table in ("songs", "compilations"):
            columns = {r["name"] for r in cn.execute("PRAGMA table_info(" + table + ")")}
            if "account_id" not in columns:
                cn.execute("ALTER TABLE " + table + " ADD COLUMN account_id TEXT NOT NULL DEFAULT 'default'")
        account_columns = {r["name"] for r in cn.execute("PRAGMA table_info(accounts)")}
        if "thumbnail" not in account_columns:
            cn.execute("ALTER TABLE accounts ADD COLUMN thumbnail TEXT NOT NULL DEFAULT ''")
        song_columns = {r["name"] for r in cn.execute("PRAGMA table_info(songs)") }
        if "prompt" not in song_columns:
            cn.execute("ALTER TABLE songs ADD COLUMN prompt TEXT NOT NULL DEFAULT ''")
        comp_columns = {r["name"] for r in cn.execute("PRAGMA table_info(compilations)")}
        if "thumbnail" not in comp_columns:
            cn.execute("ALTER TABLE compilations ADD COLUMN thumbnail TEXT NOT NULL DEFAULT ''")

def valid_account(account_id):
    if not isinstance(account_id, str):
        return None
    with connect() as cn:
        row = cn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        return dict(row) if row else None

@bp.get("/accounts")
def accounts():
    with connect() as cn:
        return jsonify([dict(r) for r in cn.execute("SELECT * FROM accounts ORDER BY created")])

@bp.post("/accounts")
def add_account():
    obj = request.get_json(silent=True) or {}
    name = str(obj.get("name") or "").strip()[:90]
    email = str(obj.get("email") or "").strip()[:250]
    genre = str(obj.get("genre") or "Classical").strip()[:90]
    description = str(obj.get("description") or "").strip()[:500]
    if not name or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        return jsonify(error="Enter a channel name and valid email."), 400
    account_id = uuid.uuid4().hex
    with connect() as cn:
        cn.execute("INSERT INTO accounts (id,name,email,genre,description,created) VALUES (?, ?, ?, ?, ?, ?)",
                   (account_id, name, email, genre, description, datetime.now().isoformat()))
    return jsonify(id=account_id, name=name), 201

@bp.patch("/accounts/<account_id>")
def edit_account(account_id):
    account = valid_account(account_id)
    if not account:
        return jsonify(error="Account not found"), 404
    obj = request.get_json(silent=True) or {}
    for key, maximum in (("name",90), ("email",250), ("genre",90), ("description",500)):
        if key in obj:
            account[key] = str(obj[key]).strip()[:maximum]
    if not account["name"] or (account["email"] and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", account["email"])):
        return jsonify(error="Invalid name/email"), 400
    with connect() as cn:
        cn.execute("UPDATE accounts SET name=?, email=?, genre=?, description=? WHERE id=?",
                   (account["name"], account["email"], account["genre"], account["description"], account_id))
    return jsonify(account)

def ollama_status():
    with requests.get(OLLAMA_URL + "/api/tags", timeout=5) as response:
        response.raise_for_status()
        models = [m.get("name","") for m in response.json().get("models", [])]
    return {"connected": True, "model": OLLAMA_MODEL, "installed_models": models,
            "model_ready": any(m == OLLAMA_MODEL or m.split(":")[0] == OLLAMA_MODEL.split(":")[0] for m in models)}

@bp.get("/prompt-ai/status")
def prompt_status():
    try:
        return jsonify(ollama_status())
    except requests.RequestException as exc:
        return jsonify(connected=False, model=OLLAMA_MODEL, error=str(exc))

def compose_music_prompt(account, base_prompt, index, prior):
    """Use installed Ollama model to create a *new* prompt; fail explicitly if unavailable."""
    previous = "\n".join(prior[-8:])
    instructions = (
        "You are a creative director for ORIGINAL royalty-safe instrumental AI music. "
        "Write ONE distinctive production prompt, 45-95 words, no introduction or quotation marks. "
        "Describe instrumentation, tempo, harmony, arrangement, atmosphere and dynamics. "
        "Do not name living artists, particular recordings, or copyrighted melodies. "
        "Make this track different from the other tracks in the compilation. "
        "No classical composition references that could cause the AI to copy an existing piece.\n"
        f"Channel: {account['name']}\nGenre: {account['genre']}\n"
        f"Channel direction: {account['description']}\n"
        f"Compilation theme: {base_prompt}\nTrack number: {index}\n"
        f"Previous track prompts (avoid repetition):\n{previous}\n"
        "Return only the new music-generation prompt."
    )
    try:
        res = requests.post(OLLAMA_URL + "/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": instructions, "stream": False,
                  "options": {"temperature": 0.95, "num_predict": 160}, "keep_alive": 0}, timeout=180)
        res.raise_for_status()
        result = (res.json().get("response") or "").strip().strip('"')[:1000]
        if len(result) < 30:
            raise RuntimeError("Ollama returned an empty/short prompt")
        return result
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError("Local Ollama prompt generation failed. Install Ollama and run 'ollama pull "
                           + OLLAMA_MODEL + "'. Details: " + str(exc)) from exc

@bp.post("/prompt-ai/preview")
def preview_prompt():
    data = request.get_json(silent=True) or {}
    account = valid_account(data.get("account_id"))
    if not account:
        return jsonify(error="Select a valid channel"), 400
    try:
        result = compose_music_prompt(account, str(data.get("theme") or "Original instrumental music")[:500], 1, [])
        return jsonify(prompt=result)
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 503

def font(size):
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()

def create_thumbnail(account, title, theme, palette, background_image=None):
    """Local zero-cost custom 1280x720 design. Not an AI image synthesis model."""
    colors = {
        "midnight": ((10, 16, 36), (78, 57, 124), (236, 190, 109)),
        "gold": ((26, 19, 28), (94, 67, 56), (240, 200, 130)),
        "forest": ((9, 30, 25), (36, 92, 75), (191, 214, 158)),
        "rose": ((37, 14, 35), (126, 61, 90), (255, 201, 213)),
        "blue": ((10, 23, 53), (37, 105, 159), (160, 214, 248)),
    }
    dark, bright, accent = colors.get(palette, colors["midnight"])
    img = Image.new("RGB", (1280, 720))
    pix = img.load()
    for y in range(720):
        t = y / 719
        shade = tuple(int(dark[i]*(1-t)+bright[i]*t) for i in range(3))
        ImageDraw.Draw(img).line([(0,y),(1280,y)],fill=shade)
    if background_image:
        background_image = Image.open(io.BytesIO(background_image)).convert("RGB")
        ratio = max(1280/background_image.width,720/background_image.height)
        resized = background_image.resize((int(background_image.width*ratio)+1,
                                           int(background_image.height*ratio)+1),Image.Resampling.LANCZOS)
        left = (resized.width-1280)//2
        top = (resized.height-720)//2
        img = resized.crop((left,top,left+1280,top+720))
        img = Image.blend(img, Image.new("RGB",img.size,dark), .38)
    draw = ImageDraw.Draw(img, "RGBA")
    if not background_image:
        for i in range(7):
            x = 720+i*80
            radius = 230+i*13
            draw.ellipse((x-radius,360-radius,x+radius,360+radius),outline=(*accent, 18+i*9),width=4)
        for i in range(25):
            x = 650+i*24
            h = 28+(i*47)%165
            draw.rounded_rectangle((x,360-h,x+11,360+h),radius=5,fill=(*accent,60+i%4*25))
    draw.rounded_rectangle((55,48,560,112),radius=18,fill=(0,0,0,120))
    draw.text((77,60), account["name"][:35].upper(), font=font(28), fill="white")
    main_title = title[:75]
    words=main_title.split()
    lines=[]
    while words and len(lines)<3:
        line=""
        while words and len(line+" "+words[0])<23:
            line=(line+" "+words.pop(0)).strip()
        lines.append(line or words.pop(0))
    for i,line in enumerate(lines):
        draw.text((65,270+i*92),line.upper(),font=font(75),fill="white",stroke_width=3,stroke_fill=(16,12,23))
    draw.rounded_rectangle((60,632,430,688),radius=12,fill=(*accent,220))
    draw.text((78,641),"ONE HOUR MUSIC MIX",font=font(30),fill=(14,18,30))
    image_id=uuid.uuid4().hex+".png"
    img.save(THUMBS/image_id,optimize=True)
    return image_id

@bp.post("/thumbnails")
def thumbnail():
    account_id = request.form.get("account_id")
    account = valid_account(account_id)
    if not account:
        return jsonify(error="Select a valid channel"), 400
    title = request.form.get("title","One Hour Music Mix").strip()[:120]
    palette = request.form.get("palette","gold")
    file = request.files.get("background")
    background = None
    if file and file.filename:
        background = file.read(8*1024*1024+1)
        if len(background)>8*1024*1024:
            return jsonify(error="Background image must be under 8 MB"),400
        try:
            Image.open(io.BytesIO(background)).verify()
        except Exception:
            return jsonify(error="Invalid image upload"),400
    name = create_thumbnail(account, title, account["genre"], palette, background)
    with connect() as cn:
        cn.execute("UPDATE accounts SET thumbnail=? WHERE id=?", (name, account_id))
        cn.execute("INSERT INTO thumbnail_library (id,account_id,filename,title,method,reference_id,created) VALUES (?,?,?,?,?,?,?)",
                   (uuid.uuid4().hex,account_id,name,title,"template",None,datetime.now().isoformat()))
    return jsonify(filename=name, url="/api/thumbnail/"+name)

@bp.get("/thumbnail/<filename>")
def thumbnail_file(filename):
    if not re.fullmatch("[a-f0-9]{32}\\.png", filename):
        return jsonify(error="Invalid filename"),400
    return send_from_directory(THUMBS,filename)
