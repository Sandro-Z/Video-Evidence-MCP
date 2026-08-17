from __future__ import annotations

import html
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel
from yt_dlp import YoutubeDL

from video_evidence_mcp.schemas import TranscriptSegment

TIMING_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})\s+-->\s+"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")


def _retry_sleep(n: int) -> float:
    return min(2.0**n, 20.0)


def _seconds(value: str) -> float:
    chunks = value.replace(",", ".").split(":")
    if len(chunks) == 2:
        minutes, seconds = chunks
        return int(minutes) * 60 + float(seconds)
    hours, minutes, seconds = chunks
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_subtitle_text(text: str) -> list[TranscriptSegment]:
    lines = text.replace("\ufeff", "").splitlines()
    segments: list[TranscriptSegment] = []
    index = 0
    previous_text = ""
    while index < len(lines):
        match = TIMING_RE.search(lines[index])
        if not match:
            index += 1
            continue
        index += 1
        content: list[str] = []
        while index < len(lines) and lines[index].strip():
            clean = html.unescape(TAG_RE.sub("", lines[index])).strip()
            if clean and not clean.startswith(("NOTE", "Kind:")):
                content.append(clean)
            index += 1
        joined = " ".join(content).strip()
        if joined and joined != previous_text:
            segments.append(
                TranscriptSegment(
                    start_seconds=_seconds(match.group("start")),
                    end_seconds=_seconds(match.group("end")),
                    text=joined,
                    relevance=None,
                )
            )
            previous_text = joined
    return segments


def download_captions(
    url: str, workdir: Path, languages: list[str]
) -> tuple[list[TranscriptSegment], str, str | None, list[str]]:
    outtmpl = str(workdir / "caption.%(ext)s")
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "ignoreconfig": True,
        "noplaylist": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": languages,
        "subtitlesformat": "vtt/srt/best",
        "outtmpl": outtmpl,
        "socket_timeout": 20,
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 10,
        "retry_sleep_functions": {"http": _retry_sleep, "extractor": _retry_sleep},
    }
    warnings: list[str] = []
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:
        return [], "none", None, [f"caption retrieval failed: {type(exc).__name__}"]
    caption_files = sorted(workdir.glob("caption.*.vtt")) + sorted(workdir.glob("caption.*.srt"))
    if not caption_files:
        caption_files = sorted(workdir.glob("caption.vtt")) + sorted(workdir.glob("caption.srt"))
    for path in caption_files:
        segments = parse_subtitle_text(path.read_text(encoding="utf-8", errors="replace"))
        if segments:
            language = path.name.split(".")[-2] if path.name.count(".") >= 2 else None
            automatic = language in (info.get("automatic_captions") or {})
            return segments, "auto_caption" if automatic else "platform_caption", language, warnings
    return [], "none", None, warnings


def download_audio(url: str, workdir: Path, max_bytes: int) -> Path:
    target = workdir / "audio.%(ext)s"
    options = {
        "quiet": True,
        "no_warnings": True,
        "ignoreconfig": True,
        "noplaylist": True,
        "format": "bestaudio/best[acodec!=none]",
        "outtmpl": str(target),
        "max_filesize": max_bytes,
        "socket_timeout": 20,
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 10,
        "retry_sleep_functions": {
            "http": _retry_sleep,
            "fragment": _retry_sleep,
            "extractor": _retry_sleep,
        },
    }
    with YoutubeDL(options) as ydl:
        ydl.extract_info(url, download=True)
    files = [path for path in workdir.glob("audio.*") if path.is_file()]
    if not files:
        raise RuntimeError("audio download produced no file")
    audio = max(files, key=lambda path: path.stat().st_size)
    if audio.stat().st_size > max_bytes:
        raise RuntimeError("audio exceeds configured temporary file limit")
    return audio


def transcribe_audio(
    audio: Path, model_name: str, device: str, compute_type: str, language: str | None = None
) -> tuple[list[TranscriptSegment], str | None, str]:
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    raw_segments, info = model.transcribe(
        str(audio), language=language, vad_filter=True, beam_size=5
    )
    segments = [
        TranscriptSegment(
            start_seconds=float(segment.start),
            end_seconds=float(segment.end),
            text=segment.text.strip(),
            relevance=None,
        )
        for segment in raw_segments
        if segment.text.strip()
    ]
    probability = getattr(info, "language_probability", None)
    confidence = (
        f"Whisper language probability {probability:.3f}"
        if probability is not None
        else "ASR confidence unavailable"
    )
    return segments, getattr(info, "language", language), confidence


def obtain_transcript(
    url: str,
    workdir: Path,
    languages: list[str],
    max_bytes: int,
    model_name: str,
    device: str,
    compute_type: str,
    *,
    caption_fetcher: Callable[
        [str, Path, list[str]], tuple[list[TranscriptSegment], str, str | None, list[str]]
    ] = download_captions,
    audio_fetcher: Callable[[str, Path, int], Path] = download_audio,
    asr: Callable[
        [Path, str, str, str, str | None], tuple[list[TranscriptSegment], str | None, str]
    ] = transcribe_audio,
) -> tuple[list[TranscriptSegment], str, str | None, str, list[str]]:
    segments, source, language, warnings = caption_fetcher(url, workdir, languages)
    if segments:
        confidence = (
            "Platform captions; confidence is not reported by the platform"
            if source == "platform_caption"
            else "Automatic platform captions; verify names and numbers against audio/visual evidence"
        )
        return segments, source, language, confidence, warnings
    try:
        audio = audio_fetcher(url, workdir, max_bytes)
        segments, language, confidence = asr(
            audio,
            model_name,
            device,
            compute_type,
            languages[0] if len(languages) == 1 else None,
        )
        return segments, "asr" if segments else "none", language, confidence, warnings
    except Exception as exc:
        warnings.append(f"ASR fallback failed: {type(exc).__name__}")
        return [], "none", None, "No transcript could be produced", warnings
