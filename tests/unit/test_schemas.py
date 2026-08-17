from __future__ import annotations

import pytest
from pydantic import ValidationError

from video_evidence_mcp.schemas import (
    GetVideoWindowInput,
    Platform,
    SearchVideosInput,
    StartVideoAnalysisInput,
)


def test_all_inputs_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError, match="extra"):
        SearchVideosInput(query="test", platforms=[Platform.YOUTUBE], surprise=True)
    with pytest.raises(ValidationError, match="extra"):
        StartVideoAnalysisInput(
            url="https://www.youtube.com/watch?v=BaW_jenozKc",
            question="what happens?",
            surprise=True,
        )


def test_window_is_ordered() -> None:
    with pytest.raises(ValidationError, match="greater"):
        GetVideoWindowInput(
            analysis_id="12345678",
            start_seconds=10,
            end_seconds=10,
            question="what?",
        )


def test_search_date_order() -> None:
    with pytest.raises(ValidationError, match="published_after"):
        SearchVideosInput(
            query="test",
            platforms=[Platform.YOUTUBE],
            published_after="2026-08-02T00:00:00Z",
            published_before="2026-08-01T00:00:00Z",
        )
