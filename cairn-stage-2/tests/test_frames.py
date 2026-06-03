import shutil
import subprocess

import pytest
from helpers import frames


def test_frame_timestamps_count_and_order():
    ts = frames.frame_timestamps(100.0, count=5)
    assert len(ts) == 5
    assert ts == sorted(ts)
    assert ts[0] >= 0.0 and ts[-1] <= 100.0


def test_frame_timestamps_single():
    assert frames.frame_timestamps(10.0, count=1) == [5.0]


def test_frame_timestamps_invalid():
    with pytest.raises(ValueError):
        frames.frame_timestamps(0.0)
    with pytest.raises(ValueError):
        frames.frame_timestamps(10.0, count=0)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_extract_frames_writes_jpgs(tmp_path):
    clip = tmp_path / "test.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=25", str(clip)],
        capture_output=True,
        check=True,
    )
    out = frames.extract_frames(clip, [0.5, 1.5, 2.5], tmp_path / "frames")
    assert len(out) == 3
    assert all(p.exists() and p.stat().st_size > 0 for p in out)
