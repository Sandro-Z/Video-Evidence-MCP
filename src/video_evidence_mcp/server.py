from __future__ import annotations

import base64
import hashlib
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, cast

import uvicorn
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ContentBlock, ImageContent, TextContent, ToolAnnotations
from pydantic import BaseModel, Field, HttpUrl, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from video_evidence_mcp.auth import PublicSecurityMiddleware
from video_evidence_mcp.cache import EvidenceCache
from video_evidence_mcp.config import Settings, get_settings
from video_evidence_mcp.jobs import JobStore, RedisJobQueue
from video_evidence_mcp.logging_utils import configure_logging
from video_evidence_mcp.pipeline import PIPELINE_VERSION
from video_evidence_mcp.pipeline.evidence import question_terms
from video_evidence_mcp.pipeline.frames import make_contact_sheet
from video_evidence_mcp.pipeline.metadata import search_videos_impl
from video_evidence_mcp.schemas import (
    AnalysisDepth,
    ErrorCode,
    GetVideoAnalysisInput,
    GetVideoAnalysisOutput,
    GetVideoWindowInput,
    GetVideoWindowOutput,
    JobPayload,
    JobStatus,
    Platform,
    SearchVideosInput,
    SearchVideosOutput,
    StartVideoAnalysisInput,
    StartVideoAnalysisOutput,
    VideoWindowEvidence,
)
from video_evidence_mcp.security import URLSafetyError, assert_public_dns, normalize_video_url

LOGGER = logging.getLogger(__name__)

SERVER_INSTRUCTIONS = """Use search_videos at least once for substantive external research when
public video may improve accuracy. Analyze one to three directly relevant videos. Use deep analysis
for demonstrations, interfaces, charts, field footage, or disputed claims. Check both transcript and
relevant visual windows for important conclusions; distinguish what a video says or visibly shows
from facts independently verified elsewhere. If anonymous video access fails, continue with other
sources and state the limitation."""


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = JobStore(settings.database_path)
        self.store.initialize()
        self.queue = RedisJobQueue(settings.redis_url)
        self.cache = EvidenceCache(
            settings.cache_dir, self.store, settings.cache_ttl_days, settings.max_cache_bytes
        )


def trace_id() -> str:
    return uuid.uuid4().hex


def _enforce_strict_tool_inputs(mcp: MCPServer) -> None:
    """Make SDK-generated argument models reject unknown fields as required by this server."""
    manager = getattr(mcp, "_tool_manager", None)
    registered = getattr(manager, "_tools", None)
    if not isinstance(registered, dict):
        raise RuntimeError(
            "MCP SDK tool registry is unavailable; strict input schemas cannot be set"
        )
    for tool in registered.values():
        argument_model = tool.fn_metadata.arg_model
        argument_model.model_config = {**argument_model.model_config, "extra": "forbid"}
        argument_model.model_rebuild(force=True)
        tool.parameters = argument_model.model_json_schema(by_alias=True)


def _tool_result(model: object, image_path: Path | None = None) -> CallToolResult:
    # Keep enum instances for the SDK's strict return-model validation. MCP's
    # Pydantic transport serializer still emits their wire string values.
    payload = model.model_dump(mode="python") if isinstance(model, BaseModel) else model
    content: list[ContentBlock] = [
        TextContent(
            type="text",
            text=json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
        )
    ]
    if image_path and image_path.is_file():
        content.append(
            ImageContent(
                type="image",
                data=base64.b64encode(image_path.read_bytes()).decode("ascii"),
                mime_type="image/webp",
            )
        )
    return CallToolResult(content=content, structured_content=payload)


