from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path

from playwright.async_api import async_playwright

from video_evidence_mcp.adapters import BilibiliAdapter, YouTubeAdapter
from video_evidence_mcp.cache import EvidenceCache, default_expiry
from video_evidence_mcp.config import Settings
from video_evidence_mcp.pipeline import PIPELINE_VERSION
from video_evidence_mcp.pipeline.evidence import build_timeline, rank_transcript
from video_evidence_mcp.pipeline.frames import (
    detect_scene_timestamps,
    distributed_timestamps,
    download_video,
    extract_distributed_frames,
    make_contact_sheet,
    probe_duration,
    stamp_frame,
)
from video_evidence_mcp.pipeline.metadata import fetch_metadata
from video_evidence_mcp.pipeline.ocr import OCRProcessor
from video_evidence_mcp.pipeline.transcript import obtain_transcript, transcribe_audio
from video_evidence_mcp.schemas import (
    AnalysisEvidence,
    ErrorCode,
    JobPayload,
    Platform,
    PopupAction,
    VisualCue,
)

LOGGER = logging.getLogger(__name__)
ProgressCallback = Callable[[float, str, list[str] | None], None]


class PipelineError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


async def _browser_actions(url: str, platform: Platform) -> tuple[list[PopupAction], list[str]]:
    adapter = YouTubeAdapter() if platform == Platform.YOUTUBE else BilibiliAdapter()
    warnings: list[str] = []
    actions: list[PopupAction] = []
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None,
            )
            context = await browser.new_context(locale="en-US")
            page = await context.new_page()
            await adapter.install_network_guard(page)
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            actions = await adapter.dismiss_obstructions(page)
            if response is not None and response.status in {401, 403}:
                warnings.append("anonymous browser access returned authentication-related status")
            await context.close()
            await browser.close()
    except Exception as exc:
        warnings.append(f"browser obstruction check failed: {type(exc).__name__}")
    return actions, warnings


def _fingerprint(payload: JobPayload, duration: float, title: str) -> str:
    material = f"{payload.platform}:{payload.video_id}:{duration:.3f}:{title}:{PIPELINE_VERSION}"
    return hashlib.sha256(material.encode()).hexdigest()


