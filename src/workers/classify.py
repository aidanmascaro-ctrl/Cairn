"""
Stage 2 — Classify worker.

KNN classifier trained on labeled examples from ~/.cairn/ground_truth.json.
Two features per clip:

  1. Face detection  (OpenCV Haar cascade, local, no cloud)
       Samples 5 frames spread across the clip.
       face_ratio = frames_with_face / frames_sampled

  2. Motion score  (mean normalised frame-to-frame absolute pixel diff)
       0 = completely static, 1 = chaotic movement

Decision:
  KNN lookup (k=5) → confident vote (>=70%)  → A-ROLL / B-ROLL
  KNN low confidence / no training data       → AMBIGUOUS (user review)
  Fallback when <5 examples: face>=threshold  → A-ROLL, else B-ROLL

Audio is intentionally NOT used.  The user keeps a live mic on the
interview subject while shooting B-roll, making any speech signal
unreliable as a primary classifier.

No network calls.  Runs entirely locally.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# ── Tunable thresholds ────────────────────────────────────────────────────────

FACE_THRESHOLD: float = 0.40  # rule-based fallback: >=40% frames with face
FACE_SAMPLE_COUNT: int = 5  # frames sampled per clip
FACE_MIN_SIZE: int = 50  # min face bounding-box edge (pixels) — filters noise

# KNN classifier (trained on ~/.cairn/ground_truth.json)
GROUND_TRUTH_PATH: Path = Path.home() / ".cairn" / "ground_truth.json"
KNN_K: int = 5
KNN_MIN_EXAMPLES: int = 5
KNN_CONFIDENCE_THRESHOLD: float = 0.70

_VIDEO_EXTS: set[str] = {".mp4", ".mov", ".mxf", ".avi", ".m2ts"}

# ── Binary paths ──────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _find_bin(name: str) -> Path:
    """Locate ffmpeg/ffprobe: bundled binary → Homebrew → system PATH."""
    candidates = [
        _PROJECT_ROOT / "assets" / "bin" / name,
        Path(f"/opt/homebrew/bin/{name}"),
        Path(f"/usr/local/bin/{name}"),
        Path(name),
    ]
    for p in candidates:
        try:
            if p.exists():
                return p
        except OSError:
            pass
    return Path(name)


FFMPEG_PATH: Path = _find_bin("ffmpeg")
FFPROBE_PATH: Path = _find_bin("ffprobe")

# ── OpenCV face cascade (lazy, module-level cache) ────────────────────────────

_face_cascade: Any = None
_gt_examples: list[dict[str, Any]] | None = None


def _get_face_cascade() -> Any:
    global _face_cascade
    if _face_cascade is None:
        import cv2

        _face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
    return _face_cascade


# ── Duration ──────────────────────────────────────────────────────────────────


def get_duration(clip_path: str) -> float | None:
    """Return clip duration in seconds via ffprobe, or None on failure."""
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
            return None
        data: dict[str, Any] = json.loads(proc.stdout)
        raw = data.get("format", {}).get("duration", "0")
        return float(raw)
    except Exception:
        return None


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


# ── Motion score ─────────────────────────────────────────────────────────────


def compute_motion_score(frame_paths: list[Path]) -> float:
    """
    Mean normalised frame-to-frame absolute pixel difference (0=static, 1=chaos).
    Reuses the frames already extracted for face detection.
    """
    import cv2
    import numpy as np

    grays: list[np.ndarray] = []
    for fp in frame_paths:
        img = cv2.imread(str(fp), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            grays.append(img.astype(np.float32) / 255.0)

    if len(grays) < 2:
        return 0.0

    diffs = [float(np.mean(np.abs(grays[i] - grays[i - 1]))) for i in range(1, len(grays))]
    return round(float(np.mean(diffs)), 4)


# ── KNN classifier ────────────────────────────────────────────────────────────


def _load_ground_truth() -> list[dict[str, Any]]:
    """Load enriched clips from ground truth JSON. Cached for the process lifetime."""
    global _gt_examples
    if _gt_examples is not None:
        return _gt_examples
    if not GROUND_TRUTH_PATH.exists():
        _gt_examples = []
        return _gt_examples
    try:
        with open(GROUND_TRUTH_PATH) as f:
            data: dict[str, Any] = json.load(f)
        _gt_examples = [
            c
            for c in data.get("clips", [])
            if "face_ratio" in c.get("features", {}) and "motion_score" in c.get("features", {})
        ]
    except Exception as exc:
        print(
            f"[classify.py] Failed to load ground truth from {GROUND_TRUTH_PATH}: {exc}",
            file=sys.stderr,
        )
        return []  # don't cache — allow retry on next clip
    return _gt_examples


def _knn_predict(face_ratio: float, motion_score: float) -> tuple[str, float] | None:
    """
    KNN prediction using face_ratio and motion_score.
    Returns (label, confidence) or None when insufficient data or confidence < threshold.
    """
    examples = _load_ground_truth()
    if len(examples) < KNN_MIN_EXAMPLES:
        return None

    all_face = [c["features"]["face_ratio"] for c in examples]
    all_motion = [c["features"]["motion_score"] for c in examples]
    face_rng = (max(all_face) - min(all_face)) or 1.0
    motion_rng = (max(all_motion) - min(all_motion)) or 1.0

    def _dist(c: dict[str, Any]) -> float:
        df = (c["features"]["face_ratio"] - face_ratio) / face_rng
        dm = (c["features"]["motion_score"] - motion_score) / motion_rng
        return df * df + dm * dm

    neighbours = sorted(examples, key=_dist)[:KNN_K]
    labels = [n["label"] for n in neighbours]
    top = max(set(labels), key=labels.count)
    confidence = labels.count(top) / KNN_K

    if confidence < KNN_CONFIDENCE_THRESHOLD:
        return None
    return top, confidence


# ── Classification decision ───────────────────────────────────────────────────


def classify_clip(
    duration_sec: float,
    face_ratio: float,
    motion_score: float = 0.0,
) -> str:
    """
    Return 'A-ROLL', 'B-ROLL', or 'AMBIGUOUS'.

    KNN lookup against labeled examples is tried first.
    Falls back to AMBIGUOUS when no trained data exists or confidence is low.
    Rule-based is only used when there are too few training examples.
    """
    knn = _knn_predict(face_ratio, motion_score)
    if knn is not None:
        return knn[0]

    # Not enough training data — use simple rule-based fallback
    if len(_load_ground_truth()) < KNN_MIN_EXAMPLES:
        if face_ratio >= FACE_THRESHOLD:
            return "A-ROLL"
        return "B-ROLL"

    # KNN had low confidence — flag for user review
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
                "motion_score": meta.get("motion_score", 0.0),
            }
            continue

        tmp_dir = Path(tempfile.mkdtemp(prefix="cairn-classify-"))
        try:
            duration_sec = get_duration(clip_path)
            if duration_sec is None:
                yield {
                    "event": "clip_error",
                    "file": clip_path,
                    "reason": "ffprobe failed — clip may be corrupt or unreadable",
                }
                continue

            frames = extract_frames(clip_path, duration_sec, tmp_dir)
            face_frame_count = count_face_frames(frames) if frames else 0
            face_ratio = face_frame_count / len(frames) if frames else 0.0
            motion_score = compute_motion_score(frames) if frames else 0.0

            label = classify_clip(duration_sec, face_ratio, motion_score)
            _tally(counts, label)

            classification_meta: dict[str, Any] = {
                "duration_sec": round(duration_sec, 2),
                "face_ratio": round(face_ratio, 3),
                "face_frames": face_frame_count,
                "frames_sampled": len(frames),
                "motion_score": motion_score,
                "classifier_version": "v3-knn-face-motion",
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
                "motion_score": motion_score,
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


def inspect_clip(clip_path: str) -> dict[str, Any]:
    """
    Re-analyse a clip and return annotated frame images for the visual inspector.

    Extracts the same 5 frames used during classification, runs face detection
    to get bounding boxes, computes per-frame motion diffs, then draws overlays
    (cyan face rectangles, motion intensity bar) onto each frame.

    Returns base64-encoded JPEG data URLs so the renderer can display them
    directly without writing any temp files to disk after the call returns.
    """
    import cv2
    import numpy as np

    duration_sec = get_duration(clip_path)
    if duration_sec is None:
        return {
            "ok": False,
            "duration_sec": 0.0,
            "face_ratio": 0.0,
            "motion_score": 0.0,
            "knn_label": None,
            "knn_confidence": None,
            "frames": [],
            "error": "ffprobe failed — clip may be corrupt or unreadable",
        }

    tmp_dir = Path(tempfile.mkdtemp(prefix="cairn-inspect-"))
    try:
        frames = extract_frames(clip_path, duration_sec, tmp_dir)
        if not frames:
            return {
                "ok": False,
                "duration_sec": round(duration_sec, 2),
                "face_ratio": 0.0,
                "motion_score": 0.0,
                "knn_label": None,
                "knn_confidence": None,
                "frames": [],
                "error": "No frames could be extracted from clip",
            }

        cascade = _get_face_cascade()
        fractions = [0.10, 0.25, 0.50, 0.75, 0.90][:FACE_SAMPLE_COUNT]

        grays: list[Any] = []
        raw: list[dict[str, Any]] = []

        for i, (frame_path, frac) in enumerate(zip(frames, fractions, strict=False)):
            img = cv2.imread(str(frame_path))
            if img is None:
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            grays.append(gray.astype(np.float32) / 255.0)

            faces = cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(FACE_MIN_SIZE, FACE_MIN_SIZE),
            )
            face_boxes = (
                [[int(x), int(y), int(w), int(h)] for x, y, w, h in faces] if len(faces) else []
            )
            raw.append(
                {
                    "frame_idx": i,
                    "timestamp_sec": round(duration_sec * frac, 2),
                    "face_boxes": face_boxes,
                    "img": img,
                }
            )

        # Per-frame motion diffs (first frame has no prior → 0.0)
        motion_diffs: list[float] = [0.0]
        for i in range(1, len(grays)):
            diff = float(np.mean(np.abs(grays[i] - grays[i - 1])))
            motion_diffs.append(round(diff, 4))
        max_motion = max(motion_diffs) if max(motion_diffs) > 0 else 1.0

        # Annotate frames and encode to base64 JPEG
        annotated: list[dict[str, Any]] = []
        for fd, motion_diff in zip(raw, motion_diffs, strict=False):
            img_ann = fd["img"].copy()
            img_h, img_w = img_ann.shape[:2]

            # Cyan face bounding boxes
            for x, y, bw, bh in fd["face_boxes"]:
                cv2.rectangle(img_ann, (x, y), (x + bw, y + bh), (255, 210, 0), 2)
                cv2.putText(
                    img_ann,
                    "face",
                    (x, max(y - 6, 14)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.38,
                    (255, 210, 0),
                    1,
                    cv2.LINE_AA,
                )

            # Motion intensity bar — 10px strip at bottom edge
            motion_norm = motion_diff / max_motion
            bar_h = 10
            bar_px = max(int(img_w * motion_norm), 0)
            if motion_norm < 0.33:
                bar_color = (60, 190, 80)  # green — low
            elif motion_norm < 0.67:
                bar_color = (60, 200, 210)  # amber — medium
            else:
                bar_color = (60, 80, 230)  # red — high
            cv2.rectangle(img_ann, (0, img_h - bar_h), (bar_px, img_h), bar_color, -1)

            # Timestamp label above the bar
            cv2.putText(
                img_ann,
                f"{fd['timestamp_sec']:.1f}s",
                (4, img_h - bar_h - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.32,
                (200, 200, 200),
                1,
                cv2.LINE_AA,
            )

            ok_enc, buf = cv2.imencode(".jpg", img_ann, [cv2.IMWRITE_JPEG_QUALITY, 82])
            if not ok_enc:
                continue
            b64 = base64.b64encode(buf.tobytes()).decode("ascii")
            annotated.append(
                {
                    "frame_idx": fd["frame_idx"],
                    "timestamp_sec": fd["timestamp_sec"],
                    "face_count": len(fd["face_boxes"]),
                    "motion_diff": motion_diff,
                    "data_url": f"data:image/jpeg;base64,{b64}",
                }
            )

        face_frame_count = sum(1 for fd in raw if fd["face_boxes"])
        face_ratio = round(face_frame_count / len(raw), 3) if raw else 0.0
        motion_score = round(float(np.mean(motion_diffs[1:])), 4) if len(motion_diffs) > 1 else 0.0

        knn_result = _knn_predict(face_ratio, motion_score)

        return {
            "ok": True,
            "duration_sec": round(duration_sec, 2),
            "face_ratio": face_ratio,
            "motion_score": motion_score,
            "knn_label": knn_result[0] if knn_result else None,
            "knn_confidence": round(knn_result[1], 3) if knn_result else None,
            "frames": annotated,
            "error": None,
        }

    except Exception as exc:
        print(f"[classify.py] inspect_clip failed for {clip_path}: {exc}", file=sys.stderr)
        return {
            "ok": False,
            "duration_sec": 0.0,
            "face_ratio": 0.0,
            "motion_score": 0.0,
            "knn_label": None,
            "knn_confidence": None,
            "frames": [],
            "error": str(exc)[:300],
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _tally(counts: dict[str, int], label: str, *, skipped: bool = False) -> None:
    if skipped:
        counts["skipped"] += 1
    if label == "A-ROLL":
        counts["a_roll"] += 1
    elif label == "B-ROLL":
        counts["b_roll"] += 1
    elif label == "AMBIGUOUS":
        counts["ambiguous"] += 1
