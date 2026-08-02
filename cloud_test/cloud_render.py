#!/usr/bin/env python3
import json, math, subprocess, sys, urllib.parse
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "cloud_build"

def run(*args):
    subprocess.run([str(x) for x in args], check=True)

def commons_image(query, target, seen):
    params = {
        "action": "query", "generator": "search", "gsrsearch": query,
        "gsrnamespace": 6, "gsrlimit": 20, "prop": "imageinfo",
        "iiprop": "url|mime", "iiurlwidth": 1920, "format": "json",
    }
    data = requests.get("https://commons.wikimedia.org/w/api.php", params=params,
                        headers={"User-Agent": "CrystalVideoStudio/1.0"}, timeout=30).json()
    for page in data.get("query", {}).get("pages", {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("thumburl") or info.get("url")
        if not url or url in seen or info.get("mime") not in {"image/jpeg", "image/png"}:
            continue
        content = requests.get(url, headers={"User-Agent": "CrystalVideoStudio/1.0"}, timeout=45).content
        target.write_bytes(content)
        try:
            with Image.open(target) as im:
                if im.width >= 700 and im.height >= 500:
                    seen.add(url); return True
        except Exception:
            pass
    return False

def fallback_card(target, title, subtitle):
    im = Image.new("RGB", (1920, 1080), "#0d0912")
    d = ImageDraw.Draw(im)
    for r in range(720, 40, -8):
        c = (35 + r // 20, 16 + r // 35, 52 + r // 16)
        d.ellipse((960-r, 540-r, 960+r, 540+r), fill=c)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", 92)
    small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 34)
    d.text((110, 760), title, font=font, fill="white")
    d.text((116, 880), subtitle, font=small, fill="#d4bddf")
    im.save(target, quality=94)

def main(job_path):
    job = json.loads(Path(job_path).read_text())
    BUILD.mkdir(exist_ok=True)
    script = "\n\n".join(s["narration"] for s in job["scenes"])
    (BUILD / "narration.txt").write_text(script)
    run(sys.executable, "-m", "edge_tts", "--voice", job.get("voice", "en-US-AriaNeural"),
        "--rate=-8%", "--file", BUILD / "narration.txt", "--write-media", BUILD / "narration.mp3",
        "--write-subtitles", BUILD / "subtitles.srt")
    duration = float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", BUILD / "narration.mp3"
    ], text=True).strip())
    scene_len = duration / len(job["scenes"])
    seen = set(); segments = []
    for i, scene in enumerate(job["scenes"]):
        image = BUILD / f"image-{i:02d}.jpg"
        ok = commons_image(scene["search"], image, seen)
        if not ok: fallback_card(image, job["title"], scene["heading"])
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
    output = ROOT / "amethyst-five-minute.mp4"
    subtitle_filter = "subtitles=cloud_build/subtitles.srt:force_style='FontName=DejaVu Sans,FontSize=19,PrimaryColour=&H00FFFFFF,BackColour=&H99000000,BorderStyle=3,Outline=0,Shadow=0,MarginV=38'"
    run("ffmpeg", "-y", "-i", silent, "-i", BUILD / "narration.mp3", "-vf", subtitle_filter,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", output)
    print(f"Created {output} ({duration:.1f} seconds)")

if __name__ == "__main__":
    main(sys.argv[1])
