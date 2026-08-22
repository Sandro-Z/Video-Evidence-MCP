from __future__ import annotations

from pathlib import Path

import pytest

from video_evidence_mcp.pipeline import transcript
from video_evidence_mcp.pipeline.transcript import (
    download_captions,
    obtain_transcript,
    parse_subtitle_text,
)
from video_evidence_mcp.schemas import TranscriptSegment


def test_parse_vtt_and_deduplicate() -> None:
    parsed = parse_subtitle_text(
        """WEBVTT

00:00:01.000 --> 00:00:03.000
<c>Hello &amp; welcome</c>

00:00:03.000 --> 00:00:04.000
<c>Hello &amp; welcome</c>

00:00:04.000 --> 00:00:06.500
第二段字幕
"""
    )
    assert [segment.text for segment in parsed] == ["Hello & welcome", "第二段字幕"]
    assert parsed[1].end_seconds == 6.5


def test_asr_fallback_runs_when_captions_missing(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    calls: list[str] = []

    def captions(
        _url: str, _directory: Path, _languages: list[str]
    ) -> tuple[list[TranscriptSegment], str, str | None, list[str]]:
        return [], "none", None, ["no captions"]

    def audio_fetcher(_url: str, _directory: Path, _limit: int) -> Path:
        calls.append("audio")
        return audio

    def asr(
        _audio: Path, _model: str, _device: str, _compute: str, _language: str | None
    ) -> tuple[list[TranscriptSegment], str | None, str]:
        calls.append("asr")
        return [TranscriptSegment(start_seconds=0, end_seconds=1, text="hello")], "en", "ok"

    segments, source, language, confidence, warnings = obtain_transcript(
        "https://example.invalid/video",
        tmp_path,
        ["en"],
        1024,
        "tiny",
        "cpu",
        "int8",
        caption_fetcher=captions,
        audio_fetcher=audio_fetcher,
        asr=asr,
    )
    assert calls == ["audio", "asr"]
    assert segments[0].text == "hello"
    assert (source, language, confidence) == ("asr", "en", "ok")
    assert warnings == ["no captions"]


def test_audio_fallback_can_be_deferred_until_video_is_downloaded(tmp_path: Path) -> None:
    def captions(
        _url: str, _directory: Path, _languages: list[str]
    ) -> tuple[list[TranscriptSegment], str, str | None, list[str]]:
        return [], "none", None, ["captions unavailable"]

    def unexpected_audio_fetch(_url: str, _directory: Path, _limit: int) -> Path:
        raise AssertionError("audio should be reused from the later video download")

    result = obtain_transcript(
        "https://example.invalid/video",
        tmp_path,
        ["en"],
        1024,
        "tiny",
        "cpu",
        "int8",
        caption_fetcher=captions,
        audio_fetcher=unexpected_audio_fetch,
        allow_audio_fallback=False,
    )

    assert result == (
        [],
        "none",
        None,
        "No transcript could be produced yet",
        ["captions unavailable"],
    )


def test_caption_bot_challenge_returns_actionable_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeYoutubeDL:
        def __init__(self, _options: dict[str, object]) -> None:
            pass

        def __enter__(self) -> FakeYoutubeDL:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def extract_info(self, _url: str, *, download: bool) -> None:
            assert download
            raise RuntimeError("Sign in to confirm you're not a bot")

    monkeypatch.setattr(transcript, "YoutubeDL", FakeYoutubeDL)

    segments, source, language, warnings = download_captions(
        "https://www.youtube.com/watch?v=jNQXAC9IVRw", tmp_path, ["en"]
    )

    assert (segments, source, language) == ([], "none", None)
    assert warnings == [
        "YouTube blocked anonymous caption access with bot verification; trying ASR"
    ]
