"""A-roll vs B-roll via ffmpeg silencedetect speech ratio."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

AROLL_THRESHOLD = 0.45  # speech_ratio >= this -> A-ROLL
BROLL_THRESHOLD = 0.10  # speech_ratio <= this -> B-ROLL

_EVENT = re.compile(r"silence_(start|end):\s*([0-9.]+)")


def parse_silence_ratio(stderr: str, duration: float) -> float:
    """speech_ratio = 1 - silent_time/duration, parsed from silencedetect stderr.

    Walks the start/end events in chronological order (a state machine) rather than
    pairing two separate lists positionally. This correctly handles the two cases
    ffmpeg's silencedetect leaves implicit:
      - a clip that opens in silence: only a `silence_end` is emitted (no start at 0),
      - a clip that ends in silence: a `silence_start` with no closing `silence_end`.
    Both biased B-roll toward A-ROLL under the old positional pairing.
    """
    if duration <= 0:
        raise ValueError("duration must be > 0")
    silent = 0.0
    open_at: float | None = None
    for kind, pos in ((m.group(1), float(m.group(2))) for m in _EVENT.finditer(stderr)):
        if kind == "start":
            if open_at is None:  # ignore a redundant second start
                open_at = pos
            continue
        # end event: an end with no preceding start means the clip opened silent (t=0).
        start = open_at if open_at is not None else 0.0
        silent += max(0.0, min(pos, duration) - start)
        open_at = None
    if open_at is not None:  # trailing silence ran to the end of the clip
        silent += max(0.0, duration - open_at)
    return max(0.0, min(1.0, 1.0 - silent / duration))


def classify(speech_ratio: float) -> str:
    if speech_ratio >= AROLL_THRESHOLD:
        return "A-ROLL"
    if speech_ratio <= BROLL_THRESHOLD:
        return "B-ROLL"
    return "AMBIGUOUS"


def probe_duration(file_path: str | Path, ffprobe: str = "ffprobe") -> float:
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
    raw = out.stdout.strip()
    try:
        duration = float(raw)
    except ValueError as exc:
        # Containers without a duration tag (raw MXF, fragmented MP4) print "N/A".
        # Fail loud rather than let a NaN/None duration flow into the speech-ratio math.
        raise ValueError(f"ffprobe returned no usable duration: {raw!r}") from exc
    if duration <= 0:
        raise ValueError(f"clip duration must be > 0, got {duration}")
    return duration


def run_silencedetect(
    file_path: str | Path,
    ffmpeg: str = "ffmpeg",
    noise: str = "-30dB",
    min_silence: float = 0.5,
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


def analyze_clip(file_path: str | Path) -> dict:
    """Side-effectful convenience: probe + silencedetect + classify."""
    duration = probe_duration(file_path)
    ratio = parse_silence_ratio(run_silencedetect(file_path), duration)
    return {"duration": duration, "speech_ratio": ratio, "label": classify(ratio)}
