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

def download_music(music, target):
    filename = music["asset_filename"]
    url = "https://commons.wikimedia.org/wiki/Special:Redirect/file/" + urllib.parse.quote(filename)
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if not (content_type.startswith("audio/") or content_type in {"application/ogg", "application/octet-stream"}):
        raise RuntimeError(f"Not an audio file: {filename} ({content_type})")
    target.write_bytes(response.content)
    if target.stat().st_size < 100_000:
        raise RuntimeError(f"Music file too small: {filename}")
    print(f"Downloaded background music: {filename}")

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
Style: Sentence,DejaVu Sans,30,&H00FFFFFF,&H00FFFFFF,&H78000000,&H78000000,0,0,0,0,100,100,0,0,3,8,0,2,60,60,48,1

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
    scenes = job["scenes"][:2] if preview else job["scenes"]
    groups = job.get("visual_groups")
    if preview and groups:
        groups = groups[:2]
    visuals = ([asset for group in groups for asset in group["assets"]]
               if groups else job.get("visuals", job["scenes"]))
    script = "\n\n".join(s["narration"] for s in scenes)
    (BUILD / "narration.txt").write_text(script)
    run(sys.executable, "-m", "edge_tts", "--voice", job.get("voice", "en-US-AriaNeural"),
        "--rate=+12%", "--file", BUILD / "narration.txt", "--write-media", BUILD / "narration.mp3")
    duration = float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", BUILD / "narration.mp3"
    ], text=True).strip())
    make_subtitles(script, duration, BUILD / "subtitles.ass")
    target_visual_seconds = float(job.get("visual_seconds", 6.0))
    if preview:
        preview_count = max(1, math.ceil(duration / target_visual_seconds))
        visuals = visuals[:preview_count]
    final_segment_seconds = duration - target_visual_seconds * (len(visuals) - 1)
    if final_segment_seconds <= 0 or final_segment_seconds > target_visual_seconds * 1.5:
        raise RuntimeError(
            f"Visual count does not fit narration: {len(visuals)} images for {duration:.1f}s at {target_visual_seconds:.1f}s"
        )
    segment_lengths = [target_visual_seconds] * (len(visuals) - 1) + [final_segment_seconds]
    segments = []
    for i, (scene, scene_len) in enumerate(zip(visuals, segment_lengths)):
        image = BUILD / f"image-{i:02d}.jpg"
        download_asset(scene, image)
        segment = BUILD / f"segment-{i:02d}.mp4"
        frames = max(2, math.ceil(scene_len * 30))
        run("ffmpeg", "-y", "-loop", "1", "-i", image, "-vf",
            f"scale=2000:1125:force_original_aspect_ratio=increase,crop=2000:1125,zoompan=z='1+0.08*on/{frames - 1}':d={frames}:s=1920x1080:fps=30,format=yuv420p",
            "-t", f"{scene_len:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", segment)
        segments.append(segment)
    concat = BUILD / "concat.txt"
    concat.write_text("".join(f"file '{p.name}'\n" for p in segments))
    silent = BUILD / "silent.mp4"
    run("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat, "-c", "copy", silent)
    raw_music = BUILD / "background-music.ogg"
    download_music(job["music"], raw_music)
    music = BUILD / "background-music.m4a"
    run("ffmpeg", "-y", "-stream_loop", "-1", "-i", raw_music, "-t", f"{duration:.3f}", "-af",
        f"afade=t=in:st=0:d=4,afade=t=out:st={max(0, duration - 5):.3f}:d=5",
        "-c:a", "aac", "-b:a", "160k", music)
    output = ROOT / ("amethyst-visual-preview.mp4" if preview else "amethyst-five-minute.mp4")
    subtitle_filter = "subtitles=cloud_build/subtitles.ass"
    run("ffmpeg", "-y", "-i", silent, "-i", BUILD / "narration.mp3", "-i", music,
        "-filter_complex", f"[0:v]{subtitle_filter}[v];"
        "[2:a]volume=0.24[bg];[bg][1:a]sidechaincompress=threshold=0.025:ratio=8:attack=20:release=500[ducked];"
        "[1:a][ducked]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", output)
    print(f"Created {output} ({duration:.1f} seconds)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("job"); parser.add_argument("--preview", action="store_true")
    args = parser.parse_args(); main(args.job, args.preview)