def run_pipeline(
    payload: JobPayload,
    settings: Settings,
    cache: EvidenceCache,
    progress: ProgressCallback,
) -> str:
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    workdir_path = Path(tempfile.mkdtemp(prefix="job-", dir=settings.temp_dir))
    try:
        progress(0.05, "metadata", warnings)
        metadata = asyncio.run(fetch_metadata(payload.normalized_url, payload.question))
        duration = metadata.duration_seconds
        if metadata.is_live:
            raise PipelineError(ErrorCode.LIVE_NOT_SUPPORTED, "live streams are not supported")
        if metadata.availability not in {"public", "unlisted"}:
            code = (
                ErrorCode.AUTHENTICATION_REQUIRED
                if metadata.availability in {"needs_auth", "subscriber_only"}
                else ErrorCode.PRIVATE_CONTENT
            )
            raise PipelineError(code, f"video availability is {metadata.availability}")
        if duration is not None and duration > settings.max_video_seconds:
            raise PipelineError(ErrorCode.RESOURCE_LIMIT, "video exceeds MAX_VIDEO_SECONDS")

        progress(0.12, "browser_obstruction_check", warnings)
        if settings.playwright_enabled:
            popup_actions, browser_warnings = asyncio.run(
                _browser_actions(payload.normalized_url, payload.platform)
            )
            warnings.extend(browser_warnings)
        else:
            popup_actions = []
            warnings.append("Playwright obstruction handling disabled by configuration")

        progress(0.20, "captions", warnings)
        transcript, transcript_source, language, transcript_confidence, transcript_warnings = (
            obtain_transcript(
                payload.normalized_url,
                workdir_path,
                payload.transcript_languages,
                settings.max_temp_bytes,
                settings.asr_model,
                settings.asr_device,
                settings.asr_compute_type,
            )
        )
        warnings.extend(transcript_warnings)

        progress(0.45, "media_download", warnings)
        media = download_video(payload.normalized_url, workdir_path, settings.max_temp_bytes)
        actual_duration = probe_duration(media)
        if actual_duration > settings.max_video_seconds:
            raise PipelineError(
                ErrorCode.RESOURCE_LIMIT, "downloaded media exceeds MAX_VIDEO_SECONDS"
            )
        duration = actual_duration

        # If the dedicated audio fetch failed, reuse the already downloaded media.
        # faster-whisper/FFmpeg can decode its audio stream directly, avoiding a
        # second platform request and preserving the subtitle -> ASR fallback.
        if transcript_source == "none":
            progress(0.52, "asr_from_downloaded_media", warnings)
            try:
                fallback_segments, fallback_language, fallback_confidence = transcribe_audio(
                    media,
                    settings.asr_model,
                    settings.asr_device,
                    settings.asr_compute_type,
                    payload.transcript_languages[0]
                    if len(payload.transcript_languages) == 1
                    else None,
                )
                if fallback_segments:
                    transcript = fallback_segments
                    transcript_source = "asr"
                    language = fallback_language
                    transcript_confidence = fallback_confidence
            except Exception as exc:
                warnings.append(f"ASR from downloaded media failed: {type(exc).__name__}")

        progress(0.58, "distributed_frames", warnings)
        fixed_count = max(4, payload.max_frames * 3 // 4)
        fixed = distributed_timestamps(duration, fixed_count)
        scene_budget = max(0, payload.max_frames - len(fixed))
        scenes = detect_scene_timestamps(media, duration, scene_budget) if scene_budget else []
        timestamps = sorted({round(value, 3) for value in fixed + scenes})[: payload.max_frames]
        raw_frames = extract_distributed_frames(media, workdir_path / "raw-frames", timestamps)
        stamped: list[tuple[Path, float]] = []
        stamped_dir = workdir_path / "stamped-frames"
        stamped_dir.mkdir()
        for index, (source, timestamp) in enumerate(zip(raw_frames, timestamps, strict=False)):
            target = stamped_dir / f"frame-{index:03d}.jpg"
            stamped.append((stamp_frame(source, timestamp, target), timestamp))

        progress(0.72, "ocr", warnings)
        if settings.ocr_enabled:
            try:
                ocr_cues = OCRProcessor().scan(stamped)
            except Exception as exc:
                ocr_cues = []
                warnings.append(f"OCR failed: {type(exc).__name__}")
        else:
            ocr_cues = []
            warnings.append("OCR disabled by configuration")

        progress(0.82, "evidence_selection", warnings)
        relevant_transcript = rank_transcript(transcript, payload.question)
        visual_cues = [
            VisualCue(
                timestamp_seconds=timestamp,
                observation="Timestamped frame sampled for visual inspection in the contact sheet.",
                source="frame",
                uncertainty="No model-generated visual description; inspect pixels directly.",
            )
            for _path, timestamp in stamped
        ]
        visual_cues.extend(
            VisualCue(
                timestamp_seconds=cue.timestamp_seconds,
                observation=f"OCR detected: {cue.text}",
                source="ocr",
                uncertainty="OCR may confuse names, numbers, or stylized text.",
            )
            for cue in ocr_cues
        )
        contact_sheet = make_contact_sheet(stamped, workdir_path / "contact-sheet.webp")
        cached_at, expires_at = default_expiry(settings.cache_ttl_days)
        analysis_id = uuid.uuid4().hex
        fingerprint = _fingerprint(payload, duration, metadata.title)
        coverage_ratio = (
            0.0 if not timestamps else min(1.0, len(timestamps) / max(payload.max_frames, 1))
        )
        limitations = [
            "Visual cues identify sampling locations and OCR; the calling model must inspect the contact sheet.",
            "Automatic captions and ASR can misrecognize names and numbers.",
        ]
        if transcript_source == "none":
            limitations.append(
                "No transcript was available; conclusions must rely on visual evidence only."
            )
        evidence = AnalysisEvidence(
            analysis_id=analysis_id,
            metadata=metadata.model_copy(update={"duration_seconds": duration}),
            content_fingerprint=fingerprint,
            transcript_source=transcript_source,  # type: ignore[arg-type]
            transcript_language=language,
            transcript_confidence=transcript_confidence,
            relevant_transcript=relevant_transcript,
            timeline=build_timeline(transcript, duration),
            coverage_ratio=coverage_ratio,
            frame_timestamps=timestamps,
            ocr_cues=ocr_cues,
            visual_cues=visual_cues,
            popup_actions=popup_actions,
            access_limitations=browser_warnings if settings.playwright_enabled else [],
            analysis_limitations=limitations,
            cached_at=cached_at,
            expires_at=expires_at,
            analysis_version=PIPELINE_VERSION,
            contact_sheet_available=True,
        )
        progress(0.93, "cache_write", warnings)
        cache.save(payload.cache_key, evidence, contact_sheet, stamped)
        cache.cleanup()
        return analysis_id
    finally:
        # Raw video/audio and all other job-temporary artifacts are removed on success or failure.
        if workdir_path.parent == settings.temp_dir and workdir_path.name.startswith("job-"):
            shutil.rmtree(workdir_path, ignore_errors=True)
