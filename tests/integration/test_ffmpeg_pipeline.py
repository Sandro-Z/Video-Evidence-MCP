from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from video_evidence_mcp.pipeline.frames import (
    detect_scene_timestamps,
    distributed_timestamps,
    extract_distributed_frames,
    probe_duration,
)


@pytest.mark.integration
def test_ffmpeg_probe_and_extract(tmp_path: Path) -> None:
    media = tmp_path / "test.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x180:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:d=1",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(media),
        ],
        check=True,
    )
    duration = probe_duration(media)
    assert 1.8 <= duration <= 2.2
    timestamps = distributed_timestamps(duration, 4)
    frames = extract_distributed_frames(media, tmp_path / "frames", timestamps)
    assert len(frames) == 4
    assert detect_scene_timestamps(media, duration, 4)
