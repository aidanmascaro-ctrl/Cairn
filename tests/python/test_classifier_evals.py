"""
KNN classifier accuracy eval harness.

Uses frozen fixtures (gt_snapshot.json as training data, held_out_clips.json as
test set) to measure per-clip accuracy and detect regressions in the classifier.

Design goals:
  - Parametrized: each held-out clip becomes its own test node so failures are
    pinpointed in CI output.
  - Regression gate: overall accuracy must stay >= ACCURACY_THRESHOLD (80%).
  - Zero I/O: no filesystem, no ffprobe, no OpenCV — tests run offline in <10ms.
  - Ground-truth isolation: _gt_examples is reset after each test so different
    test runs don't bleed into each other.

Run: pytest tests/python/test_classifier_evals.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src" / "workers"))
import classify

# ── Fixtures dir ──────────────────────────────────────────────────────────────

FIXTURES = Path(__file__).parent / "fixtures"

ACCURACY_THRESHOLD = 0.80  # minimum acceptable accuracy across held-out set


# ── Load fixture data ─────────────────────────────────────────────────────────


def _load_gt_snapshot() -> list[dict]:
    return json.loads((FIXTURES / "gt_snapshot.json").read_text())["clips"]


def _load_held_out() -> list[dict]:
    return json.loads((FIXTURES / "held_out_clips.json").read_text())["clips"]


GT_EXAMPLES = _load_gt_snapshot()
HELD_OUT_CLIPS = _load_held_out()


# ── Per-clip parametrized tests ───────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_gt():
    """Inject the frozen GT snapshot and restore after each test."""
    classify._gt_examples = GT_EXAMPLES
    yield
    classify._gt_examples = None


@pytest.mark.parametrize(
    "clip",
    HELD_OUT_CLIPS,
    ids=[c["id"] for c in HELD_OUT_CLIPS],
)
def test_held_out_clip_correctly_classified(clip: dict) -> None:
    """Each held-out clip must be classified with its known label."""
    face_ratio: float = clip["features"]["face_ratio"]
    motion_score: float = clip["features"]["motion_score"]
    duration_sec: float = clip["duration_sec"]

    predicted = classify.classify_clip(duration_sec, face_ratio, motion_score)

    assert predicted == clip["label"], (
        f"Clip {clip['id']!r}: expected {clip['label']!r} but got {predicted!r} "
        f"(face_ratio={face_ratio}, motion_score={motion_score})"
    )


# ── Aggregate accuracy regression gate ───────────────────────────────────────


def test_accuracy_meets_threshold() -> None:
    """Accuracy across the entire held-out set must be >= ACCURACY_THRESHOLD."""
    correct = 0
    misclassified: list[str] = []

    for clip in HELD_OUT_CLIPS:
        predicted = classify.classify_clip(
            clip["duration_sec"],
            clip["features"]["face_ratio"],
            clip["features"]["motion_score"],
        )
        if predicted == clip["label"]:
            correct += 1
        else:
            misclassified.append(f"{clip['id']}: expected {clip['label']!r}, got {predicted!r}")

    accuracy = correct / len(HELD_OUT_CLIPS)
    assert accuracy >= ACCURACY_THRESHOLD, (
        f"Classifier accuracy {accuracy:.0%} is below threshold {ACCURACY_THRESHOLD:.0%}.\n"
        f"Misclassified:\n" + "\n".join(f"  {m}" for m in misclassified)
    )


# ── KNN confidence sanity checks ──────────────────────────────────────────────


def test_knn_predict_returns_high_confidence_for_clear_aroll() -> None:
    """A clip deep in the A-ROLL cluster should get >= 70% confidence."""
    result = classify._knn_predict(face_ratio=1.0, motion_score=0.01)
    assert result is not None, "_knn_predict returned None with sufficient GT examples"
    label, confidence = result
    assert label == "A-ROLL"
    assert confidence >= classify.KNN_CONFIDENCE_THRESHOLD


def test_knn_predict_returns_high_confidence_for_clear_broll() -> None:
    """A clip deep in the B-ROLL cluster should get >= 70% confidence."""
    result = classify._knn_predict(face_ratio=0.0, motion_score=0.20)
    assert result is not None
    label, confidence = result
    assert label == "B-ROLL"
    assert confidence >= classify.KNN_CONFIDENCE_THRESHOLD


def test_knn_predict_returns_none_when_below_min_examples() -> None:
    """_knn_predict should return None when fewer than KNN_MIN_EXAMPLES examples are loaded."""
    classify._gt_examples = GT_EXAMPLES[: classify.KNN_MIN_EXAMPLES - 1]
    result = classify._knn_predict(face_ratio=0.8, motion_score=0.02)
    assert result is None


# ── Fixture integrity checks ──────────────────────────────────────────────────


def test_gt_snapshot_has_both_classes() -> None:
    """GT snapshot must contain both A-ROLL and B-ROLL examples."""
    labels = {c["label"] for c in GT_EXAMPLES}
    assert "A-ROLL" in labels
    assert "B-ROLL" in labels


def test_held_out_set_has_both_classes() -> None:
    """Held-out set must contain both A-ROLL and B-ROLL clips."""
    labels = {c["label"] for c in HELD_OUT_CLIPS}
    assert "A-ROLL" in labels
    assert "B-ROLL" in labels


def test_gt_snapshot_exceeds_knn_min_examples() -> None:
    """GT snapshot must have at least KNN_MIN_EXAMPLES entries for the KNN to activate."""
    assert len(GT_EXAMPLES) >= classify.KNN_MIN_EXAMPLES
