"""Local Ollama-powered YouTube metadata for finished music videos."""
import json
import re
from datetime import datetime

import requests
from flask import Blueprint, jsonify, request

from channel_features import OLLAMA_MODEL, OLLAMA_URL, connect, valid_account

bp = Blueprint("video_metadata", __name__, url_prefix="/api")


def initialize():
    with connect() as cn:
        cn.execute("""CREATE TABLE IF NOT EXISTS finished_video_metadata (
            video_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
            keywords TEXT NOT NULL DEFAULT '[]', tags TEXT NOT NULL DEFAULT '[]',
            hashtags TEXT NOT NULL DEFAULT '[]', updated TEXT NOT NULL
        )""")


def context(video_id, account_id):
    account = valid_account(account_id)
    if not account:
        return None, None, None, []
    with connect() as cn:
        video = cn.execute("SELECT * FROM finished_videos WHERE id=? AND account_id=?",
                           (video_id, account_id)).fetchone()
        if not video:
            return account, None, None, []
        mix = cn.execute("SELECT * FROM finished_mp3s WHERE id=? AND account_id=?",
                         (video["mp3_id"], account_id)).fetchone()
        songs = []
        if mix:
            try:
                ids = json.loads(mix["track_ids"])
            except (ValueError, TypeError):
                ids = []
            if isinstance(ids, list):
                for sid in ids[:100]:
                    item = cn.execute(
                        "SELECT title,prompt,duration FROM songs WHERE id=? AND account_id=?",
                        (sid, account_id)).fetchone()
                    if item:
                        songs.append(dict(item))
    return account, dict(video), dict(mix) if mix else None, songs


def get_saved(account_id, video_id):
    with connect() as cn:
        row = cn.execute("SELECT * FROM finished_video_metadata WHERE video_id=? AND account_id=?",
                         (video_id, account_id)).fetchone()
    if not row:
        return None
    result = dict(row)
    for key in ("keywords", "tags", "hashtags"):
        try:
            result[key] = json.loads(result[key])
        except (ValueError, TypeError):
            result[key] = []
    return result


def normalize_list(value, limit, max_length, hashtag=False):
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        raise ValueError("Keywords, tags and hashtags must be lists.")
    items = []
    for item in value:
        if not isinstance(item, str):
            continue
        item = re.sub(r"\s+", " ", item.strip())
        item = item.lstrip("#").replace("#", "") if hashtag else item.lstrip("#")
        item = item[:max_length].strip()
        if not item:
            continue
        if hashtag:
            item = re.sub(r"[^\w]", "", item, flags=re.UNICODE)
        if item and item.lower() not in {x.lower() for x in items}:
            items.append(item)
        if len(items) == limit:
            break
    return items


def clean_metadata(data):
    if not isinstance(data, dict):
        raise ValueError("Metadata must be an object.")
    title = str(data.get("title") or "").strip()[:100]
    description = str(data.get("description") or "").strip()[:4500]
    if not title:
        raise ValueError("Enter a YouTube title.")
    if not description:
        raise ValueError("Enter a YouTube description.")
    keywords = normalize_list(data.get("keywords", []), 25, 80)
    tags = normalize_list(data.get("tags", []), 35, 70)
    hashtags = normalize_list(data.get("hashtags", []), 5, 40, True)
    # YouTube's tag limit includes separators; keep within 500 characters.
    shortened = []
    for tag in tags:
        candidate = shortened + [tag]
        if len(", ".join(candidate)) > 500:
            break
        shortened = candidate
    return dict(title=title, description=description, keywords=keywords,
                tags=shortened, hashtags=hashtags)


def save_metadata(account_id, video_id, metadata):
    with connect() as cn:
        cn.execute("""INSERT INTO finished_video_metadata
            (video_id, account_id, title, description, keywords, tags, hashtags, updated)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(video_id) DO UPDATE SET
                title=excluded.title, description=excluded.description,
                keywords=excluded.keywords, tags=excluded.tags,
                hashtags=excluded.hashtags, updated=excluded.updated""",
            (video_id, account_id, metadata["title"], metadata["description"],
             json.dumps(metadata["keywords"]), json.dumps(metadata["tags"]),
             json.dumps(metadata["hashtags"]), datetime.now().isoformat()))
    return get_saved(account_id, video_id)


