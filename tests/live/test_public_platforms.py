from __future__ import annotations

import os

import pytest

from video_evidence_mcp.pipeline.metadata import fetch_metadata

pytestmark = pytest.mark.live


@pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="live tests are opt-in")
@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        "https://www.bilibili.com/video/BV15pXvY8Ed5",
    ],
)
async def test_anonymous_metadata(url: str) -> None:
    metadata = await fetch_metadata(url)
    assert metadata.title
    assert not metadata.is_live
