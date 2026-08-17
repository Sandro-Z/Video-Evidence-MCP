from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from video_evidence_mcp.schemas import Platform, SearchVideosInput, VideoMetadata
from video_evidence_mcp.security import normalize_video_url

LOGGER = logging.getLogger(__name__)
_RATE_LOCK = threading.Lock()
_LAST_REQUEST: dict[Platform, float] = {}
_MIN_REQUEST_INTERVAL = {Platform.YOUTUBE: 0.75, Platform.BILIBILI: 1.0}


class PlatformAccessError(RuntimeError):
    pass


def _platform_rate_limit(platform: Platform) -> None:
    """Apply a conservative, per-process platform request floor."""
    with _RATE_LOCK:
        now = time.monotonic()
        delay = _MIN_REQUEST_INTERVAL[platform] - (now - _LAST_REQUEST.get(platform, 0.0))
        if delay > 0:
            time.sleep(delay)
        _LAST_REQUEST[platform] = time.monotonic()


def _ydl(options: dict[str, Any] | None = None) -> YoutubeDL:
    base: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 3,
        "ignoreconfig": True,
        "cachedir": False,
    }
    base.update(options or {})
    return YoutubeDL(base)


def _published(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value)
    try:
        if len(text) == 8 and text.isdigit():
            return datetime.strptime(text, "%Y%m%d").replace(tzinfo=UTC)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _platform_from_info(info: dict[str, Any], fallback: Platform | None = None) -> Platform:
    extractor = str(info.get("extractor_key") or info.get("extractor") or "").lower()
    if "bili" in extractor:
        return Platform.BILIBILI
    if "youtube" in extractor:
        return Platform.YOUTUBE
    if fallback is not None:
        return fallback
    return normalize_video_url(str(info.get("webpage_url") or info.get("original_url"))).platform


def metadata_from_info(
    info: dict[str, Any], query: str = "", fallback: Platform | None = None
) -> VideoMetadata:
    platform = _platform_from_info(info, fallback)
    video_id = str(info.get("id") or "")
    canonical = normalize_video_url(
        str(info.get("webpage_url") or info.get("original_url") or _url_for(platform, video_id))
    )
    title = str(info.get("title") or "Untitled")
    description = str(info.get("description") or "")
    tokens = [token.casefold() for token in query.split() if len(token) > 1]
    haystack = f"{title} {description}".casefold()
    matched = [token for token in tokens if token in haystack]
    reason = (
        f"Verified title/description match: {', '.join(matched[:5])}"
        if matched
        else "Platform search candidate; metadata was verified from the final video page"
    )
    availability = str(info.get("availability") or "public")
    return VideoMetadata(
        platform=platform,
        video_id=canonical.video_id,
        url=canonical.url,
        title=title[:500],
        channel=str(info.get("channel") or info.get("uploader") or "") or None,
        published_at=_published(info.get("timestamp") or info.get("upload_date")),
        duration_seconds=float(info["duration"]) if info.get("duration") is not None else None,
        description_snippet=" ".join(description.split())[:500],
        view_count=int(info["view_count"]) if info.get("view_count") is not None else None,
        is_live=bool(info.get("is_live") or info.get("live_status") in {"is_live", "post_live"}),
        availability=availability,
        relevance_reason=reason,
    )


def _url_for(platform: Platform, video_id: str) -> str:
    if platform == Platform.YOUTUBE:
        return f"https://www.youtube.com/watch?v={video_id}"
    return f"https://www.bilibili.com/video/{video_id}"


def _extract_one(url: str, platform: Platform) -> dict[str, Any]:
    _platform_rate_limit(platform)
    try:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
            _ydl() as ydl,
        ):
            result = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        raise PlatformAccessError(str(exc)) from exc
    if result is None or result.get("_type") in {"playlist", "multi_video"}:
        raise PlatformAccessError("video metadata was unavailable or resolved to a playlist")
    return dict(result)


async def fetch_metadata(url: str, query: str = "") -> VideoMetadata:
    normalized = normalize_video_url(url)
    last_error: PlatformAccessError | None = None
    for attempt in range(3):
        try:
            info = await asyncio.to_thread(_extract_one, normalized.url, normalized.platform)
            break
        except PlatformAccessError as exc:
            last_error = exc
            if attempt == 2:
                raise
            await asyncio.sleep(2**attempt)
    else:  # pragma: no cover - the loop either succeeds or raises
        assert last_error is not None
        raise last_error
    return metadata_from_info(info, query=query, fallback=normalized.platform)


def _search_candidates(request: SearchVideosInput, platform: Platform, limit: int) -> list[str]:
    _platform_rate_limit(platform)
    prefix = (
        "ytsearchdate" if platform == Platform.YOUTUBE and request.sort == "recent" else "ytsearch"
    )
    if platform == Platform.BILIBILI:
        prefix = "bilisearch"
    options: dict[str, Any] = {"extract_flat": True, "playlistend": min(limit * 3, 40)}
    if request.published_after:
        options["dateafter"] = request.published_after.strftime("%Y%m%d")
    if request.published_before:
        options["datebefore"] = request.published_before.strftime("%Y%m%d")
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
        _ydl(options) as ydl,
    ):
        result = ydl.extract_info(f"{prefix}{min(limit * 3, 40)}:{request.query}", download=False)
    entries = (result or {}).get("entries") or []
    urls: list[str] = []
    for entry in entries:
        if not entry:
            continue
        candidate = entry.get("url") or entry.get("webpage_url")
        if not candidate:
            candidate = _url_for(platform, str(entry.get("id") or ""))
        try:
            normalized = normalize_video_url(str(candidate))
        except ValueError:
            continue
        if normalized.platform == platform:
            urls.append(normalized.url)
    return list(dict.fromkeys(urls))


async def search_videos_impl(request: SearchVideosInput) -> tuple[list[VideoMetadata], list[str]]:
    per_platform = max(1, request.max_results // len(request.platforms) + 1)
    warnings: list[str] = []
    candidate_groups = await asyncio.gather(
        *[
            asyncio.to_thread(_search_candidates, request, platform, per_platform)
            for platform in request.platforms
        ],
        return_exceptions=True,
    )
    candidates: list[str] = []
    for platform, group in zip(request.platforms, candidate_groups, strict=True):
        if isinstance(group, BaseException):
            warnings.append(f"{platform.value} search failed: {type(group).__name__}")
        else:
            candidates.extend(group[:per_platform])
    verified = await asyncio.gather(
        *[fetch_metadata(url, request.query) for url in candidates[: request.max_results * 2]],
        return_exceptions=True,
    )
    results: list[VideoMetadata] = []
    for item in verified:
        if isinstance(item, BaseException):
            warnings.append(f"candidate metadata verification failed: {type(item).__name__}")
            continue
        if item.is_live or item.availability not in {"public", "unlisted"}:
            continue
        if request.language:
            # Language may be unavailable in platform metadata; do not assert an unverified value.
            item.relevance_reason += (
                f"; requested language {request.language} will be checked in captions"
            )
        results.append(item)
        if len(results) >= request.max_results:
            break
    return results, warnings[:50]
