from __future__ import annotations

from pathlib import Path

from video_evidence_mcp.pipeline.ocr import OCRProcessor


class FakeEngine:
    def __call__(self, _path: str) -> tuple[list[list[object]], float]:
        return (
            [
                [[], "Hello", 0.9],
                [[], "低置信", 0.2],
                [[], "数字 42", 0.8],
            ],
            0.01,
        )


def test_ocr_filters_low_confidence(tmp_path: Path) -> None:
    processor = object.__new__(OCRProcessor)
    processor.engine = FakeEngine()  # type: ignore[attr-defined]
    cue = processor.scan([(tmp_path / "frame.jpg", 12.5)])[0]
    assert cue.timestamp_seconds == 12.5
    assert cue.text == "Hello | 数字 42"
    assert cue.confidence == 0.85
