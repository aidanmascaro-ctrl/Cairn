"""Extract representative frames from a clip for Claude to view (B-roll tagging)."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path


def frame_timestamps(duration: float, count: int = 5) -> list[float]:
    if duration <= 0:
        raise ValueError("duration must be > 0")
    if count < 1:
        raise ValueError("count must be >= 1")
    if count == 1:
        return [duration / 2]
    inset = min(0.1, duration * 0.02)
    span = duration - 2 * inset
    return [round(inset + span * i / (count - 1), 3) for i in range(count)]


def extract_frames(
    file_path: str | Path,
    timestamps: Iterable[float],
    out_dir: str | Path,
    ffmpeg: str = "ffmpeg",
) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, ts in enumerate(timestamps):
        out = out_dir / f"frame_{i:02d}.jpg"
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-ss",
                str(ts),
                "-i",
                str(file_path),
                "-frames:v",
                "1",
                "-q:v",
                "4",
                str(out),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        paths.append(out)
    return paths
