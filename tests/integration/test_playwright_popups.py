from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from video_evidence_mcp.adapters import BilibiliAdapter, YouTubeAdapter

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("fixture", "adapter", "removed_selector"),
    [
        ("youtube_login.html", YouTubeAdapter(), "#overlay"),
        ("youtube_consent.html", YouTubeAdapter(), "#consent"),
        ("bilibili_app.html", BilibiliAdapter(), "#appPrompt"),
        ("bilibili_login_iframe.html", BilibiliAdapter(), "iframe"),
    ],
)
async def test_dismiss_popup_fixture(fixture: str, adapter: object, removed_selector: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None,
        )
        page = await browser.new_page()
        await page.goto((FIXTURES / fixture).as_uri())
        actions = await adapter.dismiss_obstructions(page)  # type: ignore[attr-defined]
        assert any(action.result == "dismissed" for action in actions)
        assert await page.locator(removed_selector).count() == 0
        await browser.close()
