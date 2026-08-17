from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Platform(StrEnum):
    YOUTUBE = "youtube"
    BILIBILI = "bilibili"


class AnalysisDepth(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ErrorCode(StrEnum):
    INVALID_URL = "invalid_url"
    UNSAFE_URL = "unsafe_url"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    PLAYLIST_NOT_SUPPORTED = "playlist_not_supported"
    LIVE_NOT_SUPPORTED = "live_not_supported"
    PRIVATE_CONTENT = "private_content"
    AUTHENTICATION_REQUIRED = "authentication_required"
    RESOURCE_LIMIT = "resource_limit"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    PLATFORM_ERROR = "platform_error"
    TRANSCRIPT_UNAVAILABLE = "transcript_unavailable"
    PROCESSING_FAILED = "processing_failed"
    INTERNAL_ERROR = "internal_error"


class ToolEnvelope(StrictModel):
    ok: bool
    trace_id: str = Field(min_length=8, max_length=64)
    error_code: ErrorCode | None = None
    warnings: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def error_matches_ok(self) -> ToolEnvelope:
        if self.ok and self.error_code is not None:
            raise ValueError("successful responses cannot contain error_code")
        if not self.ok and self.error_code is None:
            raise ValueError("failed responses require error_code")
        return self


class SearchVideosInput(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    platforms: list[Platform] = Field(
        default_factory=lambda: [Platform.YOUTUBE, Platform.BILIBILI], min_length=1, max_length=2
    )
    max_results: int = Field(10, ge=1, le=20)
    sort: Literal["relevance", "recent"] = "relevance"
    published_after: datetime | None = None
    published_before: datetime | None = None
    language: str | None = Field(None, min_length=2, max_length=35)

    @model_validator(mode="after")
    def valid_dates(self) -> SearchVideosInput:
        if (
            self.published_after is not None
            and self.published_before is not None
            and self.published_after > self.published_before
        ):
            raise ValueError("published_after must not be after published_before")
        if len(set(self.platforms)) != len(self.platforms):
            raise ValueError("platforms cannot contain duplicates")
        return self


class VideoMetadata(StrictModel):
    platform: Platform
    video_id: str = Field(min_length=1, max_length=128)
    url: str
    title: str
    channel: str | None
    published_at: datetime | None
    duration_seconds: float | None = Field(None, ge=0)
    description_snippet: str
    view_count: int | None = Field(None, ge=0)
    is_live: bool
    availability: str
    relevance_reason: str


class SearchVideosOutput(ToolEnvelope):
    status: Literal["completed", "failed"]
    results: list[VideoMetadata] = Field(default_factory=list, max_length=20)


class StartVideoAnalysisInput(StrictModel):
    url: HttpUrl
    question: str = Field(min_length=1, max_length=2000)
    depth: AnalysisDepth = AnalysisDepth.STANDARD
    transcript_languages: list[str] = Field(default_factory=lambda: ["en", "zh"], max_length=10)
    max_frames: int | None = Field(None, ge=4, le=48)
    force_refresh: bool = False

    @model_validator(mode="after")
    def unique_languages(self) -> StartVideoAnalysisInput:
        if len(set(self.transcript_languages)) != len(self.transcript_languages):
            raise ValueError("transcript_languages cannot contain duplicates")
        return self


class StartVideoAnalysisOutput(ToolEnvelope):
    job_id: str
    status: JobStatus
    retry_after_ms: int = Field(ge=0, le=120_000)
    cache_status: Literal["miss", "queued", "in_progress", "hit", "refresh"]


class GetVideoAnalysisInput(StrictModel):
    job_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    include_contact_sheets: bool = True


class TranscriptSegment(StrictModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    text: str
    relevance: float | None = Field(None, ge=0, le=1)

    @model_validator(mode="after")
    def valid_range(self) -> TranscriptSegment:
        if self.end_seconds < self.start_seconds:
            raise ValueError("end_seconds must be >= start_seconds")
        return self


class TimelineEntry(StrictModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    title: str
    summary: str


class OCRCue(StrictModel):
    timestamp_seconds: float = Field(ge=0)
    text: str
    confidence: float | None = Field(None, ge=0, le=1)


class VisualCue(StrictModel):
    timestamp_seconds: float = Field(ge=0)
    observation: str
    source: Literal["frame", "ocr", "optional_model"] = "frame"
    uncertainty: str | None = None


class PopupAction(StrictModel):
    platform: Platform
    action: str
    match_strategy: str
    result: Literal["dismissed", "not_found", "failed", "skipped"]
    timestamp: datetime


class AnalysisEvidence(StrictModel):
    analysis_id: str
    metadata: VideoMetadata
    content_fingerprint: str
    transcript_source: Literal["platform_caption", "auto_caption", "asr", "none"]
    transcript_language: str | None
    transcript_confidence: str
    relevant_transcript: list[TranscriptSegment]
    timeline: list[TimelineEntry]
    coverage_ratio: float = Field(ge=0, le=1)
    frame_timestamps: list[float]
    ocr_cues: list[OCRCue]
    visual_cues: list[VisualCue]
    popup_actions: list[PopupAction]
    access_limitations: list[str]
    analysis_limitations: list[str]
    cached_at: datetime
    expires_at: datetime
    analysis_version: str
    contact_sheet_available: bool


class GetVideoAnalysisOutput(ToolEnvelope):
    status: JobStatus
    progress: float = Field(ge=0, le=1)
    stage: str
    retry_after_ms: int = Field(ge=0, le=120_000)
    evidence: AnalysisEvidence | None = None


class GetVideoWindowInput(StrictModel):
    analysis_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    question: str = Field(min_length=1, max_length=2000)
    include_frames: bool = True
    include_transcript: bool = True

    @model_validator(mode="after")
    def valid_range(self) -> GetVideoWindowInput:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


class VideoWindowEvidence(StrictModel):
    analysis_id: str
    start_seconds: float
    end_seconds: float
    transcript: list[TranscriptSegment]
    frame_timestamps: list[float]
    ocr_cues: list[OCRCue]
    visual_cues: list[VisualCue]
    relevant_evidence: list[str]
    uncertainty: list[str]
    contact_sheet_available: bool


class GetVideoWindowOutput(ToolEnvelope):
    status: Literal["completed", "failed"]
    evidence: VideoWindowEvidence | None


class JobPayload(StrictModel):
    normalized_url: str
    platform: Platform
    video_id: str
    question: str
    depth: AnalysisDepth
    transcript_languages: list[str]
    max_frames: int
    force_refresh: bool
    cache_key: str
