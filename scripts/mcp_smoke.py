#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import tempfile

from mcp import Client


async def run() -> None:
    with tempfile.TemporaryDirectory(prefix="video-evidence-mcp-smoke-") as directory:
        os.environ["DATA_DIR"] = directory
        os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/15"
        from video_evidence_mcp.config import Settings
        from video_evidence_mcp.server import create_server

        server, _runtime = create_server(Settings())
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
            names = sorted(tool.name for tool in tools)
            expected = sorted(
                ["search_videos", "start_video_analysis", "get_video_analysis", "get_video_window"]
            )
            if names != expected:
                raise SystemExit(f"tool mismatch: {names}")
            for tool in tools:
                if tool.input_schema.get("additionalProperties") is not False:
                    raise SystemExit(f"{tool.name} input schema permits extra fields")
                if (
                    tool.output_schema is None
                    or tool.output_schema.get("additionalProperties") is not False
                ):
                    raise SystemExit(f"{tool.name} output schema is not strict")
            print(json.dumps({"status": "ok", "tools": names}, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
