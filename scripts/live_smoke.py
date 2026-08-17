#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from video_evidence_mcp.pipeline.metadata import fetch_metadata

URLS = {
    "youtube": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    "bilibili": "https://www.bilibili.com/video/BV15pXvY8Ed5",
}


async def run() -> int:
    output: dict[str, object] = {"tested_at": datetime.now(UTC).isoformat(), "results": {}}
    failures = 0
    for platform, url in URLS.items():
        try:
            metadata = await fetch_metadata(url)
            output["results"][platform] = {  # type: ignore[index]
                "url": url,
                "status": "passed",
                "video_id": metadata.video_id,
                "title": metadata.title,
                "duration_seconds": metadata.duration_seconds,
                "availability": metadata.availability,
            }
        except Exception as exc:
            failures += 1
            output["results"][platform] = {  # type: ignore[index]
                "url": url,
                "status": "failed",
                "error_class": type(exc).__name__,
                "error": str(exc)[:1000],
                "reproduce": f"yt-dlp --verbose --skip-download '{url}'",
            }
    target_dir = Path("test-results")
    target_dir.mkdir(exist_ok=True)
    target = target_dir / "live-smoke.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(target.read_text(encoding="utf-8"))
    return failures


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