@bp.get("/finished-videos/<video_id>/metadata")
def read_video_metadata(video_id):
    account_id = request.args.get("account_id", "default")
    _, video, _, _ = context(video_id, account_id)
    if not video:
        return jsonify(error="Video not found for this channel."), 404
    return jsonify(get_saved(account_id, video_id) or {})


@bp.put("/finished-videos/<video_id>/metadata")
def edit_video_metadata(video_id):
    data = request.get_json(silent=True) or {}
    account_id = data.get("account_id", "default")
    _, video, _, _ = context(video_id, account_id)
    if not video:
        return jsonify(error="Video not found for this channel."), 404
    try:
        metadata = clean_metadata(data)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(save_metadata(account_id, video_id, metadata))


@bp.post("/finished-videos/<video_id>/metadata/generate")
def generate_video_metadata(video_id):
    data = request.get_json(silent=True) or {}
    account_id = data.get("account_id", "default")
    account, video, mix, songs = context(video_id, account_id)
    if not video:
        return jsonify(error="Video not found for this channel."), 404
    focus = str(data.get("focus") or "").strip()[:500]
    minutes = max(1, int(round(video["duration"] / 60)))
    track_details = [
        {"title": s["title"], "description": (s.get("prompt") or "")[:280]}
        for s in songs[:16]
    ]
    playlist = ", ".join(s["title"] for s in songs[:30])
    request_prompt = (
        "You are a careful YouTube SEO copywriter for an original AI-generated music channel. "
        "Create publish-ready YouTube metadata based ONLY on the supplied context. "
        "Do not invent artists, licensing, copyright clearance, links, timestamps, "
        "listener testimonials, album releases, or musical instruments not supported by the context. "
        "Write natural engaging language, not keyword stuffing. "
        "Return ONLY a JSON object with keys title (up to 100 characters), description "
        "(200-450 words, include a short introduction, listener use cases, description "
        "of the music, an optional track list of known titles, a simple invitation to subscribe, "
        "and 3-5 relevant hashtags on the last line), keywords (array of 12-20 search phrases), "
        "tags (array of 15-25 distinct short YouTube tags, total under 500 characters), "
        "hashtags (array of 3-5 words WITHOUT #). "
        "Avoid claiming the music is copyright-free, royalty-free, public domain or safe "
        "for commercial reuse. Do not add fictional chapter times.\n"
        + "Context:\n" + json.dumps({
            "video_name": video["title"],
            "video_duration_minutes": minutes,
            "channel": account["name"],
            "channel_genre": account["genre"],
            "channel_direction": account["description"],
            "finished_mp3_name": mix["title"] if mix else "",
            "track_titles": playlist,
            "song_details": track_details,
            "user_seo_direction": focus,
        }, ensure_ascii=False)
    )
    try:
        response = requests.post(
            OLLAMA_URL + "/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": request_prompt, "stream": False,
                  "format": "json", "options": {"temperature": 0.65, "num_predict": 1450}},
            timeout=240
        )
        response.raise_for_status()
        raw = (response.json().get("response") or "").strip()
        generated = json.loads(raw)
        if not isinstance(generated, dict):
            raise ValueError("Ollama did not return metadata fields.")
        metadata = clean_metadata(generated)
        if len(metadata["description"]) < 180:
            raise ValueError("Ollama generated a description that was too short.")
        if not metadata["tags"] or not metadata["keywords"]:
            raise ValueError("Ollama returned no keywords or tags.")
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        return jsonify(error="Could not generate metadata using local Ollama (" +
                       OLLAMA_MODEL + "). Confirm Ollama is running. Details: " + str(exc)), 502
    return jsonify(save_metadata(account_id, video_id, metadata))
