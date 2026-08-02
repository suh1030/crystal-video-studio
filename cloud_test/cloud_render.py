#!/usr/bin/env python3
import argparse, json, math, re, subprocess, sys, urllib.parse
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "cloud_build"
USER_AGENT = "CrystalVideoStudio/1.1 (https://github.com/suh1030/crystal-video-studio)"

def run(*args):
    subprocess.run([str(x) for x in args], check=True)

def download_asset(scene, target):
    filename = scene["asset_filename"]
    url = "https://commons.wikimedia.org/wiki/Special:Redirect/file/" + urllib.parse.quote(filename) + "?width=1920"
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        raise RuntimeError(f"Not an image: {filename} ({content_type})")
    target.write_bytes(response.content)
    with Image.open(target) as im:
        im.verify()
    with Image.open(target) as im:
        if im.width < 700 or im.height < 450:
            raise RuntimeError(f"Image too small: {filename} ({im.width}x{im.height})")
    print(f"Downloaded real asset: {filename}")

def ass_time(value):
    centiseconds = round(value * 100)
    h, centiseconds = divmod(centiseconds, 360000); m, centiseconds = divmod(centiseconds, 6000)
    s, centiseconds = divmod(centiseconds, 100)
    return f"{h}:{m:02d}:{s:02d}.{centiseconds:02d}"

def make_subtitles(text, duration, path):
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    cues = [sentence.strip() for sentence in sentences if sentence.strip()]
    weights = [max(1, len(c.split())) for c in cues]
    total = sum(weights); now = 0.0
    rows = ["""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sentence,DejaVu Sans,12,&H00FFFFFF,&H00FFFFFF,&H00000000,&H70000000,0,0,0,0,100,100,0,0,3,0,0,2,24,24,44,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""]
    for cue, weight in zip(cues, weights):
        end = now + duration * weight / total
        safe_cue = cue.replace("{", "(").replace("}", ")").replace("\n", " ")
        rows.append(f"Dialogue: 0,{ass_time(now)},{ass_time(end)},Sentence,,0,0,0,,{safe_cue}\n")
        now = end
    path.write_text("".join(rows))

def main(job_path, preview=False):
    job = json.loads(Path(job_path).read_text())
    BUILD.mkdir(exist_ok=True)
    scenes = job["scenes"][:3] if preview else job["scenes"]
    if preview:
        scenes = [{**s, "narration": re.split(r"(?<=[.!?])\s+", s["narration"])[0]} for s in scenes]
    script = "\n\n".join(s["narration"] for s in scenes)
    (BUILD / "narration.txt").write_text(script)
    run(sys.executable, "-m", "edge_tts", "--voice", job.get("voice", "en-US-AriaNeural"),
        "--rate=+5%", "--file", BUILD / "narration.txt", "--write-media", BUILD / "narration.mp3")
    duration = float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", BUILD / "narration.mp3"
    ], text=True).strip())
    make_subtitles(script, duration, BUILD / "subtitles.ass")
    scene_len = duration / len(scenes); segments = []
    for i, scene in enumerate(scenes):
        image = BUILD / f"image-{i:02d}.jpg"
        download_asset(scene, image)
        segment = BUILD / f"segment-{i:02d}.mp4"
        frames = max(1, math.ceil(scene_len * 30))
        run("ffmpeg", "-y", "-loop", "1", "-i", image, "-vf",
            f"scale=2000:1125:force_original_aspect_ratio=increase,crop=2000:1125,zoompan=z='min(zoom+0.00035,1.08)':d={frames}:s=1920x1080:fps=30,format=yuv420p",
            "-t", f"{scene_len:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", segment)
        segments.append(segment)
    concat = BUILD / "concat.txt"
    concat.write_text("".join(f"file '{p.name}'\n" for p in segments))
    silent = BUILD / "silent.mp4"
    run("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat, "-c", "copy", silent)
    output = ROOT / ("amethyst-visual-preview.mp4" if preview else "amethyst-five-minute.mp4")
    subtitle_filter = "subtitles=cloud_build/subtitles.ass"
    run("ffmpeg", "-y", "-i", silent, "-i", BUILD / "narration.mp3", "-vf", subtitle_filter,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", output)
    print(f"Created {output} ({duration:.1f} seconds)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("job"); parser.add_argument("--preview", action="store_true")
    args = parser.parse_args(); main(args.job, args.preview)
