from __future__ import annotations

import math
import threading
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from video_evidence_mcp.schemas import SearchVideosInput
from video_evidence_mcp.security import normalize_video_url

_BILIBILI_HOME = "https://www.bilibili.com/"
_BILIBILI_SEARCH_API = "https://api.bilibili.com/x/web-interface/search/type"
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
_GUEST_COOKIE_TTL_SECONDS = 10 * 60
_GUEST_COOKIE_LOCK = threading.Lock()
_GUEST_COOKIES: dict[str, str] = {}
_GUEST_COOKIES_EXPIRE_AT = 0.0


class BilibiliSearchError(RuntimeError):
    pass


class BilibiliSearchRateLimitError(BilibiliSearchError):
    pass


def _new_guest_cookies(transport: httpx.BaseTransport | None = None) -> dict[str, str]:
    with httpx.Client(
        headers=_BROWSER_HEADERS,
        follow_redirects=True,
        timeout=20,
        transport=transport,
    ) as client:
        response = client.get(_BILIBILI_HOME)
        if response.status_code != 200:
            raise BilibiliSearchError(
                f"Bilibili guest session initialization returned HTTP {response.status_code}"
            )
        cookies = {
            cookie.name: cookie.value for cookie in client.cookies.jar if cookie.value is not None
        }
    if "buvid3" not in cookies:
        raise BilibiliSearchError("Bilibili guest session did not issue a buvid3 cookie")
    return cookies


def _guest_cookies(transport: httpx.BaseTransport | None = None) -> dict[str, str]:
    if transport is not None:
        return _new_guest_cookies(transport)
    global _GUEST_COOKIES, _GUEST_COOKIES_EXPIRE_AT
    with _GUEST_COOKIE_LOCK:
        now = time.monotonic()
        if _GUEST_COOKIES and now < _GUEST_COOKIES_EXPIRE_AT:
            return dict(_GUEST_COOKIES)
        cookies = _new_guest_cookies()
        _GUEST_COOKIES = cookies
        _GUEST_COOKIES_EXPIRE_AT = now + _GUEST_COOKIE_TTL_SECONDS
        return dict(cookies)


def _invalidate_guest_cookies() -> None:
    global _GUEST_COOKIES, _GUEST_COOKIES_EXPIRE_AT
    with _GUEST_COOKIE_LOCK:
        _GUEST_COOKIES = {}
        _GUEST_COOKIES_EXPIRE_AT = 0.0


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _published_in_range(item: dict[str, Any], request: SearchVideosInput) -> bool:
    if request.published_after is None and request.published_before is None:
        return True
    timestamp = item.get("pubdate")
    if not isinstance(timestamp, (int, float)):
        return False
    published = datetime.fromtimestamp(float(timestamp), tz=UTC)
    if request.published_after is not None and published < _utc(request.published_after):
        return False
    return not (request.published_before is not None and published > _utc(request.published_before))


def search_bilibili(
    request: SearchVideosInput,
    limit: int,
    *,
    transport: httpx.BaseTransport | None = None,
) -> list[str]:
    """Search public Bilibili videos using a real anonymous guest session."""
    if limit <= 0:
        return []
    cookies = _guest_cookies(transport)
    urls: list[str] = []
    seen_urls: set[str] = set()
    page_count = max(1, math.ceil(limit / 20))
    with httpx.Client(
        headers=_BROWSER_HEADERS,
        cookies=cookies,
        follow_redirects=True,
        timeout=20,
        transport=transport,
    ) as client:
        for page in range(1, page_count + 1):
            response = client.get(
                _BILIBILI_SEARCH_API,
                params={
                    "keyword": request.query,
                    "search_type": "video",
                    "page": page,
                    "order": "pubdate" if request.sort == "recent" else "totalrank",
                    "duration": 0,
                    "tids": 0,
                    "highlight": 1,
                },
                headers={"Referer": "https://search.bilibili.com/"},
            )
            if response.status_code == 412:
                if transport is None:
                    _invalidate_guest_cookies()
                raise BilibiliSearchRateLimitError(
                    "Bilibili search rejected the guest session with HTTP 412"
                )
            if response.status_code != 200:
                raise BilibiliSearchError(f"Bilibili search returned HTTP {response.status_code}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise BilibiliSearchError("Bilibili search returned invalid JSON") from exc
            if not isinstance(payload, dict):
                raise BilibiliSearchError("Bilibili search returned an unexpected response")
            code = payload.get("code")
            if code != 0:
                raise BilibiliSearchError(f"Bilibili search API returned code {code}")
            data = payload.get("data")
            rows = data.get("result") if isinstance(data, dict) else None
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                if not isinstance(row, dict) or not _published_in_range(row, request):
                    continue
                bvid = row.get("bvid")
                if not isinstance(bvid, str):
                    continue
                try:
                    normalized = normalize_video_url(f"https://www.bilibili.com/video/{bvid}")
                except ValueError:
                    continue
                if normalized.url in seen_urls:
                    continue
                seen_urls.add(normalized.url)
                urls.append(normalized.url)
                if len(urls) >= limit:
                    return urls
    return urls
