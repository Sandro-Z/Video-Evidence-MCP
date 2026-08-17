from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from video_evidence_mcp.cache import EvidenceCache
from video_evidence_mcp.config import Settings
from video_evidence_mcp.jobs import JobStore
from video_evidence_mcp.pipeline import runner
from video_evidence_mcp.schemas import (
    AnalysisDepth,
    JobPayload,
    Platform,
    TranscriptSegment,
    VideoMetadata,
)


@pytest.mark.parametrize(
    ("fail_after_download", "caption_available"),
    [(False, True), (True, True), (False, False)],
)
def test_pipeline_deletes_raw_media_on_success_or_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_after_download: bool,
    caption_available: bool,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        playwright_enabled=False,
        ocr_enabled=False,
        max_temp_bytes=64 * 1024**2,
    )
    store = JobStore(settings.database_path)
    store.initialize()
    cache = EvidenceCache(settings.cache_dir, store, 14, 1024**3)
    payload = JobPayload(
        normalized_url="https://www.youtube.com/watch?v=BaW_jenozKc",
        platform=Platform.YOUTUBE,
        video_id="BaW_jenozKc",
        question="what happens?",
        depth=AnalysisDepth.QUICK,
        transcript_languages=["en"],
        max_frames=4,
        force_refresh=False,
        cache_key="f" * 64,
    )

    async def metadata(_url: str, _question: str) -> VideoMetadata:
        return VideoMetadata(
            platform=Platform.YOUTUBE,
            video_id="BaW_jenozKc",
            url=payload.normalized_url,
            title="Fixture",
            channel="Fixture",
            published_at=datetime.now(UTC),
            duration_seconds=4,
            description_snippet="",
            view_count=1,
            is_live=False,
            availability="public",
            relevance_reason="fixture",
        )

    def transcript(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        if not caption_available:
            return ([], "none", None, "none", ["dedicated audio unavailable"])
        return (
            [TranscriptSegment(start_seconds=0, end_seconds=1, text="hello")],
            "platform_caption",
            "en",
            "fixture",
            [],
        )

    def asr(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return (
            [TranscriptSegment(start_seconds=0, end_seconds=1, text="fallback speech")],
            "en",
            "fixture ASR confidence",
        )

    def media(_url: str, directory: Path, _limit: int) -> Path:
        path = directory / "video.mp4"
        path.write_bytes(b"raw-media")
        return path

    def frames(_media: Path, directory: Path, timestamps: list[float]) -> list[Path]:
        directory.mkdir(parents=True)
        output: list[Path] = []
        for index, _timestamp in enumerate(timestamps):
            path = directory / f"{index}.jpg"
            Image.new("RGB", (320, 180), "green").save(path)
            output.append(path)
        return output

    def probe(_media: Path) -> float:
        if fail_after_download:
            raise RuntimeError("fixture failure after raw media creation")
        return 4.0

    monkeypatch.setattr(runner, "fetch_metadata", metadata)
    monkeypatch.setattr(runner, "obtain_transcript", transcript)
    monkeypatch.setattr(runner, "transcribe_audio", asr)
    monkeypatch.setattr(runner, "download_video", media)
    monkeypatch.setattr(runner, "probe_duration", probe)
    monkeypatch.setattr(runner, "detect_scene_timestamps", lambda *_args: [])
    monkeypatch.setattr(runner, "extract_distributed_frames", frames)

    if fail_after_download:
        with pytest.raises(RuntimeError, match="fixture failure"):
            runner.run_pipeline(payload, settings, cache, lambda *_args: None)
    else:
        analysis_id = runner.run_pipeline(payload, settings, cache, lambda *_args: None)
        loaded = cache.load(analysis_id)
        assert loaded is not None
        assert loaded[0].transcript_source == ("platform_caption" if caption_available else "asr")
    assert list(settings.temp_dir.glob("job-*")) == []
    assert not any(
        path.name.startswith(("video.", "audio.")) for path in settings.cache_dir.rglob("*")
    )
