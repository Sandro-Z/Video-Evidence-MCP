from __future__ import annotations

import socket

import pytest

from video_evidence_mcp.schemas import ErrorCode, Platform
from video_evidence_mcp.security import (
    URLSafetyError,
    assert_public_dns,
    normalize_video_url,
    runtime_host_allowed,
    validate_redirect,
)


@pytest.mark.parametrize(
    ("raw", "platform", "video_id"),
    [
        ("https://youtu.be/BaW_jenozKc", Platform.YOUTUBE, "BaW_jenozKc"),
        (
            "https://m.youtube.com/shorts/BaW_jenozKc?feature=share",
            Platform.YOUTUBE,
            "BaW_jenozKc",
        ),
        (
            "https://www.bilibili.com/video/BV1xx411c7mD/",
            Platform.BILIBILI,
            "BV1xx411c7mD",
        ),
    ],
)
def test_normalize_allowed_urls(raw: str, platform: Platform, video_id: str) -> None:
    result = normalize_video_url(raw)
    assert result.platform == platform
    assert result.video_id == video_id
    assert result.url.startswith("https://")


@pytest.mark.parametrize(
    "raw",
    [
        "http://www.youtube.com/watch?v=BaW_jenozKc",
        "https://youtube.com.evil.example/watch?v=BaW_jenozKc",
        "https://127.0.0.1/watch?v=BaW_jenozKc",
        "https://user:pass@youtube.com/watch?v=BaW_jenozKc",
        "https://youtube.com:8443/watch?v=BaW_jenozKc",
    ],
)
def test_reject_unsafe_urls(raw: str) -> None:
    with pytest.raises(URLSafetyError):
        normalize_video_url(raw)


def test_reject_playlist() -> None:
    with pytest.raises(URLSafetyError) as caught:
        normalize_video_url("https://www.youtube.com/watch?v=BaW_jenozKc&list=PL123456789")
    assert caught.value.code == ErrorCode.PLAYLIST_NOT_SUPPORTED


@pytest.mark.asyncio
async def test_dns_rejects_private_or_mixed_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.1.1", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(URLSafetyError, match="non-public"):
        await assert_public_dns("www.youtube.com")


@pytest.mark.asyncio
async def test_dns_benchmark_proxy_requires_explicit_cidr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.30", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(URLSafetyError, match="non-public"):
        await assert_public_dns("www.youtube.com")
    assert await assert_public_dns("www.youtube.com", "198.18.0.0/15") == ("198.18.0.30",)


def test_redirect_allowlist() -> None:
    allowed = validate_redirect(
        "https://www.youtube.com/watch?v=BaW_jenozKc",
        "https://rr1---sn.example.googlevideo.com/videoplayback",
        Platform.YOUTUBE,
    )
    assert "googlevideo.com" in allowed
    with pytest.raises(URLSafetyError):
        validate_redirect(
            "https://www.youtube.com/watch?v=BaW_jenozKc",
            "https://169.254.169.254/latest/meta-data",
            Platform.YOUTUBE,
        )


def test_runtime_allowlists_are_platform_specific() -> None:
    assert runtime_host_allowed(Platform.YOUTUBE, "https://i.ytimg.com/x.jpg")
    assert not runtime_host_allowed(Platform.BILIBILI, "https://i.ytimg.com/x.jpg")
