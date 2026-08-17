from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from video_evidence_mcp.cache import EvidenceCache
from video_evidence_mcp.jobs import JobStore
from video_evidence_mcp.schemas import (
    AnalysisDepth,
    AnalysisEvidence,
    JobPayload,
    JobStatus,
    Platform,
    VideoMetadata,
)


def payload(cache_key: str = "a" * 64) -> JobPayload:
    return JobPayload(
        normalized_url="https://www.youtube.com/watch?v=BaW_jenozKc",
        platform=Platform.YOUTUBE,
        video_id="BaW_jenozKc",
        question="what happens?",
        depth=AnalysisDepth.STANDARD,
        transcript_languages=["en"],
        max_frames=24,
        force_refresh=False,
        cache_key=cache_key,
    )


def evidence(analysis_id: str, expires_at: datetime) -> AnalysisEvidence:
    now = datetime.now(UTC)
    return AnalysisEvidence(
        analysis_id=analysis_id,
        metadata=VideoMetadata(
            platform=Platform.YOUTUBE,
            video_id="BaW_jenozKc",
            url="https://www.youtube.com/watch?v=BaW_jenozKc",
            title="Test",
            channel="Tester",
            published_at=now,
            duration_seconds=10,
            description_snippet="",
            view_count=None,
            is_live=False,
            availability="public",
            relevance_reason="test",
        ),
        content_fingerprint="b" * 64,
        transcript_source="none",
        transcript_language=None,
        transcript_confidence="none",
        relevant_transcript=[],
        timeline=[],
        coverage_ratio=1,
        frame_timestamps=[],
        ocr_cues=[],
        visual_cues=[],
        popup_actions=[],
        access_limitations=[],
        analysis_limitations=[],
        cached_at=now,
        expires_at=expires_at,
        analysis_version="0.1.0",
        contact_sheet_available=False,
    )


def test_jobs_are_idempotent_and_recover_interrupted(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    first, created = store.create_or_get(payload())
    second, created_again = store.create_or_get(payload())
    assert created is True
    assert created_again is False
    assert second.job_id == first.job_id
    claimed = store.claim(first.job_id)
    assert claimed is not None and claimed.status == JobStatus.RUNNING
    assert store.recover_interrupted() == 1
    assert store.get(first.job_id).status == JobStatus.FAILED  # type: ignore[union-attr]


def test_job_diagnostics_remove_control_characters(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    record, _created = store.create_or_get(payload())
    store.fail(record.job_id, "processing_failed", "ERROR:\r\nnetwork\x00failure", ["retry\r1"])
    failed = store.get(record.job_id)
    assert failed is not None
    assert failed.error_message == "ERROR: network failure"
    assert failed.warnings == ["retry 1"]


def test_cache_roundtrip_and_expiry_cleanup(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    cache = EvidenceCache(tmp_path / "cache", store, ttl_days=14, max_bytes=1024 * 1024)
    item = evidence("analysis123", datetime.now(UTC) + timedelta(days=1))
    cache.save("c" * 64, item, None)
    loaded = cache.load("analysis123")
    assert loaded is not None and loaded[0].analysis_id == "analysis123"

    refreshed = evidence("analysis789", datetime.now(UTC) + timedelta(days=1))
    cache.save("c" * 64, refreshed, None)
    assert cache.load("analysis789") is not None
    assert not (tmp_path / "cache" / "analysis123").exists()

    expired = evidence("analysis456", datetime.now(UTC) - timedelta(seconds=1))
    cache.save("d" * 64, expired, None)
    report = cache.cleanup()
    assert report["entries"] == 1
    assert not (tmp_path / "cache" / "analysis456").exists()
