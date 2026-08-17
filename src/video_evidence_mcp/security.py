from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from video_evidence_mcp.schemas import ErrorCode, Platform

YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
BILIBILI_HOSTS = frozenset({"bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv"})
INPUT_HOSTS = YOUTUBE_HOSTS | BILIBILI_HOSTS
YOUTUBE_RUNTIME_HOST_SUFFIXES = (
    ".youtube.com",
    ".youtube-nocookie.com",
    ".googlevideo.com",
    ".ytimg.com",
    ".google.com",
    ".gstatic.com",
)
BILIBILI_RUNTIME_HOST_SUFFIXES = (
    ".bilibili.com",
    ".biligame.com",
    ".bilivideo.com",
    ".hdslb.com",
    ".b23.tv",
)
YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
BILI_ID_RE = re.compile(r"^(?:BV[0-9A-Za-z]{8,20}|av[0-9]{1,20})$", re.IGNORECASE)


class URLSafetyError(ValueError):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class NormalizedVideoURL:
    platform: Platform
    video_id: str
    url: str


def _canonical_host(hostname: str | None) -> str:
    if not hostname:
        raise URLSafetyError(ErrorCode.INVALID_URL, "URL must include a hostname")
    try:
        return hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise URLSafetyError(ErrorCode.INVALID_URL, "invalid internationalized hostname") from exc


def normalize_video_url(raw_url: str) -> NormalizedVideoURL:
    try:
        parsed = urlsplit(raw_url)
    except ValueError as exc:
        raise URLSafetyError(ErrorCode.INVALID_URL, "malformed URL") from exc
    if parsed.scheme != "https":
        raise URLSafetyError(ErrorCode.UNSAFE_URL, "only HTTPS video URLs are accepted")
    if parsed.username or parsed.password:
        raise URLSafetyError(ErrorCode.UNSAFE_URL, "userinfo in URLs is forbidden")
    if parsed.port not in {None, 443}:
        raise URLSafetyError(ErrorCode.UNSAFE_URL, "non-default ports are forbidden")
    host = _canonical_host(parsed.hostname)
    if host not in INPUT_HOSTS:
        raise URLSafetyError(ErrorCode.UNSUPPORTED_PLATFORM, "URL host is not an allowed platform")

    query = parse_qs(parsed.query, keep_blank_values=True)
    if any(key in query for key in ("list", "playlist", "index")):
        raise URLSafetyError(ErrorCode.PLAYLIST_NOT_SUPPORTED, "playlist URLs are not accepted")

    if host in YOUTUBE_HOSTS:
        if host == "youtu.be":
            video_id = parsed.path.strip("/").split("/", 1)[0]
        elif parsed.path == "/watch":
            values = query.get("v", [])
            video_id = values[0] if len(values) == 1 else ""
        elif parsed.path.startswith(("/shorts/", "/embed/")):
            video_id = parsed.path.strip("/").split("/", 1)[1].split("/", 1)[0]
        else:
            raise URLSafetyError(ErrorCode.INVALID_URL, "not a canonical YouTube video URL")
        if not YT_ID_RE.fullmatch(video_id):
            raise URLSafetyError(ErrorCode.INVALID_URL, "invalid YouTube video id")
        return NormalizedVideoURL(
            platform=Platform.YOUTUBE,
            video_id=video_id,
            url=f"https://www.youtube.com/watch?{urlencode({'v': video_id})}",
        )

    if host == "b23.tv":
        raise URLSafetyError(
            ErrorCode.INVALID_URL,
            "Bilibili short links must first be resolved to a bilibili.com/video URL",
        )
    match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{8,20}|av[0-9]{1,20})/?", parsed.path, re.I)
    if not match or not BILI_ID_RE.fullmatch(match.group(1)):
        raise URLSafetyError(ErrorCode.INVALID_URL, "not a canonical Bilibili video URL")
    video_id = match.group(1)
    return NormalizedVideoURL(
        platform=Platform.BILIBILI,
        video_id=video_id,
        url=f"https://www.bilibili.com/video/{video_id}",
    )


def _is_forbidden_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


async def assert_public_dns(
    hostname: str, trusted_proxy_cidr: str | None = None
) -> tuple[str, ...]:
    """Resolve every address once and reject mixed public/private answers."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.run_in_executor(
            None,
            lambda: socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM),
        )
    except socket.gaierror as exc:
        raise URLSafetyError(ErrorCode.UNSAFE_URL, f"DNS resolution failed for {hostname}") from exc
    addresses = tuple(sorted({str(item[4][0]) for item in infos}))
    trusted_proxy = (
        ipaddress.ip_network(trusted_proxy_cidr, strict=False) if trusted_proxy_cidr else None
    )
    forbidden = [
        address
        for address in addresses
        if _is_forbidden_ip(address)
        and (trusted_proxy is None or ipaddress.ip_address(address) not in trusted_proxy)
    ]
    if not addresses or forbidden:
        raise URLSafetyError(ErrorCode.UNSAFE_URL, "hostname resolves to a non-public address")
    return addresses


def runtime_host_allowed(platform: Platform, url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "data", "blob"}:
        return False
    if parsed.scheme in {"data", "blob"}:
        return True
    host = _canonical_host(parsed.hostname)
    suffixes = (
        YOUTUBE_RUNTIME_HOST_SUFFIXES
        if platform == Platform.YOUTUBE
        else BILIBILI_RUNTIME_HOST_SUFFIXES
    )
    return host in INPUT_HOSTS or any(host.endswith(suffix) for suffix in suffixes)


def validate_redirect(source_url: str, location: str, platform: Platform) -> str:
    target = urljoin(source_url, location)
    parsed = urlsplit(target)
    if parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise URLSafetyError(ErrorCode.UNSAFE_URL, "unsafe redirect authority")
    if not runtime_host_allowed(platform, target):
        raise URLSafetyError(ErrorCode.UNSAFE_URL, "redirect left the platform allowlist")
    return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))
