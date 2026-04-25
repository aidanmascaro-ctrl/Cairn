"""
Stage 2 -- Classify worker.

Uses Silero VAD to label every video clip as A-ROLL, B-ROLL, or AMBIGUOUS.
Writes results to sidecar JSON before any Resolve API call.
No network calls -- runs entirely locally via ONNX runtime.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# ── Constants (single source of truth for tunable values) ─────────────────────

AROLL_THRESHOLD: float = 0.60  # speech_ratio >= this -> A-ROLL
BROLL_THRESHOLD: float = 0.05  # speech_ratio <= this -> B-ROLL
VAD_THRESHOLD: float = 0.5  # silero confidence cutoff
SAMPLING_RATE: int = 16_000
MODEL_NAME: str = "silero_vad"
MODEL_VERSION: str = "v6"

_VIDEO_EXTS: set[str] = {".mp4", ".mov", ".mxf", ".avi", ".m2ts"}

# ── ffmpeg ────────────────────────────────────────────────────────────────────

# Prefer vendored binary; fall back to system ffmpeg for dev.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FFMPEG_PATH: Path = _PROJECT_ROOT / "assets" / "bin" / "ffmpeg"
if not FFMPEG_PATH.exists():
    FFMPEG_PATH = Path("ffmpeg")

# ── Errors ────────────────────────────────────────────────────────────────────


class AudioExtractionError(Exception):
    """Raised when ffmpeg cannot extract audio from a clip."""


# ── Model cache ───────────────────────────────────────────────────────────────

_model: Any = None


def _get_model() -> Any:
    global _model
    if _model is None:
        from silero_vad import load_silero_vad

        _model = load_silero_vad()
    return _model


# ── Audio extraction ──────────────────────────────────────────────────────────


def extract_audio(clip_path: str, tmp_dir: Path) -> Path:
    """
    Decode clip audio to a temporary 16 kHz mono WAV for Silero VAD.
    Raises AudioExtractionError if ffmpeg exits non-zero.
    Caller is responsible for cleaning up tmp_dir.
    """
    clip_hash = hashlib.sha256(clip_path.encode()).hexdigest()[:12]
    out = tmp_dir / f"cairn-{clip_hash}.wav"
    proc = subprocess.run(
        [
            str(FFMPEG_PATH),
            "-y",
            "-i",
            clip_path,
            "-ac",
            "1",
            "-ar",
            str(SAMPLING_RATE),
            "-f",
            "wav",
            "-vn",
            str(out),
        ],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise AudioExtractionError(
            f"ffmpeg exited {proc.returncode}: " + proc.stderr.decode(errors="replace")[-300:]
        )
    return out


# ── VAD ───────────────────────────────────────────────────────────────────────


def run_vad(wav_path: Path) -> dict[str, Any]:
    """
    Run Silero VAD on a 16 kHz mono WAV.
    Returns speech_ratio (0.0-1.0), speech_segments (seconds), and model info.
    """
    from silero_vad import get_speech_timestamps, read_audio

    model = _get_model()
    wav = read_audio(str(wav_path), sampling_rate=SAMPLING_RATE)
    segments = get_speech_timestamps(
        wav, model, threshold=VAD_THRESHOLD, sampling_rate=SAMPLING_RATE
    )
    total_speech = sum(s["end"] - s["start"] for s in segments)
    speech_ratio = float(total_speech / len(wav)) if len(wav) > 0 else 0.0

    return {
        "speech_ratio": round(speech_ratio, 4),
        "speech_segments": [
            [round(s["start"] / SAMPLING_RATE, 3), round(s["end"] / SAMPLING_RATE, 3)]
            for s in segments
        ],
        "model": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "threshold_used": VAD_THRESHOLD,
        "audio_stream": True,
    }


# ── Classification decision ───────────────────────────────────────────────────


def classify_clip(speech_ratio: float) -> str:
    """Return 'A-ROLL', 'B-ROLL', or 'AMBIGUOUS' from speech_ratio."""
    if speech_ratio >= AROLL_THRESHOLD:
        return "A-ROLL"
    if speech_ratio <= BROLL_THRESHOLD:
        return "B-ROLL"
    return "AMBIGUOUS"


# ── Clip scanner ──────────────────────────────────────────────────────────────


def scan_video_clips(shoot_folder: str | Path) -> list[str]:
    """Return sorted absolute paths of all video clips in the organised folder."""
    folder = Path(shoot_folder)
    paths: list[str] = []
    for cam_dir in ("A_CAM", "B_CAM", "C_CAM"):
        target = folder / "01_VIDEOS" / cam_dir
        if target.is_dir():
            paths.extend(
                str(f)
                for f in sorted(target.iterdir())
                if f.is_file() and f.suffix.lower() in _VIDEO_EXTS
            )
    return paths


# ── Main pipeline ─────────────────────────────────────────────────────────────


def classify_all(
    clip_paths: list[str],
    shoot_folder: str | Path,
) -> Iterator[dict[str, Any]]:
    """
    Classify each clip. Yields one dict per clip, then a final summary dict.
    Idempotent: clips with 'classify' in stages_complete are skipped without
    re-running ffmpeg or VAD.
    Writes sidecar before yielding each clip event.
    """
    import sidecar as sid

    counts: dict[str, int] = {"a_roll": 0, "b_roll": 0, "ambiguous": 0, "skipped": 0}

    for clip_path in clip_paths:
        existing = sid.read_clip(shoot_folder, clip_path)
        if existing and "classify" in existing.get("stages_complete", []):
            label = existing.get("classification", "UNKNOWN")
            _tally(counts, label, skipped=True)
            yield {
                "event": "clip_skipped",
                "file": clip_path,
                "label": label,
                "speech_ratio": existing.get("vad", {}).get("speech_ratio", 0.0),
            }
            continue

        tmp_dir = Path(tempfile.mkdtemp(prefix="cairn-vad-"))
        try:
            try:
                wav_path = extract_audio(clip_path, tmp_dir)
                vad_result: dict[str, Any] = run_vad(wav_path)
            except AudioExtractionError as exc:
                vad_result = {
                    "speech_ratio": 0.0,
                    "speech_segments": [],
                    "model": MODEL_NAME,
                    "model_version": MODEL_VERSION,
                    "threshold_used": VAD_THRESHOLD,
                    "audio_stream": False,
                    "error": str(exc)[:200],
                }

            label = classify_clip(vad_result["speech_ratio"])
            _tally(counts, label)

            sid.merge_clip(
                shoot_folder,
                clip_path,
                updates={"classification": label, "vad": vad_result},
                new_stage="classify",
            )

            yield {
                "event": "clip_classified",
                "file": clip_path,
                "label": label,
                "speech_ratio": vad_result["speech_ratio"],
                "audio_stream": vad_result.get("audio_stream", True),
            }
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    yield {
        "event": "classify_complete",
        "a_roll": counts["a_roll"],
        "b_roll": counts["b_roll"],
        "ambiguous": counts["ambiguous"],
        "skipped": counts["skipped"],
        "total": len(clip_paths),
    }


def _tally(counts: dict[str, int], label: str, *, skipped: bool = False) -> None:
    if skipped:
        counts["skipped"] += 1
    if label == "A-ROLL":
        counts["a_roll"] += 1
    elif label == "B-ROLL":
        counts["b_roll"] += 1
    elif label == "AMBIGUOUS":
        counts["ambiguous"] += 1
