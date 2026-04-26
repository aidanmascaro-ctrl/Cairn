"""Tests for the classify worker (duration + face detection classifier)."""

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
    def test_short_no_face_is_broll(self):
        assert classify.classify_clip(30.0, 0.0) == "B-ROLL"

    def test_short_no_face_at_boundary(self):
        # exactly 59.9 s, no face → still B-ROLL
        assert classify.classify_clip(59.9, 0.0) == "B-ROLL"

    def test_short_with_face_is_ambiguous(self):
        assert classify.classify_clip(30.0, 0.8) == "AMBIGUOUS"

    def test_long_with_face_is_aroll(self):
        assert classify.classify_clip(300.0, 1.0) == "A-ROLL"

    def test_long_with_face_at_threshold(self):
        # face_ratio exactly at FACE_THRESHOLD
        assert classify.classify_clip(200.0, classify.FACE_THRESHOLD) == "A-ROLL"

    def test_long_no_face_is_ambiguous(self):
        assert classify.classify_clip(300.0, 0.0) == "AMBIGUOUS"

    def test_mid_duration_any_face_is_ambiguous(self):
        # 60 s - 120 s range: always AMBIGUOUS regardless of face
        assert classify.classify_clip(90.0, 0.0) == "AMBIGUOUS"
        assert classify.classify_clip(90.0, 1.0) == "AMBIGUOUS"

    def test_exactly_at_broll_max_is_ambiguous(self):
        # BROLL_MAX_DURATION = 60.0: duration >= 60 is NOT in the short bucket
        assert classify.classify_clip(classify.BROLL_MAX_DURATION, 0.0) == "AMBIGUOUS"

    def test_exactly_at_aroll_min_with_face_is_aroll(self):
        assert classify.classify_clip(classify.AROLL_MIN_DURATION, 1.0) == "A-ROLL"

    def test_just_below_face_threshold_is_not_aroll(self):
        ratio_just_below = classify.FACE_THRESHOLD - 0.01
        assert classify.classify_clip(300.0, ratio_just_below) == "AMBIGUOUS"


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
        # reset the lazy cascade cache so each test gets a clean stub
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
    def test_returns_zero_on_nonzero_returncode(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        with patch("subprocess.run", return_value=mock_proc):
            assert classify.get_duration("/fake/clip.mp4") == 0.0

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

    def test_returns_zero_on_exception(self):
        with patch("subprocess.run", side_effect=OSError("no ffprobe")):
            assert classify.get_duration("/fake/clip.mp4") == 0.0


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
