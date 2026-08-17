from __future__ import annotations

from pathlib import Path
from typing import Any

from video_evidence_mcp.schemas import OCRCue


class OCRProcessor:
    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self.engine = RapidOCR()

    def scan(self, frames: list[tuple[Path, float]]) -> list[OCRCue]:
        cues: list[OCRCue] = []
        for path, timestamp in frames:
            result = self.engine(str(path))
            rows: Any = result[0] if isinstance(result, tuple) else result
            if not rows:
                continue
            texts: list[str] = []
            confidences: list[float] = []
            for row in rows:
                if len(row) < 3:
                    continue
                text = str(row[1]).strip()
                confidence = float(row[2])
                if text and confidence >= 0.45:
                    texts.append(text)
                    confidences.append(confidence)
            if texts:
                cues.append(
                    OCRCue(
                        timestamp_seconds=timestamp,
                        text=" | ".join(texts)[:2000],
                        confidence=round(sum(confidences) / len(confidences), 6),
                    )
                )
        return cues
