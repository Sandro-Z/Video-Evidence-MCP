from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

LOGGER = logging.getLogger("video_evidence_mcp.yt_dlp")

_YOUTUBE_BOT_CHALLENGE_MARKERS = (
    "sign in to confirm you're not a bot",
    "login_required",
)


class YTDLPLogger:
    """Route yt-dlp diagnostics through the application's redacting logger."""

    def debug(self, message: str) -> None:
        LOGGER.debug("yt-dlp %s", message)

    def warning(self, message: str) -> None:
        LOGGER.warning("yt-dlp %s", message)

    def error(self, message: str) -> None:
        LOGGER.error("yt-dlp %s", message)


YTDLP_LOGGER = YTDLPLogger()


def _youtube_cookie_stream(url: str | None) -> io.StringIO | None:
    configured = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    if not configured or not url:
        return None
    host = (urlsplit(url).hostname or "").lower()
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        return None
    path = Path(configured)
    try:
        if path.stat().st_size > 2 * 1024**2:
            raise RuntimeError("configured YouTube cookies file exceeds 2 MiB")
        # yt-dlp saves its cookie jar on close. Use an in-memory stream so the
        # mounted secret remains read-only and is never modified by a request.
        return io.StringIO(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError("configured YouTube cookies file could not be read") from exc


def common_ydl_options(url: str | None = None) -> dict[str, Any]:
    """Return safe defaults shared by every yt-dlp access path."""
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": False,
        "ignoreconfig": True,
        "noplaylist": True,
        "cachedir": False,
        "socket_timeout": 20,
        # Space yt-dlp's internal HTTP calls as recommended for YouTube guest sessions.
        "sleep_interval_requests": 1.0,
        "logger": YTDLP_LOGGER,
    }
    cookie_stream = _youtube_cookie_stream(url)
    if cookie_stream is not None:
        options["cookiefile"] = cookie_stream
    return options


def is_youtube_bot_challenge(error: BaseException) -> bool:
    """Recognize YouTube's deterministic anonymous-access challenge."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).casefold().replace(chr(0x2019), "'")
        if any(marker in message for marker in _YOUTUBE_BOT_CHALLENGE_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False
