from __future__ import annotations

import math
import re

from video_evidence_mcp.schemas import TimelineEntry, TranscriptSegment

WORD_RE = re.compile(r"[\w\u3400-\u9fff]+", re.UNICODE)


def question_terms(question: str) -> set[str]:
    return {term.casefold() for term in WORD_RE.findall(question) if len(term) > 1}


def rank_transcript(
    segments: list[TranscriptSegment], question: str, limit: int = 24
) -> list[TranscriptSegment]:
    terms = question_terms(question)
    scored: list[tuple[float, TranscriptSegment]] = []
    for segment in segments:
        words = {word.casefold() for word in WORD_RE.findall(segment.text)}
        overlap = len(words & terms)
        score = min(1.0, overlap / max(len(terms), 1))
        enriched = segment.model_copy(update={"relevance": score})
        scored.append((score, enriched))
    if not scored:
        return []
    relevant = sorted(scored, key=lambda item: (-item[0], item[1].start_seconds))[:limit]
    if all(score == 0 for score, _segment in relevant):
        step = max(1, len(segments) // min(limit, len(segments)))
        return [
            segment.model_copy(update={"relevance": 0.0}) for segment in segments[::step][:limit]
        ]
    return sorted((segment for _score, segment in relevant), key=lambda item: item.start_seconds)


def build_timeline(
    segments: list[TranscriptSegment], duration: float, chapters: int = 8
) -> list[TimelineEntry]:
    if duration <= 0:
        return []
    count = min(chapters, max(1, math.ceil(duration / 300)))
    window = duration / count
    output: list[TimelineEntry] = []
    for index in range(count):
        start = index * window
        end = duration if index == count - 1 else (index + 1) * window
        texts = [
            segment.text
            for segment in segments
            if segment.end_seconds >= start and segment.start_seconds <= end
        ]
        excerpt = " ".join(texts)[:500]
        output.append(
            TimelineEntry(
                start_seconds=round(start, 3),
                end_seconds=round(end, 3),
                title=f"Timeline {index + 1}",
                summary=excerpt
                or "No transcript was available for this interval; inspect sampled frames.",
            )
        )
    return output
