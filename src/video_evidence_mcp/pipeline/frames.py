from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from yt_dlp import YoutubeDL

from video_evidence_mcp.pipeline.yt_dlp_support import common_ydl_options

VIDEO_FORMAT_SELECTOR = (
    "bestvideo[width<=1280][height<=1280]+bestaudio/"
    "best[width<=1280][height<=1280]/worstvideo+worstaudio/worst"
)


def _retry_sleep(n: int) -> float:
    return min(2.0**n, 20.0)


def probe_duration(media: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(media),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
    payload = json.loads(result.stdout)
    return float(payload["format"]["duration"])


def download_video(url: str, workdir: Path, max_bytes: int) -> Path:
    options = common_ydl_options(url)
    options.update(
        {
            # Bound both axes so portrait and landscape 720p-class streams are eligible.
            # Do not filter on `filesize`: many DASH formats only expose an estimate and
            # yt-dlp treats an unknown exact filesize as not matching that filter.
            "format": VIDEO_FORMAT_SELECTOR,
            "outtmpl": str(workdir / "video.%(ext)s"),
            "max_filesize": max_bytes,
            "merge_output_format": "mp4",
            "socket_timeout": 20,
            "retries": 3,
            "fragment_retries": 3,
            "extractor_retries": 3,
            "retry_sleep_functions": {
                "http": _retry_sleep,
                "fragment": _retry_sleep,
                "extractor": _retry_sleep,
            },
        }
    )
    with YoutubeDL(options) as ydl:
        ydl.extract_info(url, download=True)
    candidates = [path for path in workdir.glob("video.*") if path.is_file()]
    if not candidates:
        raise RuntimeError("video download produced no file")
    media = max(candidates, key=lambda path: path.stat().st_size)
    if media.stat().st_size > max_bytes:
        raise RuntimeError("video exceeds configured temporary file limit")
    return media


def distributed_timestamps(duration: float, count: int) -> list[float]:
    if duration <= 0 or count <= 0:
        return []
    margin = min(1.0, duration * 0.02)
    usable = max(duration - margin * 2, 0.01)
    return [round(margin + usable * (index + 0.5) / count, 3) for index in range(count)]


def extract_distributed_frames(media: Path, output: Path, timestamps: list[float]) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    for index, timestamp in enumerate(timestamps):
        target = output / f"frame-{index:03d}-{timestamp:.3f}.jpg"
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(media),
            "-frames:v",
            "1",
            "-vf",
            "scale='min(960,iw)':-2",
            "-q:v",
            "4",
            "-y",
            str(target),
        ]
        subprocess.run(command, check=True, capture_output=True, timeout=60)
        if target.is_file():
            frames.append(target)
    return frames


def detect_scene_timestamps(media: Path, duration: float, max_scenes: int) -> list[float]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(media),
        "-filter:v",
        "select='gt(scene,0.35)',showinfo",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return []
    values = [
        float(value) for value in __import__("re").findall(r"pts_time:([0-9.]+)", result.stderr)
    ]
    values = [value for value in values if 0.5 <= value <= max(duration - 0.5, 0.5)]
    if len(values) <= max_scenes:
        return values
    step = len(values) / max_scenes
    return [values[min(int(index * step), len(values) - 1)] for index in range(max_scenes)]


def timestamp_label(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


def stamp_frame(source: Path, timestamp: float, target: Path) -> Path:
    with Image.open(source).convert("RGB") as image:
        draw = ImageDraw.Draw(image)
        label = timestamp_label(timestamp)
        font = ImageFont.load_default(size=20)
        box = draw.textbbox((0, 0), label, font=font)
        padding = 8
        draw.rectangle((8, 8, box[2] + padding * 2 + 8, box[3] + padding * 2 + 8), fill=(0, 0, 0))
        draw.text((8 + padding, 8 + padding), label, fill=(255, 255, 255), font=font)
        image.save(target, "JPEG", quality=82, optimize=True)
    return target


def make_contact_sheet(frames: list[tuple[Path, float]], target: Path, columns: int = 4) -> Path:
    if not frames:
        raise ValueError("contact sheet requires at least one frame")
    thumb_width, thumb_height = 320, 200
    rows = math.ceil(len(frames) / columns)
    sheet = Image.new("RGB", (columns * thumb_width, rows * thumb_height), (24, 24, 24))
    for index, (path, _timestamp) in enumerate(frames):
        with Image.open(path).convert("RGB") as image:
            image.thumbnail((thumb_width, thumb_height))
            x = (index % columns) * thumb_width + (thumb_width - image.width) // 2
            y = (index // columns) * thumb_height + (thumb_height - image.height) // 2
            sheet.paste(image, (x, y))
    sheet.save(target, "WEBP", quality=74, method=6)
    return target
