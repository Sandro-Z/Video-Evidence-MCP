from __future__ import annotations

import io
from pathlib import Path

import pytest
from yt_dlp import YoutubeDL

from video_evidence_mcp.config import Settings
from video_evidence_mcp.pipeline.yt_dlp_support import (
    common_ydl_options,
    is_youtube_bot_challenge,
)


def test_common_options_keep_diagnostics_and_space_requests() -> None:
    options = common_ydl_options()

    assert options["no_warnings"] is False
    assert options["ignoreconfig"] is True
    assert options["sleep_interval_requests"] == 1.0
    assert options["logger"] is not None


def test_bot_challenge_is_recognized_through_exception_chain() -> None:
    try:
        try:
            raise RuntimeError("Sign in to confirm you're not a bot")
        except RuntimeError as exc:
            raise ValueError("caption retrieval failed") from exc
    except ValueError as error:
        assert is_youtube_bot_challenge(error)


def test_unrelated_download_error_is_not_a_bot_challenge() -> None:
    assert not is_youtube_bot_challenge(RuntimeError("HTTP Error 503: Service Unavailable"))


def test_cookie_file_is_loaded_in_memory_only_for_youtube(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_path = tmp_path / "youtube-cookies.txt"
    contents = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsecret\n"
    cookie_path.write_text(contents, encoding="utf-8")
    monkeypatch.setenv("YOUTUBE_COOKIES_FILE", str(cookie_path))

    youtube_options = common_ydl_options("https://www.youtube.com/watch?v=jNQXAC9IVRw")
    bilibili_options = common_ydl_options("https://www.bilibili.com/video/BV1xx411c7mD")

    cookie_stream = youtube_options["cookiefile"]
    assert isinstance(cookie_stream, io.StringIO)
    assert cookie_stream.getvalue() == contents
    with YoutubeDL(youtube_options) as ydl:
        assert any(cookie.name == "SID" for cookie in ydl.cookiejar)
    assert "cookiefile" not in bilibili_options
    assert cookie_path.read_text(encoding="utf-8") == contents


def test_cookie_setting_accepts_empty_or_existing_absolute_file(tmp_path: Path) -> None:
    cookie_path = tmp_path / "youtube-cookies.txt"
    cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    assert Settings(youtube_cookies_file="").youtube_cookies_file is None
    assert Settings(youtube_cookies_file=cookie_path).youtube_cookies_file == cookie_path
