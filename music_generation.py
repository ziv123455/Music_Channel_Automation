"""Resilient local ACE-Step 1.5 asynchronous REST client."""
import json
import os
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

DEFAULT_URL = "http://127.0.0.1:8001"
POLL_INTERVAL_SECONDS = 5
MAX_POLL_SECONDS = 3600
RECOVERABLE = (requests.exceptions.ConnectionError, requests.exceptions.Timeout)

def base_url():
    url = os.getenv("ACESTEP_API_URL", DEFAULT_URL).rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("ACESTEP_API_URL must point to a local ACE-Step server.")
    return url

def session():
    client = requests.Session()
    client.headers["Connection"] = "close"
    token = os.getenv("ACESTEP_API_KEY", "")
    if token:
        client.headers["Authorization"] = "Bearer " + token
    return client

def check_connection():
    with session() as client:
        response = client.get(base_url() + "/health", timeout=8)
        response.raise_for_status()
        return response.json()

def _post_json(url, payload, timeout=30):
    # A fresh connection per request avoids stale keep-alive sockets after model loading.
    with session() as client:
        response = client.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        result = response.json()
        if result.get("code", 200) != 200:
            raise RuntimeError("ACE-Step error: " + str(result.get("error") or result)[:300])
        return result

def _fetch_audio(url, output):
    temporary = Path(str(output) + ".part")
    for attempt in range(5):
        try:
            with session() as client:
                with client.get(url, stream=True, timeout=(10, 180)) as response:
                    response.raise_for_status()
                    with open(temporary, "wb") as stream:
                        for chunk in response.iter_content(1024 * 256):
                            if chunk:
                                stream.write(chunk)
            if temporary.stat().st_size < 1024:
                raise RuntimeError("ACE-Step returned an empty audio file.")
            temporary.replace(output)
            return
        except RECOVERABLE:
            temporary.unlink(missing_ok=True)
            if attempt == 4:
                raise RuntimeError("Audio download failed after 5 connection attempts.")
            time.sleep(min(5 * (attempt + 1), 20))
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

def _post_audio_task(url, payload, audio_source, mode, timeout=120):
    """Upload a real local MP3 to ACE-Step in multipart format."""
    audio_source = Path(audio_source)
    if not audio_source.is_file():
        raise FileNotFoundError("Reference audio file is missing")
    if mode not in ("style", "cover"):
        raise ValueError("Unknown audio-reference mode")
    fields = {key: ("true" if val is True else "false" if val is False else str(val))
              for key, val in payload.items()}
    fields["task_type"] = "cover" if mode == "cover" else "text2music"
    file_field = "src_audio" if mode == "cover" else "reference_audio"
    mime = "audio/mpeg" if audio_source.suffix.lower() == ".mp3" else "audio/wav"
    with audio_source.open("rb") as stream, session() as client:
        response = client.post(url, data=fields,
            files={file_field: (audio_source.name, stream, mime)}, timeout=(15, timeout))
        response.raise_for_status()
        result = response.json()
    if result.get("code", 200) != 200:
        raise RuntimeError("ACE-Step audio reference failed: " +
                           str(result.get("error") or result)[:350])
    return result


def generate_one(description, duration, instrumental, path, on_progress=None,
                 reference_audio=None, reference_mode="style", cover_strength=0.65):
    """Submit once, keep its task ID, recover connection loss during polling."""
    base = base_url()
    payload = {
        "prompt": description,
        "audio_duration": duration,
        "instrumental": instrumental,
        "lyrics": "[Instrumental]" if instrumental else "",
        "thinking": True,
        "audio_format": "mp3",
        "batch_size": 1,
    }
    if reference_audio is not None:
        if reference_mode not in ("style", "cover"):
            raise ValueError("Reference mode must be style or cover")
        if not 0.1 <= float(cover_strength) <= 1.0:
            raise ValueError("Cover strength must be between 0.1 and 1")
        if reference_mode == "cover":
            payload["audio_cover_strength"] = float(cover_strength)
            payload["instruction"] = "Reimagine this audio with the specified style and instruments"
    try:
        body = (_post_audio_task(base + "/release_task", payload, reference_audio, reference_mode)
                if reference_audio is not None else
                _post_json(base + "/release_task", payload, timeout=60))
    except RECOVERABLE as error:
        raise RuntimeError(
            "Connection lost while submitting the generation task. "
            "ACE-Step may still be processing it; do not submit again until checking its server logs. "
            + str(error)
        ) from error
    data = body.get("data") or {}
    task_id = data.get("task_id") if isinstance(data, dict) else None
    if not task_id:
        raise RuntimeError("ACE-Step did not return a task ID: " + str(body)[:300])
    if on_progress:
        on_progress("ACE-Step task submitted: " + str(task_id))
    deadline = time.monotonic() + MAX_POLL_SECONDS
    connection_errors = 0
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        try:
            result_body = _post_json(base + "/query_result", {"task_id_list": [task_id]}, timeout=30)
            connection_errors = 0
        except RECOVERABLE as error:
            connection_errors += 1
            if on_progress:
                on_progress("ACE-Step connection interrupted; recovering task " + str(task_id)
                            + " (attempt " + str(connection_errors) + "/20)")
            if connection_errors >= 20:
                raise RuntimeError(
                    "ACE-Step connection remained unavailable. Existing task ID: "
                    + str(task_id) + ". Inspect/restart the ACE-Step process; do not regenerate blindly."
                ) from error
            time.sleep(min(3 * connection_errors, 15))
            continue
        results = result_body.get("data") or []
        entry = next((r for r in results if isinstance(r, dict) and r.get("task_id") == task_id), None)
        if entry is None:
            if on_progress:
                on_progress("Waiting for ACE-Step task " + str(task_id))
            continue
        state = int(entry.get("status", 0))
        if state == 2:
            raise RuntimeError("ACE-Step task " + str(task_id) + " failed: "
                               + str(entry.get("error") or entry.get("result") or "Unknown error")[:300])
        if state != 1:
            if on_progress:
                on_progress("Generating music (task " + str(task_id) + ")")
            continue
        details = entry.get("result") or "[]"
        files = json.loads(details) if isinstance(details, str) else details
        if not isinstance(files, list) or not files or not files[0].get("file"):
            raise RuntimeError("ACE-Step task " + str(task_id) + " returned no audio.")
        audio_url = urljoin(base + "/", files[0]["file"])
        target = urlparse(audio_url)
        source = urlparse(base)
        if (target.scheme, target.hostname, target.port) != (source.scheme, source.hostname, source.port):
            raise RuntimeError("Unsafe audio URL returned by ACE-Step.")
        if on_progress:
            on_progress("Downloading completed audio for task " + str(task_id))
        _fetch_audio(audio_url, path)
        return
    raise TimeoutError("Timed out waiting for ACE-Step task " + str(task_id))
