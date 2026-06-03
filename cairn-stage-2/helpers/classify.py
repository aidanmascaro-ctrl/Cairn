"""A-roll vs B-roll via ffmpeg silencedetect speech ratio."""

from __future__ import annotations

import re
import subprocess

AROLL_THRESHOLD = 0.45  # speech_ratio >= this -> A-ROLL
BROLL_THRESHOLD = 0.10  # speech_ratio <= this -> B-ROLL

_START = re.compile(r"silence_start:\s*([0-9.]+)")
_END = re.compile(r"silence_end:\s*([0-9.]+)")


def parse_silence_ratio(stderr: str, duration: float) -> float:
    """speech_ratio = 1 - silent_time/duration, parsed from silencedetect stderr."""
    if duration <= 0:
        raise ValueError("duration must be > 0")
    starts = [float(x) for x in _START.findall(stderr)]
    ends = [float(x) for x in _END.findall(stderr)]
    silent = 0.0
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration
        silent += max(0.0, min(e, duration) - s)
    return max(0.0, min(1.0, 1.0 - silent / duration))


def classify(speech_ratio: float) -> str:
    if speech_ratio >= AROLL_THRESHOLD:
        return "A-ROLL"
    if speech_ratio <= BROLL_THRESHOLD:
        return "B-ROLL"
    return "AMBIGUOUS"


def probe_duration(file_path, ffprobe: str = "ffprobe") -> float:
    out = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nk=1:nw=1",
            str(file_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def run_silencedetect(
    file_path, ffmpeg: str = "ffmpeg", noise: str = "-30dB", min_silence: float = 0.5
) -> str:
    proc = subprocess.run(
        [
            ffmpeg,
            "-i",
            str(file_path),
            "-af",
            f"silencedetect=noise={noise}:d={min_silence}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stderr


def analyze_clip(file_path) -> dict:
    """Side-effectful convenience: probe + silencedetect + classify."""
    duration = probe_duration(file_path)
    ratio = parse_silence_ratio(run_silencedetect(file_path), duration)
    return {"duration": duration, "speech_ratio": ratio, "label": classify(ratio)}
