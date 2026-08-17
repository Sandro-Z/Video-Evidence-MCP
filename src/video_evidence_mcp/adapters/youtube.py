from __future__ import annotations

import re

from video_evidence_mcp.adapters.base import PlatformAdapter
from video_evidence_mcp.schemas import Platform


class YouTubeAdapter(PlatformAdapter):
    platform = Platform.YOUTUBE
    stable_selectors = (
        ("close", "button[aria-label='Close']"),
        ("close", "button[aria-label='关闭']"),
        ("reject_optional_cookies", "button[aria-label*='Reject']"),
    )
    action_patterns = (
        ("close", re.compile(r"^(close|关闭|關閉|×)$", re.I)),  # noqa: RUF001
        ("cancel", re.compile(r"^(cancel|取消)$", re.I)),
        (
            "continue_without_login",
            re.compile(r"continue (?:without signing in|as guest)|继续浏览|暫不登入", re.I),
        ),
        ("not_now", re.compile(r"^(not now|no thanks|稍后|暂不登录|不用了)$", re.I)),
        (
            "reject_optional_cookies",
            re.compile(r"reject (?:all|optional)|拒绝(?:全部|非必要)", re.I),
        ),
    )
