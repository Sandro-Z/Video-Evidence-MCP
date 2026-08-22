from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from video_evidence_mcp.pipeline.bilibili_search import (
    BilibiliSearchRateLimitError,
    search_bilibili,
)
from video_evidence_mcp.schemas import Platform, SearchVideosInput


def _request(**overrides: object) -> SearchVideosInput:
    values: dict[str, object] = {
        "query": "OpenAI 测试",
        "platforms": [Platform.BILIBILI],
        "max_results": 5,
        "sort": "relevance",
    }
    values.update(overrides)
    return SearchVideosInput.model_validate(values)


def _guest_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"set-cookie": "buvid3=guest-visitor-infoc; Domain=.bilibili.com; Path=/"},
        text="ok",
        request=request,
    )


def test_search_uses_real_guest_cookie_and_recent_order() -> None:
    seen_cookie = ""
    seen_order = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_cookie, seen_order
        if request.url.host == "www.bilibili.com":
            return _guest_response(request)
        seen_cookie = request.headers.get("cookie", "")
        seen_order = request.url.params["order"]
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "result": [
                        {"bvid": "BV1LJ8M64EKg", "pubdate": 1_775_000_000},
                        {"bvid": "invalid", "pubdate": 1_774_000_000},
                    ]
                },
            },
            request=request,
        )

    results = search_bilibili(_request(sort="recent"), 5, transport=httpx.MockTransport(handler))

    assert "buvid3=guest-visitor-infoc" in seen_cookie
    assert seen_order == "pubdate"
    assert results == ["https://www.bilibili.com/video/BV1LJ8M64EKg"]


def test_search_filters_verified_publication_window() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.bilibili.com":
            return _guest_response(request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "result": [
                        {
                            "bvid": "BV1LJ8M64EKg",
                            "pubdate": int(datetime(2026, 8, 10, tzinfo=UTC).timestamp()),
                        },
                        {
                            "bvid": "BV15pXvY8Ed5",
                            "pubdate": int(datetime(2026, 7, 1, tzinfo=UTC).timestamp()),
                        },
                    ]
                },
            },
            request=request,
        )

    results = search_bilibili(
        _request(published_after=datetime(2026, 8, 1, tzinfo=UTC)),
        5,
        transport=httpx.MockTransport(handler),
    )

    assert results == ["https://www.bilibili.com/video/BV1LJ8M64EKg"]


def test_search_classifies_http_412() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.bilibili.com":
            return _guest_response(request)
        return httpx.Response(412, text="", request=request)

    with pytest.raises(BilibiliSearchRateLimitError, match="HTTP 412"):
        search_bilibili(_request(), 5, transport=httpx.MockTransport(handler))
