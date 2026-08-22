from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest

from video_evidence_mcp.pipeline import metadata
from video_evidence_mcp.pipeline.bilibili_search import BilibiliSearchRateLimitError
from video_evidence_mcp.schemas import Platform, SearchVideosInput, VideoMetadata


def _request(*, sort: str = "relevance") -> SearchVideosInput:
    return SearchVideosInput(
        query="OpenAI 测试",
        platforms=[Platform.YOUTUBE],
        max_results=5,
        sort=sort,
    )


@pytest.mark.parametrize("sort", ["relevance", "recent"])
def test_youtube_search_uses_supported_search_url(sort: str) -> None:
    target = metadata._search_target(_request(sort=sort), Platform.YOUTUBE, 12)
    parsed = urlsplit(target)
    query = parse_qs(parsed.query)

    assert parsed.netloc == "www.youtube.com"
    assert parsed.path == "/results"
    assert query["search_query"] == ["OpenAI 测试"]
    assert query["sp"] == [metadata._YOUTUBE_SEARCH_PARAMS[sort]]
    assert "ytsearchdate" not in target


def test_youtube_keyerror_retries_and_recovers(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0

    def search_once(_request: SearchVideosInput, _platform: Platform, _limit: int) -> list[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise KeyError("contents")
        return ["https://www.youtube.com/watch?v=jNQXAC9IVRw"]

    monkeypatch.setattr(metadata, "_search_candidates_once", search_once)
    monkeypatch.setattr(metadata.time, "sleep", lambda _seconds: None)
    caplog.set_level(logging.WARNING)

    results = metadata._search_candidates(_request(), Platform.YOUTUBE, 5, trace_id="trace-retry")

    assert calls == 2
    assert results == ["https://www.youtube.com/watch?v=jNQXAC9IVRw"]
    assert "trace-retry" in caplog.text
    assert "contents" in caplog.text


def test_youtube_keyerror_exhaustion_is_wrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def search_once(_request: SearchVideosInput, _platform: Platform, _limit: int) -> list[str]:
        raise KeyError("contents")

    monkeypatch.setattr(metadata, "_search_candidates_once", search_once)
    monkeypatch.setattr(metadata.time, "sleep", lambda _seconds: None)

    with pytest.raises(metadata.PlatformAccessError) as captured:
        metadata._search_candidates(_request(), Platform.YOUTUBE, 5, trace_id="trace-fail")

    assert isinstance(captured.value.__cause__, KeyError)


async def test_youtube_bot_challenge_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def extract_one(_url: str, _platform: Platform) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise metadata.PlatformAccessError("Sign in to confirm you're not a bot")

    monkeypatch.setattr(metadata, "_extract_one", extract_one)

    with pytest.raises(metadata.PlatformAccessError):
        await metadata.fetch_metadata("https://www.youtube.com/watch?v=jNQXAC9IVRw")

    assert calls == 1


async def test_search_logs_platform_exception_with_trace(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def search_candidates(
        _request: SearchVideosInput,
        _platform: Platform,
        _limit: int,
        _trace_id: str | None,
    ) -> list[str]:
        raise KeyError("primaryContents")

    monkeypatch.setattr(metadata, "_search_candidates", search_candidates)
    caplog.set_level(logging.WARNING)

    results, warnings = await metadata.search_videos_impl(_request(), trace_id="trace-platform")

    assert results == []
    assert warnings == ["youtube search failed: KeyError"]
    assert "trace-platform" in caplog.text
    assert "primaryContents" in caplog.text


async def test_bilibili_rate_limit_has_actionable_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SearchVideosInput(
        query="OpenAI",
        platforms=[Platform.BILIBILI],
        max_results=2,
    )

    def search_candidates(
        _request: SearchVideosInput,
        _platform: Platform,
        _limit: int,
        _trace_id: str | None,
    ) -> list[str]:
        raise BilibiliSearchRateLimitError("HTTP 412")

    monkeypatch.setattr(metadata, "_search_candidates", search_candidates)

    results, warnings = await metadata.search_videos_impl(request, trace_id="trace-bili")

    assert results == []
    assert warnings == ["bilibili search rate limited: HTTP 412"]


async def test_recent_results_are_sorted_by_verified_publish_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls = [
        "https://www.youtube.com/watch?v=aaaaaa1",
        "https://www.youtube.com/watch?v=bbbbbb2",
        "https://www.youtube.com/watch?v=cccccc3",
    ]
    published = {
        urls[0]: datetime(2026, 8, 1, tzinfo=UTC),
        urls[1]: datetime(2026, 8, 20, tzinfo=UTC),
        urls[2]: datetime(2026, 8, 10, tzinfo=UTC),
    }

    def search_candidates(
        _request: SearchVideosInput,
        _platform: Platform,
        _limit: int,
        _trace_id: str | None,
    ) -> list[str]:
        return urls

    async def fetch_metadata(url: str, _query: str) -> VideoMetadata:
        video_id = url.rsplit("=", 1)[-1]
        return VideoMetadata(
            platform=Platform.YOUTUBE,
            video_id=video_id,
            url=url,
            title=video_id,
            channel=None,
            published_at=published[url],
            duration_seconds=1,
            description_snippet="",
            view_count=None,
            is_live=False,
            availability="public",
            relevance_reason="test",
        )

    monkeypatch.setattr(metadata, "_search_candidates", search_candidates)
    monkeypatch.setattr(metadata, "fetch_metadata", fetch_metadata)
    request = SearchVideosInput(
        query="OpenAI",
        platforms=[Platform.YOUTUBE],
        max_results=2,
        sort="recent",
    )

    results, warnings = await metadata.search_videos_impl(request, trace_id="trace-recent")

    assert warnings == []
    assert [item.url for item in results] == [urls[1], urls[2]]
