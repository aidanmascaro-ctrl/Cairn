"""Frame / seconds / timecode conversion. Non-drop-frame only (v1 = 25fps)."""

from __future__ import annotations


def _ifps(fps: float) -> int:
    if fps <= 0:
        raise ValueError("fps must be > 0")
    ifps = round(fps)
    if abs(fps - ifps) > 1e-6:
        raise ValueError(f"non-integer fps {fps} (drop-frame) not supported in v1")
    return ifps


def seconds_to_frames(seconds: float, fps: float) -> int:
    if seconds < 0:
        raise ValueError("seconds must be >= 0")
    if fps <= 0:
        raise ValueError("fps must be > 0")
    # round half up (Python's round() is banker's rounding: round(50.5) == 50)
    return int(seconds * fps + 0.5)


def frames_to_seconds(frames: int, fps: float) -> float:
    if fps <= 0:
        raise ValueError("fps must be > 0")
    return frames / fps


def timecode_to_frames(tc: str, fps: float) -> int:
    parts = tc.split(":")
    if len(parts) != 4:
        raise ValueError(f"bad timecode {tc!r}; expected HH:MM:SS:FF")
    hh, mm, ss, ff = (int(p) for p in parts)
    ifps = _ifps(fps)
    if not (0 <= ff < ifps):
        raise ValueError(f"frame {ff} out of range for {ifps}fps")
    return ((hh * 60 + mm) * 60 + ss) * ifps + ff


def frames_to_timecode(frames: int, fps: float) -> str:
    if frames < 0:
        raise ValueError("frames must be >= 0")
    ifps = _ifps(fps)
    ff = frames % ifps
    total = frames // ifps
    ss, mm, hh = total % 60, (total // 60) % 60, total // 3600
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"
