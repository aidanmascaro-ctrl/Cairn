#!/usr/bin/env python3
"""
Enriches ~/.cairn/ground_truth.json with face_ratio and motion_score
for all labeled clips that are missing these features.

Run once after a training session. Progress is saved after every clip
so it's safe to interrupt and re-run.

Usage:
  python3 tools/enrich_gt.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

# Reuse classify.py's frame extraction and face detection
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "workers"))

import classify

GROUND_TRUTH_PATH = Path.home() / ".cairn" / "ground_truth.json"


def compute_motion_score(frame_paths: list[Path]) -> float:
    """
    Mean normalised frame-to-frame absolute pixel difference (0=static, 1=chaos).
    Uses the same frames already extracted for face detection — no extra ffmpeg calls.
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


def enrich_clip(clip: dict) -> dict:
    """Extract face_ratio and motion_score and merge into clip['features']."""
    features: dict = dict(clip.get("features", {}))
    clip_path = clip["clip_path"]

    if not Path(clip_path).exists():
        print("  (file not found — skipping)")
        return features

    duration = features.get("duration_sec") or classify.get_duration(clip_path)
    features["duration_sec"] = duration

    tmp_dir = Path(tempfile.mkdtemp(prefix="cairn-enrich-"))
    try:
        frames = classify.extract_frames(clip_path, duration, tmp_dir)
        if not frames:
            features["face_ratio"] = 0.0
            features["motion_score"] = 0.0
            return features

        face_count = classify.count_face_frames(frames)
        features["face_ratio"] = round(face_count / len(frames), 3)
        features["motion_score"] = compute_motion_score(frames)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return features


def main() -> None:
    if not GROUND_TRUTH_PATH.exists():
        print(f"No ground_truth.json at {GROUND_TRUTH_PATH}")
        print("Run the trainer first: npm run train -- <shoot_folder>")
        sys.exit(1)

    with open(GROUND_TRUTH_PATH) as f:
        gt = json.load(f)

    clips: list[dict] = gt["clips"]
    pending = [c for c in clips if "face_ratio" not in c.get("features", {})]

    print("Cairn GT Enrichment")
    print(f"  Ground truth : {GROUND_TRUTH_PATH}")
    print(f"  Total clips  : {len(clips)}")
    print(f"  To enrich    : {len(pending)}")
    print(f"  ffmpeg       : {classify.FFMPEG_PATH}")
    print(f"  ffprobe      : {classify.FFPROBE_PATH}")
    print()

    if not pending:
        print("All clips already enriched.")
        return

    for i, clip in enumerate(clips):
        if "face_ratio" in clip.get("features", {}):
            continue

        name = clip["clip_name"]
        label = clip["label"]
        print(f"[{i + 1:3d}/{len(clips)}] {name:22s} {label:6s} ... ", end="", flush=True)

        clip["features"] = enrich_clip(clip)

        face = clip["features"].get("face_ratio", "?")
        motion = clip["features"].get("motion_score", "?")
        print(f"face={face:.2f}  motion={motion:.3f}")

        # Save after every clip so a crash doesn't lose work
        with open(GROUND_TRUTH_PATH, "w") as f:
            json.dump(gt, f, indent=2)

    print(f"\nDone — {GROUND_TRUTH_PATH}")

    # Quick summary
    a_face = [c["features"].get("face_ratio", 0) for c in clips if c["label"] == "A-ROLL"]
    b_face = [c["features"].get("face_ratio", 0) for c in clips if c["label"] == "B-ROLL"]
    if a_face and b_face:
        print("\nFace ratio summary:")
        print(f"  A-ROLL  mean={sum(a_face) / len(a_face):.2f}  max={max(a_face):.2f}")
        print(f"  B-ROLL  mean={sum(b_face) / len(b_face):.2f}  max={max(b_face):.2f}")


if __name__ == "__main__":
    main()
