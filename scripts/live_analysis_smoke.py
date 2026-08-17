#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp import Client

URLS = {
    "youtube": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    "bilibili": "https://www.bilibili.com/video/BV15pXvY8Ed5",
}


def structured(result: Any) -> dict[str, Any]:
    if isinstance(result.structured_content, dict):
        return result.structured_content
    for block in result.content:
        if getattr(block, "type", None) == "text":
            value = json.loads(block.text)
            if isinstance(value, dict):
                return value
    raise RuntimeError("tool response had no structured object")


async def analyze(client: Client, platform: str, url: str, timeout: float) -> dict[str, Any]:
    started = structured(
        await client.call_tool(
            "start_video_analysis",
            {
                "url": url,
                "question": "What does this video say and visibly show?",
                "depth": "quick",
                "transcript_languages": ["en", "zh"],
                "max_frames": 4,
                "force_refresh": True,
            },
        )
    )
    if not started.get("ok"):
        return {"url": url, "status": "failed_to_start", "response": started}
    job_id = str(started["job_id"])
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        raw = await client.call_tool(
            "get_video_analysis",
            {"job_id": job_id, "include_contact_sheets": True},
        )
        response = structured(raw)
        if response.get("status") == "completed" and response.get("evidence"):
            evidence = response["evidence"]
            metadata = evidence["metadata"]
            duration = float(metadata["duration_seconds"] or 0)
            analysis_id = str(evidence["analysis_id"])
            window_summary: dict[str, Any] | None = None
            if duration > 0:
                window_raw = await client.call_tool(
                    "get_video_window",
                    {
                        "analysis_id": analysis_id,
                        "start_seconds": 0,
                        "end_seconds": min(duration, 10),
                        "question": "What is said and visibly shown in this opening window?",
                        "include_frames": True,
                        "include_transcript": True,
                    },
                )
                window = structured(window_raw)
                window_evidence = window.get("evidence") or {}
                window_summary = {
                    "status": window.get("status"),
                    "transcript_segments": len(window_evidence.get("transcript", [])),
                    "frame_timestamps": window_evidence.get("frame_timestamps", []),
                    "image_blocks": sum(
                        1 for block in window_raw.content if getattr(block, "type", None) == "image"
                    ),
                    "warnings": window.get("warnings", []),
                }
            return {
                "url": url,
                "status": "passed",
                "job_id": job_id,
                "analysis_id": analysis_id,
                "title": metadata["title"],
                "video_id": metadata["video_id"],
                "duration_seconds": duration,
                "transcript_source": evidence["transcript_source"],
                "transcript_segments": len(evidence["relevant_transcript"]),
                "frame_timestamps": evidence["frame_timestamps"],
                "ocr_cues": len(evidence["ocr_cues"]),
                "popup_actions": evidence["popup_actions"],
                "image_blocks": sum(
                    1 for block in raw.content if getattr(block, "type", None) == "image"
                ),
                "window": window_summary,
                "warnings": response.get("warnings", []),
            }
        if response.get("status") == "failed":
            return {
                "url": url,
                "status": "failed",
                "job_id": job_id,
                "error_code": response.get("error_code"),
                "warnings": response.get("warnings", []),
            }
        await asyncio.sleep(max(float(response.get("retry_after_ms", 2000)) / 1000, 0.5))
    return {"url": url, "status": "timeout", "job_id": job_id}


async def run() -> int:
    endpoint = os.getenv("MCP_URL", "http://127.0.0.1:8787/mcp")
    timeout = float(os.getenv("LIVE_ANALYSIS_TIMEOUT_SECONDS", "1200"))
    report: dict[str, Any] = {
        "tested_at": datetime.now(UTC).isoformat(),
        "mcp_url": endpoint,
        "results": {},
    }
    for platform, url in URLS.items():
        try:
            # A fresh Streamable HTTP session per platform keeps a transient
            # client-session failure from obscuring the independent server job.
            async with Client(endpoint) as client:
                report["results"][platform] = await analyze(client, platform, url, timeout)
        except Exception as exc:
            report["results"][platform] = {
                "url": url,
                "status": "client_error",
                "error_class": type(exc).__name__,
                "error": str(exc)[:1000],
            }
    target_dir = Path("test-results")
    target_dir.mkdir(exist_ok=True)
    target = target_dir / "live-analysis-smoke.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(target.read_text(encoding="utf-8"))
    return sum(item["status"] != "passed" for item in report["results"].values())


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
