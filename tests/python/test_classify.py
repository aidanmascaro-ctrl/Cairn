"""Tests for the classify worker (KNN + face-detection classifier)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make sure src/workers is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src" / "workers"))

import classify

# ── classify_clip decision logic ─────────────────────────────────────────────


class TestClassifyClip:
    def setup_method(self):
        classify._gt_examples = None

    def teardown_method(self):
        classify._gt_examples = None

    def _make_examples(self, n_aroll: int, n_broll: int) -> list[dict]:
        examples = []
        for _ in range(n_aroll):
            examples.append(
                {"label": "A-ROLL", "features": {"face_ratio": 0.8, "motion_score": 0.01}}
            )
        for _ in range(n_broll):
            examples.append(
                {"label": "B-ROLL", "features": {"face_ratio": 0.0, "motion_score": 0.10}}
            )
        return examples

    def test_no_gt_face_present_is_aroll(self):
        classify._gt_examples = []
        assert classify.classify_clip(0.0, 0.8, 0.01) == "A-ROLL"

    def test_no_gt_no_face_is_broll(self):
        classify._gt_examples = []
        assert classify.classify_clip(0.0, 0.0, 0.10) == "B-ROLL"

    def test_knn_confident_aroll_prediction(self):
        classify._gt_examples = self._make_examples(n_aroll=10, n_broll=10)
        # query matches A-ROLL cluster closely
        result = classify.classify_clip(0.0, 0.8, 0.01)
        assert result == "A-ROLL"

    def test_knn_confident_broll_prediction(self):
        classify._gt_examples = self._make_examples(n_aroll=10, n_broll=10)
        # query matches B-ROLL cluster closely
        result = classify.classify_clip(0.0, 0.0, 0.10)
        assert result == "B-ROLL"

    def test_knn_low_confidence_returns_ambiguous(self):
        # 5 A-ROLL and 5 B-ROLL equidistant from the query point → split vote → AMBIGUOUS
        examples = [
            {"label": "A-ROLL", "features": {"face_ratio": 1.0, "motion_score": 0.0}},
            {"label": "A-ROLL", "features": {"face_ratio": 1.0, "motion_score": 0.0}},
            {"label": "B-ROLL", "features": {"face_ratio": 0.0, "motion_score": 1.0}},
            {"label": "B-ROLL", "features": {"face_ratio": 0.0, "motion_score": 1.0}},
            # 5th equidistant from both clusters (midpoint) — neither wins by 70%
            {"label": "A-ROLL", "features": {"face_ratio": 1.0, "motion_score": 0.0}},
        ]
        classify._gt_examples = examples
        # query at the midpoint: face=0.5, motion=0.5 → nearest 5 are 3 A-ROLL + 2 B-ROLL
        # 3/5 = 60% < KNN_CONFIDENCE_THRESHOLD (0.70) → AMBIGUOUS
        result = classify.classify_clip(0.0, 0.5, 0.5)
        assert result == "AMBIGUOUS"

    def test_fewer_than_min_examples_uses_rule_based(self):
        # Only 3 enriched examples — below KNN_MIN_EXAMPLES (5)
        classify._gt_examples = [
            {"label": "A-ROLL", "features": {"face_ratio": 0.8, "motion_score": 0.01}},
            {"label": "A-ROLL", "features": {"face_ratio": 0.6, "motion_score": 0.02}},
            {"label": "B-ROLL", "features": {"face_ratio": 0.0, "motion_score": 0.10}},
        ]
        assert classify.classify_clip(0.0, 0.8, 0.01) == "A-ROLL"
        assert classify.classify_clip(0.0, 0.0, 0.10) == "B-ROLL"


# ── _knn_predict ──────────────────────────────────────────────────────────────


class TestKnnPredict:
    def setup_method(self):
        classify._gt_examples = None

    def teardown_method(self):
        classify._gt_examples = None

    def test_returns_none_when_no_gt(self):
        classify._gt_examples = []
        assert classify._knn_predict(0.5, 0.05) is None

    def test_returns_label_and_confidence(self):
        classify._gt_examples = [
            {"label": "A-ROLL", "features": {"face_ratio": 0.8, "motion_score": 0.01}}
        ] * 10
        result = classify._knn_predict(0.8, 0.01)
        assert result is not None
        label, conf = result
        assert label == "A-ROLL"
        assert conf == pytest.approx(1.0)


# ── count_face_frames (mocked OpenCV) ────────────────────────────────────────


def _make_cv2_stub(imread_return=None, cvtcolor_return=None, detect_return=None):
    """Return a mock cv2 module with controllable return values."""
    stub = MagicMock()
    stub.imread.return_value = imread_return
    stub.cvtColor.return_value = cvtcolor_return
    cascade = MagicMock()
    cascade.detectMultiScale.return_value = detect_return if detect_return is not None else []
    stub.CascadeClassifier.return_value = cascade
    stub.data.haarcascades = ""
    return stub, cascade


class TestCountFaceFrames:
    def setup_method(self):
        classify._face_cascade = None

    def teardown_method(self):
        sys.modules.pop("cv2", None)
        classify._face_cascade = None

    def test_empty_list_returns_zero(self):
        stub, _ = _make_cv2_stub()
        sys.modules["cv2"] = stub
        assert classify.count_face_frames([]) == 0

    def test_unreadable_frame_skipped(self):
        stub, _ = _make_cv2_stub(imread_return=None)
        sys.modules["cv2"] = stub
        fake_path = MagicMock(spec=Path)
        fake_path.__str__ = lambda s: "/nonexistent/frame.jpg"
        result = classify.count_face_frames([fake_path])
        assert result == 0

    def test_frame_with_face_counted(self):
        import numpy as np

        fake_img = np.zeros((480, 640, 3), dtype="uint8")
        fake_gray = np.zeros((480, 640), dtype="uint8")
        stub, _cascade = _make_cv2_stub(
            imread_return=fake_img,
            cvtcolor_return=fake_gray,
            detect_return=[(10, 10, 100, 100)],
        )
        sys.modules["cv2"] = stub

        fake_path = MagicMock(spec=Path)
        fake_path.__str__ = lambda s: "/fake/frame.jpg"
        result = classify.count_face_frames([fake_path])
        assert result == 1

    def test_frame_without_face_not_counted(self):
        import numpy as np

        fake_img = np.zeros((480, 640, 3), dtype="uint8")
        fake_gray = np.zeros((480, 640), dtype="uint8")
        stub, _ = _make_cv2_stub(
            imread_return=fake_img,
            cvtcolor_return=fake_gray,
            detect_return=[],
        )
        sys.modules["cv2"] = stub

        fake_path = MagicMock(spec=Path)
        fake_path.__str__ = lambda s: "/fake/frame.jpg"
        result = classify.count_face_frames([fake_path])
        assert result == 0


# ── get_duration (mocked ffprobe) ─────────────────────────────────────────────


class TestGetDuration:
    def test_returns_none_on_nonzero_returncode(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        with patch("subprocess.run", return_value=mock_proc):
            assert classify.get_duration("/fake/clip.mp4") is None

    def test_parses_duration_from_ffprobe_json(self):
        import json

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({"format": {"duration": "93.5"}}).encode()
        with patch("subprocess.run", return_value=mock_proc):
            assert classify.get_duration("/fake/clip.mp4") == pytest.approx(93.5)

    def test_returns_zero_on_missing_duration_key(self):
        import json

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({"format": {}}).encode()
        with patch("subprocess.run", return_value=mock_proc):
            assert classify.get_duration("/fake/clip.mp4") == 0.0

    def test_returns_none_on_exception(self):
        with patch("subprocess.run", side_effect=OSError("no ffprobe")):
            assert classify.get_duration("/fake/clip.mp4") is None


# ── scan_video_clips ──────────────────────────────────────────────────────────


class TestScanVideoClips:
    def test_finds_clips_in_a_cam_and_c_cam(self, tmp_path):
        a_cam = tmp_path / "01_VIDEOS" / "A_CAM"
        c_cam = tmp_path / "01_VIDEOS" / "C_CAM"
        a_cam.mkdir(parents=True)
        c_cam.mkdir(parents=True)
        (a_cam / "clip1.mp4").touch()
        (a_cam / "thumb.jpg").touch()  # should be ignored
        (c_cam / "clip2.mov").touch()

        result = classify.scan_video_clips(tmp_path)

        assert str(a_cam / "clip1.mp4") in result
        assert str(c_cam / "clip2.mov") in result
        assert not any("thumb.jpg" in p for p in result)

    def test_returns_empty_when_no_video_dirs(self, tmp_path):
        assert classify.scan_video_clips(tmp_path) == []
