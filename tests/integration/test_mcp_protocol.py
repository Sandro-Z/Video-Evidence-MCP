from __future__ import annotations

from pathlib import Path

import pytest
from mcp import Client

from video_evidence_mcp.config import Settings
from video_evidence_mcp.server import create_server


@pytest.mark.integration
async def test_initialize_and_strict_tool_schemas(tmp_path: Path) -> None:
    server, _runtime = create_server(
        Settings(data_dir=tmp_path, redis_url="redis://127.0.0.1:6379/15")
    )
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
        assert {tool.name for tool in tools} == {
            "search_videos",
            "start_video_analysis",
            "get_video_analysis",
            "get_video_window",
        }
        for tool in tools:
            assert tool.input_schema["additionalProperties"] is False
            assert tool.output_schema is not None
            assert tool.output_schema["additionalProperties"] is False
        rejected = await client.call_tool(
            "get_video_analysis", {"job_id": "missing12", "unexpected": True}
        )
        assert rejected.is_error is True
        missing = await client.call_tool(
            "get_video_analysis",
            {"job_id": "missing12", "include_contact_sheets": False},
        )
        assert missing.is_error is False
        assert missing.structured_content["error_code"] == "not_found"