def create_server(settings: Settings | None = None) -> tuple[MCPServer, Runtime]:
    settings = settings or get_settings()
    runtime = Runtime(settings)
    mcp = MCPServer(
        name="video-evidence",
        title="Video Evidence",
        description="Timestamped transcript and visual evidence from anonymous public videos.",
        instructions=SERVER_INSTRUCTIONS,
        version="0.1.0",
    )

    read_annotations = ToolAnnotations(
        read_only_hint=True,
        open_world_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
    )

    @mcp.tool(annotations=read_annotations)
    async def search_videos(
        query: Annotated[str, Field(min_length=1, max_length=500)],
        platforms: list[Platform] = [Platform.YOUTUBE, Platform.BILIBILI],
        max_results: Annotated[int, Field(ge=1, le=20)] = 10,
        sort: Literal["relevance", "recent"] = "relevance",
        published_after: datetime | None = None,
        published_before: datetime | None = None,
        language: Annotated[str | None, Field(min_length=2, max_length=35)] = None,
    ) -> SearchVideosOutput:
        """Search YouTube/Bilibili, then verify every returned item's final platform metadata."""
        call_trace = trace_id()
        try:
            request = SearchVideosInput.model_validate(
                {
                    "query": query,
                    "platforms": platforms,
                    "max_results": max_results,
                    "sort": sort,
                    "published_after": published_after,
                    "published_before": published_before,
                    "language": language,
                }
            )
            results, warnings = await search_videos_impl(request)
            return SearchVideosOutput(
                ok=True,
                trace_id=call_trace,
                warnings=warnings,
                status="completed",
                results=results,
            )
        except Exception as exc:
            LOGGER.warning("search failed trace=%s type=%s", call_trace, type(exc).__name__)
            return SearchVideosOutput(
                ok=False,
                trace_id=call_trace,
                error_code=ErrorCode.PLATFORM_ERROR,
                warnings=[f"search failed: {type(exc).__name__}"],
                status="failed",
                results=[],
            )

    @mcp.tool(annotations=read_annotations)
    async def start_video_analysis(
        url: HttpUrl,
        question: Annotated[str, Field(min_length=1, max_length=2000)],
        depth: AnalysisDepth = AnalysisDepth.STANDARD,
        transcript_languages: list[str] = ["en", "zh"],
        max_frames: Annotated[int | None, Field(ge=4, le=48)] = None,
        force_refresh: bool = False,
    ) -> StartVideoAnalysisOutput:
        """Validate one public video URL and enqueue non-blocking server-side evidence analysis."""
        call_trace = trace_id()
        try:
            request = StartVideoAnalysisInput.model_validate(
                {
                    "url": url,
                    "question": question,
                    "depth": depth,
                    "transcript_languages": transcript_languages,
                    "max_frames": max_frames,
                    "force_refresh": force_refresh,
                }
            )
            normalized = normalize_video_url(str(request.url))
            await assert_public_dns(
                normalized.url.split("/", 3)[2].split(":", 1)[0],
                settings.trusted_dns_proxy_cidr,
            )
            defaults = {
                AnalysisDepth.QUICK: 12,
                AnalysisDepth.STANDARD: settings.default_max_frames,
                AnalysisDepth.DEEP: settings.deep_max_frames,
            }
            frame_count = min(
                request.max_frames or defaults[request.depth], defaults[request.depth]
            )
            identity = hashlib.sha256(normalized.url.encode()).hexdigest()
            cache_material = json.dumps(
                {
                    "video": normalized.video_id,
                    "content_identity": identity,
                    "question": request.question,
                    "depth": request.depth,
                    "languages": request.transcript_languages,
                    "frames": frame_count,
                    "version": PIPELINE_VERSION,
                },
                sort_keys=True,
            )
            cache_key = hashlib.sha256(cache_material.encode()).hexdigest()
            payload = JobPayload(
                normalized_url=normalized.url,
                platform=normalized.platform,
                video_id=normalized.video_id,
                question=request.question,
                depth=request.depth,
                transcript_languages=request.transcript_languages,
                max_frames=frame_count,
                force_refresh=request.force_refresh,
                cache_key=cache_key,
            )
            record, created = runtime.store.create_or_get(payload)
            if created:
                runtime.queue.enqueue(record.job_id)
            cache_status: Literal["miss", "queued", "in_progress", "hit", "refresh"] = (
                "refresh"
                if created and request.force_refresh
                else "miss"
                if created
                else "hit"
                if record.status == JobStatus.COMPLETED
                else "in_progress"
                if record.status == JobStatus.RUNNING
                else "queued"
            )
            return StartVideoAnalysisOutput(
                ok=True,
                trace_id=call_trace,
                warnings=[],
                job_id=record.job_id,
                status=record.status,
                retry_after_ms=0 if record.status == JobStatus.COMPLETED else 2000,
                cache_status=cache_status,
            )
        except URLSafetyError as exc:
            return StartVideoAnalysisOutput(
                ok=False,
                trace_id=call_trace,
                error_code=exc.code,
                warnings=[str(exc)],
                job_id="invalid",
                status=JobStatus.FAILED,
                retry_after_ms=0,
                cache_status="miss",
            )
        except Exception as exc:
            LOGGER.warning("enqueue failed trace=%s type=%s", call_trace, type(exc).__name__)
            return StartVideoAnalysisOutput(
                ok=False,
                trace_id=call_trace,
                error_code=ErrorCode.INTERNAL_ERROR,
                warnings=[f"job enqueue failed: {type(exc).__name__}"],
                job_id="unavailable",
                status=JobStatus.FAILED,
                retry_after_ms=0,
                cache_status="miss",
            )

    @mcp.tool(annotations=read_annotations)
    async def get_video_analysis(
        job_id: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")],
        include_contact_sheets: bool = True,
    ) -> GetVideoAnalysisOutput:
        """Poll an analysis job and return its timestamped evidence plus a compact contact sheet."""
        call_trace = trace_id()
        request = GetVideoAnalysisInput(
            job_id=job_id, include_contact_sheets=include_contact_sheets
        )
        record = runtime.store.get(request.job_id)
        if record is None:
            output = GetVideoAnalysisOutput(
                ok=False,
                trace_id=call_trace,
                error_code=ErrorCode.NOT_FOUND,
                warnings=["job_id was not found"],
                status=JobStatus.FAILED,
                progress=0,
                stage="not_found",
                retry_after_ms=0,
                evidence=None,
            )
            return cast(GetVideoAnalysisOutput, _tool_result(output))
        if record.status == JobStatus.COMPLETED and record.analysis_id:
            loaded = runtime.cache.load(record.analysis_id)
            if loaded is not None:
                evidence, sheet = loaded
                output = GetVideoAnalysisOutput(
                    ok=True,
                    trace_id=call_trace,
                    warnings=record.warnings,
                    status=record.status,
                    progress=1,
                    stage="completed",
                    retry_after_ms=0,
                    evidence=evidence,
                )
                return cast(
                    GetVideoAnalysisOutput,
                    _tool_result(output, sheet if request.include_contact_sheets else None),
                )
        if record.status == JobStatus.FAILED:
            try:
                code = ErrorCode(record.error_code or ErrorCode.PROCESSING_FAILED)
            except ValueError:
                code = ErrorCode.PROCESSING_FAILED
            output = GetVideoAnalysisOutput(
                ok=False,
                trace_id=call_trace,
                error_code=code,
                warnings=record.warnings + ([record.error_message] if record.error_message else []),
                status=record.status,
                progress=record.progress,
                stage=record.stage,
                retry_after_ms=0,
                evidence=None,
            )
            return cast(GetVideoAnalysisOutput, _tool_result(output))
        output = GetVideoAnalysisOutput(
            ok=True,
            trace_id=call_trace,
            warnings=record.warnings,
            status=record.status,
            progress=record.progress,
            stage=record.stage,
            retry_after_ms=2000,
            evidence=None,
        )
        return cast(GetVideoAnalysisOutput, _tool_result(output))

    @mcp.tool(annotations=read_annotations)
    async def get_video_window(
        analysis_id: Annotated[
            str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
        ],
        start_seconds: Annotated[float, Field(ge=0)],
        end_seconds: Annotated[float, Field(gt=0)],
        question: Annotated[str, Field(min_length=1, max_length=2000)],
        include_frames: bool = True,
        include_transcript: bool = True,
    ) -> GetVideoWindowOutput:
        """Reinspect a bounded time window using denser cached frames, OCR, and transcript context."""
        call_trace = trace_id()
        try:
            request = GetVideoWindowInput(
                analysis_id=analysis_id,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                question=question,
                include_frames=include_frames,
                include_transcript=include_transcript,
            )
            if request.end_seconds - request.start_seconds > settings.window_max_seconds:
                raise ValueError(f"window exceeds {settings.window_max_seconds} seconds")
            loaded = runtime.cache.load(request.analysis_id)
            if loaded is None:
                output = GetVideoWindowOutput(
                    ok=False,
                    trace_id=call_trace,
                    error_code=ErrorCode.NOT_FOUND,
                    warnings=["analysis_id was not found or cache entry expired"],
                    status="failed",
                    evidence=None,
                )
                return cast(GetVideoWindowOutput, _tool_result(output))
            evidence, _sheet = loaded
            duration = evidence.metadata.duration_seconds or 0
            if request.start_seconds >= duration or request.end_seconds > duration + 0.001:
                raise ValueError("window is outside the video duration")
            transcript = (
                [
                    segment
                    for segment in evidence.relevant_transcript
                    if segment.end_seconds >= request.start_seconds
                    and segment.start_seconds <= request.end_seconds
                ]
                if request.include_transcript
                else []
            )
            ocr = [
                cue
                for cue in evidence.ocr_cues
                if request.start_seconds <= cue.timestamp_seconds <= request.end_seconds
            ]
            visual = [
                cue
                for cue in evidence.visual_cues
                if request.start_seconds <= cue.timestamp_seconds <= request.end_seconds
            ]
            frame_paths = [
                item
                for item in runtime.cache.frame_paths(request.analysis_id)
                if request.start_seconds <= item[1] <= request.end_seconds
            ]
            window_sheet: Path | None = None
            if request.include_frames and frame_paths:
                evidence_path = runtime.store.analysis_paths(request.analysis_id)
                assert evidence_path is not None
                sheet_name = hashlib.sha256(
                    f"{request.start_seconds}:{request.end_seconds}".encode()
                ).hexdigest()[:16]
                window_sheet = evidence_path[0].parent / f"window-{sheet_name}.webp"
                if not window_sheet.exists():
                    make_contact_sheet(frame_paths, window_sheet, columns=3)
            terms = question_terms(request.question)
            relevant = [
                segment.text
                for segment in transcript
                if not terms or question_terms(segment.text) & terms
            ]
            relevant.extend(f"OCR {cue.timestamp_seconds:.1f}s: {cue.text}" for cue in ocr)
            window = VideoWindowEvidence(
                analysis_id=request.analysis_id,
                start_seconds=request.start_seconds,
                end_seconds=request.end_seconds,
                transcript=transcript,
                frame_timestamps=[timestamp for _path, timestamp in frame_paths],
                ocr_cues=ocr,
                visual_cues=visual,
                relevant_evidence=relevant[:30],
                uncertainty=[
                    "Only cached sampled frames are available; this endpoint is not a media downloader.",
                    "OCR and automatic transcript text can contain recognition errors.",
                ],
                contact_sheet_available=bool(window_sheet),
            )
            output = GetVideoWindowOutput(
                ok=True,
                trace_id=call_trace,
                warnings=[],
                status="completed",
                evidence=window,
            )
            return cast(GetVideoWindowOutput, _tool_result(output, window_sheet))
        except (ValueError, ValidationError) as exc:
            output = GetVideoWindowOutput(
                ok=False,
                trace_id=call_trace,
                error_code=ErrorCode.RESOURCE_LIMIT,
                warnings=[str(exc)],
                status="failed",
                evidence=None,
            )
            return cast(GetVideoWindowOutput, _tool_result(output))

    @mcp.custom_route("/healthz", methods=["GET"])  # type: ignore[untyped-decorator]
    async def healthz(_request: Request) -> Response:
        return JSONResponse({"status": "ok", "version": "0.1.0"})

    @mcp.custom_route("/readyz", methods=["GET"])  # type: ignore[untyped-decorator]
    async def readyz(_request: Request) -> Response:
        try:
            queue_ok = runtime.queue.ping()
        except Exception:
            queue_ok = False
        status = 200 if queue_ok else 503
        return JSONResponse(
            {"status": "ready" if queue_ok else "not_ready", "redis": queue_ok},
            status_code=status,
        )

    @mcp.custom_route(  # type: ignore[untyped-decorator]
        "/.well-known/oauth-protected-resource/mcp", methods=["GET"]
    )
    async def protected_resource(_request: Request) -> Response:
        if settings.auth_mode != "oidc":
            return JSONResponse({"error": "not_configured"}, status_code=404)
        return JSONResponse(
            {
                "resource": f"{settings.public_base_url}/mcp",
                "authorization_servers": [settings.oidc_issuer],
                "scopes_supported": settings.oidc_required_scopes.split(),
                "bearer_methods_supported": ["header"],
            }
        )

    _enforce_strict_tool_inputs(mcp)
    return mcp, runtime


def create_app(settings: Settings | None = None) -> object:
    settings = settings or get_settings()
    mcp, _runtime = create_server(settings)
    hosts = ["127.0.0.1", "localhost", "[::1]", "127.0.0.1:*", "localhost:*"]
    origins: list[str] = []
    if settings.domain:
        hosts.extend([settings.domain, f"{settings.domain}:*"])
        origins.append(f"https://{settings.domain}")
    security = TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins)
    app = mcp.streamable_http_app(
        json_response=True,
        stateless_http=True,
        transport_security=security,
        host=settings.host,
    )
    return PublicSecurityMiddleware(app, settings)


app = create_app()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        "video_evidence_mcp.server:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
