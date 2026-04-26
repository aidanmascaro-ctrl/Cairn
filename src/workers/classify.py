"""
Stage 2 — Classify worker.

Two-signal classifier: clip duration + face detection.

  1. Duration  (ffprobe, instant)
       < 60 s  → short clip  (B-roll territory)
       > 120 s → long clip   (A-roll territory)

  2. Face detection  (OpenCV Haar cascade, local, no cloud)
       Samples 5 frames spread across the clip.
       face_ratio = frames_with_face / frames_sampled

Decision:
  short + no face  → B-ROLL   (auto)
  long  + face     → A-ROLL   (auto)
  everything else  → AMBIGUOUS (user review: one-tap confirm)

Audio is intentionally NOT used.  The user keeps a live mic on the
interview subject while shooting B-roll, making any speech signal
unreliable as a primary classifier.

No network calls.  Runs entirely locally.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# ── Tunable thresholds ────────────────────────────────────────────────────────

BROLL_MAX_DURATION: float = 60.0  # clips shorter than this → B-roll leaning
AROLL_MIN_DURATION: float = 120.0  # clips longer than this → A-roll leaning
FACE_THRESHOLD: float = 0.40  # ≥40 % of sampled frames must contain a face
FACE_SAMPLE_COUNT: int = 5  # frames sampled per clip
FACE_MIN_SIZE: int = 50  # min face bounding-box edge (pixels) — filters noise

_VIDEO_EXTS: set[str] = {".mp4", ".mov", ".mxf", ".avi", ".m2ts"}

# ── Binary paths ──────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FFMPEG_PATH: Path = _PROJECT_ROOT / "assets" / "bin" / "ffmpeg"
if not FFMPEG_PATH.exists():
    FFMPEG_PATH = Path("ffmpeg")

FFPROBE_PATH: Path = _PROJECT_ROOT / "assets" / "bin" / "ffprobe"
if not FFPROBE_PATH.exists():
    FFPROBE_PATH = Path("ffprobe")

# ── OpenCV face cascade (lazy, module-level cache) ────────────────────────────

_face_cascade: Any = None


def _get_face_cascade() -> Any:
    global _face_cascade
    if _face_cascade is None:
        import cv2

        _face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
    return _face_cascade


# ── Duration ──────────────────────────────────────────────────────────────────


def get_duration(clip_path: str) -> float:
    """Return clip duration in seconds via ffprobe.  Returns 0.0 on failure."""
    try:
        proc = subprocess.run(
            [
                str(FFPROBE_PATH),
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                clip_path,
            ],
            capture_output=True,
            timeout=15,
        )
        if proc.returncode != 0:
            return 0.0
        data: dict[str, Any] = json.loads(proc.stdout)
        raw = data.get("format", {}).get("duration", "0")
        return float(raw)
    except Exception:
        return 0.0


# ── Frame extraction ──────────────────────────────────────────────────────────


def extract_frames(clip_path: str, duration_sec: float, tmp_dir: Path) -> list[Path]:
    """
    Extract FACE_SAMPLE_COUNT frames spread across the clip.

    Frames are written to tmp_dir (caller cleans up).  We sample at
    10 / 25 / 50 / 75 / 90 % to skip blank leader/trailer.  Frames are
    downscaled to 640 px wide for speed — sufficient for face detection.
    """
    if duration_sec <= 0:
        return []

    fractions = [0.10, 0.25, 0.50, 0.75, 0.90][:FACE_SAMPLE_COUNT]
    frames: list[Path] = []

    for i, frac in enumerate(fractions):
        ts = duration_sec * frac
        out = tmp_dir / f"frame_{i:02d}.jpg"
        proc = subprocess.run(
            [
                str(FFMPEG_PATH),
                "-y",
                "-ss",
                f"{ts:.3f}",
                "-i",
                clip_path,
                "-vframes",
                "1",
                "-q:v",
                "5",
                "-vf",
                "scale=640:-1",
                str(out),
            ],
            capture_output=True,
            timeout=30,
        )
        if proc.returncode == 0 and out.exists():
            frames.append(out)

    return frames


# ── Face detection ────────────────────────────────────────────────────────────


def count_face_frames(frame_paths: list[Path]) -> int:
    """
    Return the number of frames that contain at least one detected face.
    Uses the OpenCV frontal-face Haar cascade — fast, local, no model download.
    """
    import cv2

    cascade = _get_face_cascade()
    count = 0

    for frame_path in frame_paths:
        img = cv2.imread(str(frame_path))
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(FACE_MIN_SIZE, FACE_MIN_SIZE),
        )
        if len(faces) > 0:
            count += 1

    return count


# ── Classification decision ───────────────────────────────────────────────────


def classify_clip(duration_sec: float, face_ratio: float) -> str:
    """
    Return 'A-ROLL', 'B-ROLL', or 'AMBIGUOUS'.

    Rules come directly from the user's shooting style:
      - B-roll: 5 s - ~1 min, no subject in frame
      - A-roll: 1 min - 30 min, subject talking to camera
    """
    face_present = face_ratio >= FACE_THRESHOLD

    if duration_sec < BROLL_MAX_DURATION and not face_present:
        return "B-ROLL"

    if duration_sec >= AROLL_MIN_DURATION and face_present:
        return "A-ROLL"

    return "AMBIGUOUS"


# ── Clip scanner ──────────────────────────────────────────────────────────────


def scan_video_clips(shoot_folder: str | Path) -> list[str]:
    """Return sorted absolute paths of all video clips under 01_VIDEOS/."""
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
    Classify every clip.  Yields one event dict per clip plus a final summary.

    Idempotent: skips clips whose sidecar already has 'classify' in
    stages_complete.  Sidecar is written before the per-clip event is yielded.
    """
    import sidecar as sid

    counts: dict[str, int] = {"a_roll": 0, "b_roll": 0, "ambiguous": 0, "skipped": 0}

    for clip_path in clip_paths:
        existing = sid.read_clip(shoot_folder, clip_path)
        if existing and "classify" in existing.get("stages_complete", []):
            label = existing.get("classification", "UNKNOWN")
            _tally(counts, label, skipped=True)
            meta = existing.get("classification_meta", {})
            yield {
                "event": "clip_skipped",
                "file": clip_path,
                "label": label,
                "duration_sec": meta.get("duration_sec", 0.0),
                "face_ratio": meta.get("face_ratio", 0.0),
            }
            continue

        tmp_dir = Path(tempfile.mkdtemp(prefix="cairn-classify-"))
        try:
            duration_sec = get_duration(clip_path)

            frames = extract_frames(clip_path, duration_sec, tmp_dir)
            face_frame_count = count_face_frames(frames) if frames else 0
            face_ratio = face_frame_count / len(frames) if frames else 0.0

            label = classify_clip(duration_sec, face_ratio)
            _tally(counts, label)

            classification_meta: dict[str, Any] = {
                "duration_sec": round(duration_sec, 2),
                "face_ratio": round(face_ratio, 3),
                "face_frames": face_frame_count,
                "frames_sampled": len(frames),
                "classifier_version": "v2-duration-face",
            }

            sid.merge_clip(
                shoot_folder,
                clip_path,
                updates={"classification": label, "classification_meta": classification_meta},
                new_stage="classify",
            )

            yield {
                "event": "clip_classified",
                "file": clip_path,
                "label": label,
                "duration_sec": round(duration_sec, 2),
                "face_ratio": round(face_ratio, 3),
            }

        except Exception as exc:
            yield {
                "event": "clip_error",
                "file": clip_path,
                "reason": str(exc)[:200],
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
