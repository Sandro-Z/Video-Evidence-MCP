from __future__ import annotations

import re

from video_evidence_mcp.adapters.base import PlatformAdapter
from video_evidence_mcp.schemas import Platform


class BilibiliAdapter(PlatformAdapter):
    platform = Platform.BILIBILI
    stable_selectors = (
        ("close", "button[aria-label='关闭']"),
        ("close", ".bili-mini-close-icon"),
        ("close", ".login-panel-popover .close"),
        ("close_app_prompt", "[data-testid='open-app-close']"),
    )
    action_patterns = (
        ("close", re.compile(r"^(close|关闭|關閉|×)$", re.I)),  # noqa: RUF001
        ("cancel", re.compile(r"^(cancel|取消)$", re.I)),
        ("continue_browsing", re.compile(r"继续浏览|继续观看|continue browsing", re.I)),
        ("not_now", re.compile(r"暂不登录|以后再说|稍后|not now", re.I)),
        ("close_app_prompt", re.compile(r"暂不打开|网页版继续|继续使用网页|stay on web", re.I)),
        ("reject_optional_cookies", re.compile(r"拒绝(?:全部|非必要)|reject optional", re.I)),
    )
