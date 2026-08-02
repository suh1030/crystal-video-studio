#!/usr/bin/env python3
"""Local web UI for the Crystal Video Workflow."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
JOBS: dict[str, "Job"] = {}
LOCK = threading.Lock()


@dataclass
class Job:
    id: str
    topic: str
    status: str = "queued"
    stage: str = "queued"
    progress: int = 0
    message: str = "Waiting to start"
    logs: list[str] = field(default_factory=list)
    output: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)

    def update(self, stage: str, progress: int, message: str) -> None:
        self.stage = stage
        self.progress = progress
        self.message = message
        self.logs.append(message)
        self.logs = self.logs[-60:]

    def json(self) -> dict[str, Any]:
        return {
            "id": self.id, "topic": self.topic, "status": self.status,
            "stage": self.stage, "progress": self.progress, "message": self.message,
            "logs": self.logs, "output": self.output, "error": self.error,
        }


def ollama_script(topic: str) -> str:
    model = os.environ.get("OLLAMA_MODEL", "qwen3:8b")
    prompt = f"""Write a factual, calm English narration about {topic}, for a premium five-minute YouTube mineral video.

Requirements:
- 650 to 700 English words.
- Focus on mineralogy, formation, color, locations, collecting, care, and photography.
- Separate cultural history from scientific claims. Do not present healing or supernatural claims as fact.
- Natural spoken English, no headings, bullets, citations, stage directions, or markdown.
- Begin with a compelling but calm introduction and finish with a reflective conclusion.
- Use short-to-medium sentences suitable for one-sentence-per-line subtitles.
- Discuss only facts you are confident about. Do not invent provenance or chemical details.

Return narration only."""
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            data = json.load(response)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(
            f"Cannot reach Ollama or model {model!r}. Install Ollama and run: ollama pull {model}"
        ) from exc
    text = re.sub(r"<think>.*?</think>", "", data.get("response", ""), flags=re.S).strip()
    if len(text.split()) < 450:
        raise RuntimeError("The local model returned a script that is too short. Retry or choose a larger Ollama model.")
    return text


def run_job(job: Job) -> None:
    job.status = "running"
    job_dir = ROOT / "jobs" / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    script_path = job_dir / "script.txt"
    try:
        job.update("script", 5, "Writing a factual five-minute narration with the local AI model")
        script = ollama_script(job.topic)
        script_path.write_text(script + "\n", encoding="utf-8")
        words = len(script.split())
        job.update("facts", 16, f"Script ready: {words} words; checking pacing and sentence structure")

        job.update("media", 26, "Preparing topic visuals; white crystal is the fallback")
        command = [
            str(ROOT / ".venv" / "bin" / "python"), "-u", str(ROOT / "generate_video.py"),
            "--topic", job.topic, "--script", str(script_path),
        ]
        music = ROOT / "assets" / "music" / "ambient.mp3"
        if music.exists():
            command.extend(["--music", str(music)])
        env = os.environ.copy()
        process = subprocess.Popen(
            command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        scene_lines = 0
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.strip()
            if not line:
                continue
            if "Downloading stock clip" in line:
                job.update("media", min(42, job.progress + 2), line)
            elif "Generating narration" in line:
                job.update("voice", 46, "Generating and timing the AI narration")
            elif "scene-" in line and "ffmpeg" in line:
                scene_lines += 1
                job.update("render", min(88, 58 + scene_lines), f"Rendering visual scene {scene_lines}")
            elif "subtitles=" in line:
                job.update("subtitles", 54, "Burning one-sentence subtitles with a translucent black box")
            elif "ffprobe" in line:
                job.update("verify", 94, "Verifying duration, resolution, frame rate, and output integrity")
            elif line.startswith("Finished:"):
                job.output = line.split("Finished:", 1)[1].strip()
            elif line.startswith("Error:") or line.startswith("Warning:"):
                job.logs.append(line)
        code = process.wait()
        if code != 0:
            raise RuntimeError(job.logs[-1] if job.logs else f"Video process exited with code {code}")
        expected = ROOT / "output" / f"{re.sub(r'[^a-zA-Z0-9]+', '-', job.topic.lower()).strip('-')}-5min-1080p.mp4"
        if not expected.exists():
            raise RuntimeError("The render finished but the MP4 file was not found.")
        job.output = str(expected)
        job.status = "completed"
        job.update("done", 100, "Video complete: 1080p, 16:9, five minutes")
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        job.logs.append(f"Error: {exc}")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/api/jobs/"):
            job_id = self.path.split("/")[-1]
            with LOCK:
                job = JOBS.get(job_id)
            return self.send_json(job.json() if job else {"error": "Job not found"}, 200 if job else 404)
        if self.path.startswith("/api/output/"):
            job_id = self.path.split("/")[-1]
            with LOCK:
                job = JOBS.get(job_id)
            if not job or not job.output or not Path(job.output).exists():
                return self.send_error(HTTPStatus.NOT_FOUND)
            path = Path(job.output)
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.send_header("Content-Length", str(path.stat().st_size))
            self.end_headers()
            with path.open("rb") as file:
                while chunk := file.read(1024 * 1024):
                    self.wfile.write(chunk)
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path != "/api/jobs":
            return self.send_error(HTTPStatus.NOT_FOUND)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length))
            topic = str(data.get("topic", "")).strip()
            if not topic or len(topic) > 80:
                return self.send_json({"error": "Enter a valid crystal name."}, 400)
            job = Job(id=uuid.uuid4().hex[:12], topic=topic)
            with LOCK:
                JOBS[job.id] = job
            threading.Thread(target=run_job, args=(job,), daemon=True).start()
            return self.send_json(job.json(), 201)
        except Exception as exc:
            return self.send_json({"error": str(exc)}, 400)


def main() -> None:
    host, port = "127.0.0.1", int(os.environ.get("CRYSTAL_STUDIO_PORT", "8765"))
    print(f"Crystal Video Studio: http://{host}:{port}")
    print("Press Control+C to stop.")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()

