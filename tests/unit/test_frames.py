from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from video_evidence_mcp.pipeline.frames import (
    VIDEO_FORMAT_SELECTOR,
    _retry_sleep,
    distributed_timestamps,
    download_video,
    make_contact_sheet,
    stamp_frame,
)


def test_distributed_timestamps_cover_video() -> None:
    values = distributed_timestamps(100, 10)
    assert len(values) == 10
    assert values[0] < 10
    assert 45 < values[4] < 55
    assert values[-1] > 90


def test_stamp_and_contact_sheet(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    Image.new("RGB", (640, 360), "navy").save(source)
    frames: list[tuple[Path, float]] = []
    for index in range(3):
        target = tmp_path / f"stamped-{index}.jpg"
        frames.append((stamp_frame(source, index * 10.0, target), index * 10.0))
    sheet = make_contact_sheet(frames, tmp_path / "sheet.webp", columns=2)
    assert sheet.stat().st_size > 100
    with Image.open(sheet) as image:
        assert image.width == 640


def test_download_format_accepts_portrait_and_unknown_filesize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class FakeYoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            captured.update(options)

        def __enter__(self) -> FakeYoutubeDL:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def extract_info(self, _url: str, *, download: bool) -> None:
            assert download
            (tmp_path / "video.mp4").write_bytes(b"media")

    monkeypatch.setattr("video_evidence_mcp.pipeline.frames.YoutubeDL", FakeYoutubeDL)
    result = download_video("https://example.invalid/video", tmp_path, 1024)
    assert result.name == "video.mp4"
    assert captured["format"] == VIDEO_FORMAT_SELECTOR
    assert "filesize" not in str(captured["format"])
    assert "width<=1280" in str(captured["format"])
    assert captured["max_filesize"] == 1024
    assert captured["retries"] == 10
    assert captured["fragment_retries"] == 10
    assert _retry_sleep(n=0) == 1.0
    assert _retry_sleep(n=10) == 20.0
