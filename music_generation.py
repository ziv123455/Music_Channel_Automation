"""Local ACE-Step 1.5 native asynchronous API integration."""
import json
import os
import time
from urllib.parse import urljoin, urlparse
import requests

DEFAULT_URL = "http://127.0.0.1:8001"

def base_url():
    url = os.getenv("ACESTEP_API_URL", DEFAULT_URL).rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("ACESTEP_API_URL must point to a local ACE-Step server.")
    return url

def session():
    client = requests.Session()
    token = os.getenv("ACESTEP_API_KEY", "")
    if token:
        client.headers["Authorization"] = "Bearer " + token
    return client

def check_connection():
    with session() as client:
        response = client.get(base_url() + "/health", timeout=8)
        response.raise_for_status()
        return response.json()

def generate_one(description, duration, instrumental, path, on_progress=None):
    """Submit one task, poll it and download the first audio result."""
    base = base_url()
    payload = {
        "prompt": description,
        "audio_duration": duration,
        "instrumental": instrumental,
        "lyrics": "[Instrumental]" if instrumental else "",
        "thinking": True,
        "audio_format": "mp3",
    }
    with session() as client:
        response = client.post(base + "/release_task", json=payload, timeout=60)
        response.raise_for_status()
        body = response.json()
        data = body.get("data") or {}
        task_id = data.get("task_id") if isinstance(data, dict) else None
        if not task_id:
            raise RuntimeError(f"ACE-Step did not return task_id: {str(body)[:300]}")
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            if on_progress:
                on_progress("Generating music on ACE-Step")
            time.sleep(5)
            result_response = client.post(base + "/query_result", json={"task_id_list": [task_id]}, timeout=30)
            result_response.raise_for_status()
            result_body = result_response.json()
            results = result_body.get("data") or []
            entry = results[0] if results else None
            if not isinstance(entry, dict):
                continue
            state = int(entry.get("status", 0))
            if state == 2:
                raise RuntimeError("ACE-Step generation failed: " + str(entry.get("error") or entry.get("result") or "Unknown reason")[:300])
            if state != 1:
                continue
            details = entry.get("result") or "[]"
            files = json.loads(details) if isinstance(details, str) else details
            if not isinstance(files, list) or not files or not files[0].get("file"):
                raise RuntimeError("ACE-Step completed without an audio file.")
            relative = files[0]["file"]
            audio_url = urljoin(base + "/", relative)
            parsed_url = urlparse(audio_url)
            parsed_base = urlparse(base)
            if (parsed_url.hostname, parsed_url.port) != (parsed_base.hostname, parsed_base.port):
                raise RuntimeError("ACE-Step returned an unsafe audio URL.")
            with client.get(audio_url, stream=True, timeout=120) as download:
                download.raise_for_status()
                with open(path, "wb") as output:
                    for chunk in download.iter_content(1024 * 256):
                        if chunk:
                            output.write(chunk)
            if path.stat().st_size < 1024:
                raise RuntimeError("Downloaded audio is empty or invalid.")
            return
        raise TimeoutError("ACE-Step generation exceeded one hour.")
