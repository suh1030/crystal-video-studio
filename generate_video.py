#!/usr/bin/env python3
"""Create a five-minute narrated YouTube video using free/local components."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import textwrap
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def run(cmd: list[str], *, capture: bool = False) -> str:
    print("  $", " ".join(str(x) for x in cmd))
    result = subprocess.run(cmd, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ""


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return value or "video"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def ffprobe_duration(path: Path) -> float:
    output = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ], capture=True)
    return float(output)


def ffprobe_video_duration(path: Path) -> float:
    output = run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ], capture=True)
    return float(output)


def valid_media(path: Path, expected_duration: float | None = None) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        duration = ffprobe_duration(path)
    except (subprocess.CalledProcessError, ValueError):
        return False
    return expected_duration is None or abs(duration - expected_duration) < 0.25


def sentences(text: str) -> list[str]:
    clean = re.sub(r"\s+", " ", text.strip())
    parts = re.split(r"(?<=[.!?。！？])\s+", clean)
    return [part.strip() for part in parts if part.strip()]


def timestamp(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def build_srt(text: str, audio_duration: float, path: Path) -> None:
    chunks = sentences(text)
    weights = [max(1, len(re.findall(r"\w+", chunk))) for chunk in chunks]
    total = sum(weights)
    cursor = 0.0
    blocks: list[str] = []
    for index, (chunk, weight) in enumerate(zip(chunks, weights), 1):
        duration = audio_duration * weight / total
        end = min(audio_duration, cursor + duration)
        blocks.append(f"{index}\n{timestamp(cursor)} --> {timestamp(end)}\n{chunk}\n")
        cursor = end
    path.write_text("\n".join(blocks), encoding="utf-8")


async def make_edge_tts(text: str, output: Path, voice: dict) -> None:
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("edge-tts is missing. Run: pip install -r requirements.txt") from exc
    communicator = edge_tts.Communicate(
        text,
        voice=voice["name"],
        rate=str(voice.get("rate", "+0%")),
        volume=str(voice.get("volume", "+0%")),
    )
    await communicator.save(str(output))


def pexels_json(url: str, api_key: str) -> dict:
    request = urllib.request.Request(url, headers={"Authorization": api_key, "User-Agent": "CrystalVideoWorkflow/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "CrystalVideoWorkflow/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response, destination.open("wb") as file:
        shutil.copyfileobj(response, file)


def fetch_pexels(topic: str, cfg: dict, destination: Path, api_key: str) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    limit = int(cfg.get("max_downloads", 18))
    terms = [term.format(topic=topic) for term in cfg.get("search_terms", ["{topic} crystal"])]
    candidates: list[tuple[str, str]] = []
    for term in terms:
        query = urllib.parse.quote(term)
        try:
            data = pexels_json(f"https://api.pexels.com/v1/videos/search?query={query}&per_page=12&orientation=landscape", api_key)
        except Exception as exc:
            print(f"Warning: Pexels search failed for {term!r}: {exc}", file=sys.stderr)
            continue
        for video in data.get("videos", []):
            files = sorted(video.get("video_files", []), key=lambda item: item.get("width") or 0, reverse=True)
            usable = [item for item in files if item.get("file_type") == "video/mp4" and (item.get("width") or 0) >= 1280]
            if usable:
                candidates.append((str(video["id"]), usable[0]["link"]))
    seen: set[str] = set()
    paths: list[Path] = []
    for media_id, url in candidates:
        if media_id in seen or len(paths) >= limit:
            continue
        seen.add(media_id)
        path = destination / f"pexels-{media_id}.mp4"
        if not path.exists():
            print(f"Downloading stock clip {len(paths) + 1}/{limit}...")
            try:
                download(url, path)
            except Exception as exc:
                print(f"Warning: download failed: {exc}", file=sys.stderr)
                path.unlink(missing_ok=True)
                continue
        paths.append(path)
    return paths


def local_media(topic: str) -> list[Path]:
    folder = ROOT / "assets" / slugify(topic)
    if not folder.exists():
        return []
    return sorted(path for path in folder.rglob("*") if path.suffix.lower() in VIDEO_EXTENSIONS | IMAGE_EXTENSIONS)


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def make_placeholder(topic: str, path: Path, width: int, height: int, color: str) -> None:
    image = Image.new("RGB", (width, height), color)
    draw = ImageDraw.Draw(image)
    for radius, shade in [(440, "#382657"), (310, "#68458c"), (180, "#b48ad1")]:
        x, y = width // 2, height // 2
        draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=shade)
    title = topic.upper()
    title_font = font(86)
    subtitle_font = font(30)
    box = draw.textbbox((0, 0), title, font=title_font)
    draw.text(((width - (box[2]-box[0]))/2, height/2-62), title, font=title_font, fill="white")
    subtitle = "A MINERAL STORY"
    box = draw.textbbox((0, 0), subtitle, font=subtitle_font)
    draw.text(((width - (box[2]-box[0]))/2, height/2+52), subtitle, font=subtitle_font, fill="#eee7f4")
    image.save(path, quality=94)


def normalized_clip(source: Path, output: Path, duration: float, cfg: dict, index: int) -> None:
    width, height, fps = int(cfg["width"]), int(cfg["height"]), int(cfg["fps"])
    fade = min(0.6, duration / 4)
    common_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps={fps},"
        f"fade=t=in:st=0:d={fade},fade=t=out:st={max(0, duration-fade)}:d={fade},format=yuv420p"
    )
    if source.suffix.lower() in IMAGE_EXTENSIONS:
        direction = 1 if index % 2 == 0 else -1
        zoom = f"min(zoom+0.00035,1.10)" if direction > 0 else "if(eq(on,1),1.10,max(zoom-0.00035,1.0))"
        vf = (
            f"scale={width*2}:{height*2}:force_original_aspect_ratio=increase,"
            f"crop={width*2}:{height*2},zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':d={math.ceil(duration*fps)}:s={width}x{height}:fps={fps},"
            f"fade=t=in:st=0:d={fade},fade=t=out:st={max(0, duration-fade)}:d={fade},format=yuv420p"
        )
        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(source), "-t", str(duration), "-vf", vf,
               "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(output)]
    else:
        cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(source), "-t", str(duration), "-vf", common_filter,
               "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(output)]
    run(cmd)


def subtitle_filter(srt: Path, cfg: dict) -> str:
    sub = cfg["subtitles"]
    escaped = str(srt).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    style = (
        f"FontName={sub.get('font_name', 'Arial')},FontSize={sub.get('font_size', 18)},"
        "PrimaryColour=&H00FFFFFF,BackColour=&H99000000,BorderStyle=3,Outline=0,Shadow=0,"
        f"MarginV={sub.get('bottom_margin', 56)},Alignment=2"
    )
    return f"subtitles='{escaped}':force_style='{style}'"


def make_video(topic: str, script_path: Path, config_path: Path, music: Path | None, demo: bool) -> Path:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    video_cfg = cfg["video"]
    duration = float(video_cfg.get("duration_seconds", 300))
    scene_seconds = float(video_cfg.get("scene_seconds", 10))
    script = script_path.read_text(encoding="utf-8").strip()
    if not script:
        raise ValueError("Script is empty.")

    slug = slugify(topic)
    work = ROOT / "work" / slug
    output_dir = ROOT / "output"
    clips_dir = work / "clips"
    media_dir = work / "stock"
    clips_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    narration = work / "narration.mp3"
    if demo:
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(duration), str(narration)])
        audio_duration = duration
    else:
        print("Generating narration...")
        try:
            asyncio.run(make_edge_tts(script, narration, cfg["voice"]))
        except Exception as exc:
            raise RuntimeError(
                "Voice generation failed. Check the internet connection, then retry; "
                "the offline --demo mode can be used to test video rendering."
            ) from exc
        audio_duration = ffprobe_duration(narration)
        if audio_duration > duration - 2:
            raise ValueError(
                f"Narration is {audio_duration:.1f}s, longer than the safe {duration-2:.0f}s limit. "
                "Shorten the script or increase voice.rate."
            )
    srt = output_dir / f"{slug}.srt"
    build_srt(script, min(audio_duration, duration), srt)

    media = local_media(topic)
    api_key = os.environ.get("PEXELS_API_KEY", "").strip()
    if cfg["media"].get("use_pexels") and api_key and not demo:
        media.extend(fetch_pexels(topic, cfg["media"], media_dir, api_key))
    media = list(dict.fromkeys(media))
    if not media:
        placeholder = work / "placeholder.jpg"
        make_placeholder(topic, placeholder, int(video_cfg["width"]), int(video_cfg["height"]), video_cfg["background_color"])
        media = [placeholder]
        print("No media found; using a generated placeholder. Add media under assets/<topic-slug>/ for production.")

    scene_count = math.ceil(duration / scene_seconds)
    normalized: list[Path] = []
    for index in range(scene_count):
        scene_duration = min(scene_seconds, duration - index * scene_seconds)
        output = clips_dir / f"scene-{index:03}.mp4"
        if not valid_media(output, scene_duration):
            for attempt in range(2):
                output.unlink(missing_ok=True)
                normalized_clip(media[index % len(media)], output, scene_duration, video_cfg, index)
                if valid_media(output, scene_duration):
                    break
            else:
                raise RuntimeError(f"Scene rendering failed validation twice: {output.name}")
        normalized.append(output)

    concat = work / "concat.txt"
    concat.write_text("".join(f"file '{path.as_posix()}'\n" for path in normalized), encoding="utf-8")
    silent_video = work / "silent.mp4"
    run(["ffmpeg", "-y", "-fflags", "+genpts", "-f", "concat", "-safe", "0", "-i", str(concat),
         "-t", str(duration), "-c", "copy", "-avoid_negative_ts", "make_zero", str(silent_video)])

    output = output_dir / f"{slug}-5min-1080p.mp4"
    cmd = ["ffmpeg", "-y", "-i", str(silent_video), "-i", str(narration)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        filter_complex = (
            f"[1:a]volume={cfg['audio'].get('narration_volume', 1.0)}[voice];"
            f"[2:a]volume={cfg['audio'].get('music_volume', 0.10)},atrim=0:{duration}[music];"
            "[voice][music]amix=inputs=2:duration=longest:dropout_transition=2[a]"
        )
        cmd += ["-filter_complex", filter_complex, "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
    if cfg["subtitles"].get("enabled") and cfg["subtitles"].get("burn_in"):
        cmd += ["-vf", subtitle_filter(srt.resolve(), cfg)]
    cmd += ["-t", str(duration), "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output)]
    run(cmd)

    rendered_duration = ffprobe_video_duration(output)
    if abs(rendered_duration - duration) > 0.5:
        output.unlink(missing_ok=True)
        raise RuntimeError(
            f"Final video validation failed: video track is {rendered_duration:.1f}s, expected {duration:.1f}s."
        )

    (output_dir / f"{slug}-script.txt").write_text(script + "\n", encoding="utf-8")
    metadata = {
        "topic": topic,
        "duration_seconds": round(ffprobe_duration(output), 3),
        "video_track_seconds": round(rendered_duration, 3),
        "resolution": f"{video_cfg['width']}x{video_cfg['height']}",
        "source_media_count": len(media),
        "video": output.name,
        "subtitles": srt.name,
    }
    (output_dir / f"{slug}-metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"\nFinished: {output}")
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a five-minute 1080p narrated crystal video.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              python generate_video.py --topic Amethyst --script sample/amethyst-script.txt
              python generate_video.py --topic Amethyst --script sample/amethyst-script.txt --music assets/music/ambient.mp3
        """),
    )
    parser.add_argument("--topic", required=True)
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--music", type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--demo", action="store_true", help="Use silent audio to test video rendering without online TTS")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(ROOT / ".env")
    for binary in ("ffmpeg", "ffprobe"):
        if not shutil.which(binary):
            print(f"Missing required program: {binary}", file=sys.stderr)
            return 2
    try:
        make_video(args.topic, args.script.resolve(), args.config.resolve(), args.music.resolve() if args.music else None, args.demo)
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
