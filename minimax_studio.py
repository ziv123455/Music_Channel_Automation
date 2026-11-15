"""MiniMax Music 3 Studio local MCP bridge (no extra model server required).

The installed desktop application serves MCP on 127.0.0.1:8765/mcp.
Tools and their schemas are discovered at runtime so compatible updates
do not depend on hardcoded internal Rust endpoints.
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import requests

BASE = os.getenv("MINIMAX_STUDIO_MCP_URL", "http://127.0.0.1:8765/mcp")
MAX_WAIT_SECONDS = 7200


def _rpc(method, params=None, timeout=30):
    data = {"jsonrpc": "2.0", "id": 1, "method": method,
            "params": {**(params or {}), "_meta": {"protocolVersion": "2025-11-25"}}}
    with requests.post(BASE, json=data, timeout=timeout,
                       headers={"Accept": "application/json, text/event-stream",
                                "Content-Type": "application/json",
                                "MCP-Protocol-Version": "2025-11-25"}) as response:
        response.raise_for_status()
        content = response.text
        if "text/event-stream" in response.headers.get("Content-Type", ""):
            events = []
            for line in content.splitlines():
                if line.startswith("data: "):
                    try:
                        event = json.loads(line[6:])
                        if "result" in event or "error" in event:
                            events.append(event)
                    except ValueError:
                        pass
            if not events:
                raise RuntimeError("MiniMax Studio returned no MCP result.")
            packet = events[-1]
        else:
            packet = response.json()
    if packet.get("error"):
        raise RuntimeError("MiniMax Studio: " + str(packet["error"])[:600])
    return packet.get("result", {})


def _unwrap(result):
    if result.get("isError"):
        raise RuntimeError("MiniMax Studio: " + str(result.get("content"))[:700])
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for item in result.get("content", []):
        if item.get("type") == "text":
            try:
                return json.loads(item.get("text", ""))
            except ValueError:
                return {"message": item.get("text", "")}
    return result


def _tools():
    result = _rpc("tools/list")
    return {t["name"]: t for t in result.get("tools", [])}


def _call(tools, name, arguments=None, timeout=45):
    if name not in tools:
        raise RuntimeError("This MiniMax Studio version does not expose " + name +
                           ". Update Studio, then try again.")
    tool = tools[name]
    schema = tool.get("inputSchema", {})
    properties = schema.get("properties", {})
    incoming = arguments or {}
    filtered = {k: v for k, v in incoming.items() if k in properties}
    missing = [x for x in schema.get("required", []) if x not in filtered]
    if missing:
        raise RuntimeError(name + " requires unsupported fields: " + ", ".join(missing) +
                           ". See Settings > Agent (MCP) in MiniMax Studio.")
    return _unwrap(_rpc("tools/call", {"name": name, "arguments": filtered}, timeout))


def status():
    try:
        tools = _tools()
        info = _call(tools, "studio_status")
        return {"connected": True, "engine": "MiniMax Music 3 Studio",
                "status": info, "generation_available": "song_create" in tools}
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        return {"connected": False, "engine": "MiniMax Music 3 Studio",
                "error": str(exc)[:500]}


def _caption(description, genre, instrumental):
    # Music3 expects a structured caption, unlike ACE-Step's plain prompt.
    direction = description.strip()[:1250]
    return (
        "Global Metadata\n"
        "Basic Attributes: " + genre.strip()[:80] + ".\n"
        "Global Emotional Progression: Follow the energy arc and dynamics described below.\n"
        "Application Scenarios & Imagery: Immersive, engaging listening experience.\n"
        "Sonics & Production Profile: " + direction + "\n"
        "Vocal Details\n"
        "Vocal Gender & Timbre: " + ("No vocals, instrumental only." if instrumental else
                                     "Vocals matching the style and energy of the track.") + "\n"
        "Vocal Style: " + ("None." if instrumental else "Natural and expressive.") + "\n"
        "Harmony/Backing Vocals: " + ("None." if instrumental else "As appropriate to the genre.") + "\n"
        "Vocal FX: " + ("None." if instrumental else "Genre-appropriate.") + "\n"
        "Arrangement\n"
        "Instrument Lifecycle Description (Primary/Secondary Layering):\n"
        "Primary: " + direction + "\n"
        "Secondary: Add complementary instruments and detailed musical transitions.\n"
        "Groove & Foundation Progression: Preserve the target tempo, groove, rhythmic "
        "character and stylistic intensity throughout; develop distinct musical sections.\n"
        "Embellishments, Textures & Spatial FX: Detailed transitions, buildups, "
        "contrast between sections, dynamic processing and a cohesive final mix."
    )


def _find(obj, *names):
    if isinstance(obj, dict):
        for name in names:
            if name in obj and obj[name] not in (None, ""):
                return obj[name]
        for value in obj.values():
            found = _find(value, *names)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find(value, *names)
            if found is not None:
                return found
    return None


def _state(obj):
    value = _find(obj, "status", "state")
    return str(value or "").lower()


def _extract_file(obj):
    if not isinstance(obj, (list, dict)):
        return None
    names = ("path", "file_path", "audio_path", "output_path", "saved_path",
             "absolute_path", "flac_path", "mp3_path")
    value = _find(obj, *names)
    if isinstance(value, str) and Path(value).is_file():
        return Path(value)
    return None


def _download_result_file(tools, song_id):
    # Studio stores its audio on the same Windows computer as Flask.
    files = _call(tools, "library_song_files", {"song_id": song_id, "id": song_id})
    source = _extract_file(files)
    if not source:
        detail = _call(tools, "library_song_get", {"song_id": song_id, "id": song_id})
        source = _extract_file(detail)
    if not source:
        raise RuntimeError("Song generated in MiniMax Studio but its audio path "
                           "was not exposed. Open its Library, export audio, and "
                           "import it manually into Music Channel Automation.")
    return source


def generate_one(description, genre, duration, instrumental, target, title,
                 on_progress=None):
    tools = _tools()
    _call(tools, "studio_status")
    caption = _caption(description, genre, instrumental)
    lyrics = "[intro]\n\n[instrumental]\n\n[bridge]\n\n[instrumental]\n\n[outro]" if instrumental else ""
    args = {"title": title, "caption": caption, "lyrics": lyrics,
            "cover_prompt": "", "duration_seconds": duration, "instrumental": instrumental}
    if "song_prompt_check" in tools:
        # Check when supported; studio may refuse prompts that exceed its limits.
        _call(tools, "song_prompt_check", args)
    if on_progress:
        on_progress("MiniMax Studio creating song: " + title)
    created = _call(tools, "song_create", args, timeout=120)
    job_id = _find(created, "job_id", "jobId")
    song_id = _find(created, "song_id", "songId")
    if not job_id and not song_id:
        raise RuntimeError("Studio did not return a song or job ID: " + str(created)[:500])
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    completed = not bool(job_id)
    while job_id and time.monotonic() < deadline:
        time.sleep(8)
        job = _call(tools, "song_job_get", {"job_id": job_id, "id": job_id})
        state = _state(job)
        if on_progress:
            on_progress("MiniMax Music 3: " + (state or "generating") +
                        " — " + title)
        if state in ("error", "failed", "cancelled", "canceled"):
            raise RuntimeError("MiniMax generation failed: " + str(job)[:600])
        if state in ("done", "completed", "complete", "succeeded", "success", "finished"):
            completed = True
            song_id = _find(job, "song_id", "songId") or song_id
            if not song_id:
                ids = _find(job, "song_ids", "songIds")
                if isinstance(ids, list) and ids:
                    song_id = ids[0]
            break
    if job_id and not completed:
        raise TimeoutError("MiniMax generation is still running. Studio job ID: " + str(job_id))
    if not song_id:
        raise RuntimeError("MiniMax job completed without a song ID: " + str(job)[:500])
    source = _download_result_file(tools, str(song_id))
    target = Path(target)
    if source.suffix.lower() == ".mp3":
        shutil.copy2(source, target)
    else:
        if not shutil.which("ffmpeg"):
            raise RuntimeError("FFmpeg is needed to import MiniMax FLAC/WAV outputs.")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(source), "-codec:a", "libmp3lame", "-q:a", "2",
                        str(target)], check=True, timeout=180)
    if on_progress:
        on_progress("MiniMax song saved to Music Channel Automation Library")
