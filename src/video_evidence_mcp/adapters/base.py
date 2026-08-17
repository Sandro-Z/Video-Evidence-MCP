from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from video_evidence_mcp.schemas import Platform, PopupAction
from video_evidence_mcp.security import runtime_host_allowed

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page, Request


class PlatformAdapter:
    platform: Platform
    action_patterns: tuple[tuple[str, re.Pattern[str]], ...] = ()
    stable_selectors: tuple[tuple[str, str], ...] = ()

    async def install_network_guard(self, page: Page) -> None:
        async def guard(route: object, request: Request) -> None:
            if request.resource_type in {
                "document",
                "xhr",
                "fetch",
                "media",
                "script",
                "stylesheet",
                "image",
            } and not runtime_host_allowed(self.platform, request.url):
                await route.abort("blockedbyclient")  # type: ignore[attr-defined]
                return
            await route.continue_()  # type: ignore[attr-defined]

        await page.route("**/*", guard)

    async def dismiss_obstructions(self, page: Page) -> list[PopupAction]:
        actions: list[PopupAction] = []
        for _pass in range(2):
            dismissed_this_pass = False
            for frame in list(page.frames):
                if await self._dismiss_in_frame(frame, actions):
                    dismissed_this_pass = True
                    await page.wait_for_timeout(120)
            if not dismissed_this_pass:
                break
        try:
            await page.keyboard.press("Escape")
            actions.append(self._action("press_escape", "keyboard", "dismissed"))
        except Exception:
            actions.append(self._action("press_escape", "keyboard", "failed"))
        return actions

    async def _dismiss_in_frame(self, frame: Frame, actions: list[PopupAction]) -> bool:
        for action_name, selector in self.stable_selectors:
            locator = frame.locator(selector).first
            try:
                if await locator.is_visible(timeout=250):
                    await locator.click(timeout=1000)
                    actions.append(self._action(action_name, f"css:{selector}", "dismissed"))
                    return True
            except Exception:
                actions.append(self._action(action_name, f"css:{selector}", "failed"))
        for action_name, pattern in self.action_patterns:
            for role in ("button", "link"):
                locator = frame.get_by_role(role, name=pattern).first
                try:
                    if await locator.is_visible(timeout=250):
                        await locator.click(timeout=1000)
                        actions.append(
                            self._action(
                                action_name, f"role:{role}/name:{pattern.pattern}", "dismissed"
                            )
                        )
                        return True
                except Exception:
                    actions.append(
                        self._action(action_name, f"role:{role}/name:{pattern.pattern}", "failed")
                    )
        return False

    def _action(self, action: str, strategy: str, result: str) -> PopupAction:
        return PopupAction(
            platform=self.platform,
            action=action,
            match_strategy=strategy,
            result=result,  # type: ignore[arg-type]
            timestamp=datetime.now(UTC),
        )
