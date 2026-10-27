"""
Tests for src/workers/selects_metrics.py and the sampler in src/workers/selects.py.

Metrics are checked on synthetic frames so the expectations hold whatever the
calibration constants are tuned to: blur lowers sharpness, a shifted frame
reads as a pan in the right direction, dark frames flag low light, and so on.
"""

from __future__ import annotations

import itertools
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest
import selects
import selects_metrics as m
import selects_score as ss

H, W = 360, 640


def texture(seed: int = 0, h: int = H, w: int = W) -> np.ndarray:
    """A detailed, mid-grey test image (random blocks + fine noise)."""
    rng = np.random.default_rng(seed)
    blocks = rng.integers(40, 215, size=(h // 8 + 1, w // 8 + 1), dtype=np.uint8)
    img = cv2.resize(blocks, (w, h), interpolation=cv2.INTER_NEAREST)
    noise = rng.integers(-12, 13, size=(h, w))
    return np.clip(img.astype(int) + noise, 0, 255).astype(np.uint8)


def shift(img: np.ndarray, dx: int, dy: int) -> np.ndarray:
    matrix = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, matrix, (img.shape[1], img.shape[0]), borderMode=cv2.BORDER_REFLECT)


# ── Static measurements ──────────────────────────────────────────────────────


class TestStatic:
    def test_blur_lowers_sharpness_and_detail(self) -> None:
        sharp = texture()
        soft = cv2.GaussianBlur(sharp, (0, 0), 6)
        a, b = m.measure_static(sharp), m.measure_static(soft)
        assert b["sharpness"] < a["sharpness"]
        assert b["detail"] < a["detail"]

    def test_all_values_in_range(self) -> None:
        out = m.measure_static(texture())
        for key in ("sharpness", "detail", "headroom", "luma", "contrast"):
            assert 0.0 <= out[key] <= 1.0, key

    def test_clipped_frame_loses_headroom(self) -> None:
        img = texture()
        img[: H // 4] = 255
        out = m.measure_static(img)
        assert out["clip_fraction"] > m.CLIPPING_FRACTION
        assert out["headroom"] < 1.0

    def test_letterbox_is_ignored(self) -> None:
        img = texture()
        boxed = img.copy()
        boxed[:60] = 0
        boxed[-60:] = 0
        assert m.crop_letterbox(m.to_float(boxed)).shape[0] == H - 120
        a, b = m.measure_static(img), m.measure_static(boxed)
        assert b["headroom"] == pytest.approx(a["headroom"], abs=0.05)

    def test_black_frame_is_not_cropped_away(self) -> None:
        black = np.zeros((H, W), dtype=np.uint8)
        assert m.crop_letterbox(m.to_float(black)).shape == (H, W)


# ── Motion ───────────────────────────────────────────────────────────────────


class TestMotion:
    def test_identical_frames_are_still(self) -> None:
        img = texture()
        out = m.measure_motion(img, img, rate=4.0)
        assert out["camera_stillness"] == pytest.approx(1.0, abs=0.05)
        assert out["raw_diff"] == 0.0

    def test_content_moving_left_reads_as_camera_panning_right(self) -> None:
        img = texture()
        out = m.measure_motion(img, shift(img, -20, 0), rate=4.0)
        assert out["pan"] > 0
        assert abs(out["pan"]) > abs(out["tilt"])
        assert out["camera_stillness"] < 0.9
        assert out["motion_confidence"] > 0.3

    def test_content_moving_up_reads_as_tilt_down(self) -> None:
        img = texture()
        out = m.measure_motion(img, shift(img, 0, -20), rate=4.0)
        assert out["tilt"] > 0
        assert abs(out["tilt"]) > abs(out["pan"])

    def test_zoom_in_reads_positive(self) -> None:
        img = texture()
        matrix = cv2.getRotationMatrix2D((W / 2, H / 2), 0, 1.08)
        zoomed = cv2.warpAffine(img, matrix, (W, H), borderMode=cv2.BORDER_REFLECT)
        assert m.measure_motion(img, zoomed, rate=4.0)["zoom"] > 0

    def test_axes_scale_with_rate(self) -> None:
        img = texture()
        moved = shift(img, -20, 0)
        slow = m.measure_motion(img, moved, rate=2.0)["pan"]
        fast = m.measure_motion(img, moved, rate=8.0)["pan"]
        assert fast == pytest.approx(slow * 4, rel=0.05)

    def test_camera_move_is_not_subject_motion(self) -> None:
        img = texture()
        out = m.measure_motion(img, shift(img, -12, 0), rate=4.0)
        assert out["subject_stillness"] > 0.7


# ── Finalise + flags ─────────────────────────────────────────────────────────


def _rows(frames: list[np.ndarray], rate: float = 4.0) -> list[list[float]]:
    statics = [m.measure_static(f) for f in frames]
    motions = [m.measure_motion(a, b, rate) for a, b in itertools.pairwise(frames)]
    return m.finalise([i / rate for i in range(len(frames))], statics, motions)


def _flag(rows: list[list[float]], name: str) -> list[bool]:
    return [bool(int(r[ss.COL["flags"]]) & ss.FLAG_BITS[name]) for r in rows]


class TestFinalise:
    def test_row_shape_matches_columns(self) -> None:
        rows = _rows([texture(i) for i in range(4)])
        assert len(rows) == 4
        assert all(len(r) == len(ss.COLUMNS) for r in rows)

    def test_single_frame_has_still_motion(self) -> None:
        rows = _rows([texture()])
        assert rows[0][ss.COL["camera_stillness"]] == 1.0

    def test_mismatched_lengths_raise(self) -> None:
        with pytest.raises(ValueError):
            m.finalise([0.0, 0.25], [m.measure_static(texture())], [])

    def test_empty_is_empty(self) -> None:
        assert m.finalise([], [], []) == []

    def test_duplicate_frames_flag(self) -> None:
        img = texture()
        assert _flag(_rows([img, img, img]), "duplicate") == [False, True, True]

    def test_hard_cut_flag(self) -> None:
        a = texture(1)
        b = np.full((H, W), 245, dtype=np.uint8)
        b[::7] = 10
        assert _flag(_rows([a, a, b]), "hard_cut")[2]

    def test_dark_and_solid_flags(self) -> None:
        black = np.full((H, W), 3, dtype=np.uint8)
        rows = _rows([black])
        assert _flag(rows, "low_light") == [True]
        assert _flag(rows, "solid_color") == [True]

    def test_focus_pull_lowers_focus_stability(self) -> None:
        img = texture()
        steady = _rows([img] * 5)
        pulled = _rows(
            [img, cv2.GaussianBlur(img, (0, 0), 5), img, cv2.GaussianBlur(img, (0, 0), 5), img]
        )
        col = ss.COL["focus_stability"]
        assert pulled[2][col] < steady[2][col]


# ── Sampler (real ffmpeg on a generated clip) ────────────────────────────────


def test_working_size_keeps_aspect_and_even() -> None:
    assert selects.working_size(3840, 2160) == (960, 540)
    assert selects.working_size(2160, 3840) == (540, 960)
    assert selects.working_size(640, 360) == (640, 360)
    w, h = selects.working_size(1001, 333)
    assert w % 2 == 0 and h % 2 == 0


def test_parse_rate() -> None:
    assert selects._parse_rate("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert selects._parse_rate("25") == 25.0
    assert selects._parse_rate("0/0") == 0.0
    assert selects._parse_rate(None) == 0.0


@pytest.fixture(scope="module")
def test_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    ffmpeg = selects.classify.FFMPEG_PATH
    if shutil.which(str(ffmpeg)) is None and not Path(ffmpeg).exists():
        pytest.skip("ffmpeg not available")
    out = tmp_path_factory.mktemp("clip") / "testsrc.mp4"
    subprocess.run(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=25:duration=3",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
    )
    return out


def test_probe_and_sample_real_clip(test_clip: Path) -> None:
    info = selects.probe(str(test_clip))
    assert (info["width"], info["height"]) == (1280, 720)
    assert info["fps"] == 25.0
    frames = list(selects.sample_frames(str(test_clip), 4.0, info["width"], info["height"]))
    assert 11 <= len(frames) <= 13
    assert frames[0].shape == (540, 960)
    assert frames[0].dtype == np.uint8


def test_analyse_clip_block(test_clip: Path) -> None:
    block = selects.analyse_clip(str(test_clip), 4.0)
    assert block["columns"] == list(ss.COLUMNS)
    assert block["sampling_rate"] == 4.0
    assert block["fps"] == 25.0
    assert len(block["frames"]) >= 11
    assert block["frames"][1][0] == pytest.approx(0.25)


def test_analyse_all_writes_sidecar_and_skips_second_time(test_clip: Path, tmp_path: Path) -> None:
    shoot = tmp_path / "shoot"
    cam = shoot / "01_VIDEOS" / "A_CAM"
    cam.mkdir(parents=True)
    clip = cam / "C0001.mp4"
    shutil.copy(test_clip, clip)

    first = list(selects.analyse_all(shoot, 4.0))
    assert first[0] == {"event": "analyse_started", "total_clips": 1, "sampling_rate": 4.0}
    done = first[-1]
    assert done["event"] == "analyse_complete"
    assert (done["analysed"], done["failed"]) == (1, 0)
    assert any(e["event"] == "clip_analysed" for e in first)

    record = selects.sidecar.read_clip(shoot, str(clip))
    assert record is not None and "selects" in record["stages_complete"]

    second = list(selects.analyse_all(shoot, 4.0))
    assert second[-1]["skipped"] == 1
    assert second[-1]["analysed"] == 0

    result = selects.rescore(shoot)
    assert result["clips"][0]["analysed"] is True
    assert result["summary"]["clip_count"] == 1
    assert selects.settings_path(shoot).is_file()


def test_unreadable_clip_reports_error_and_continues(tmp_path: Path) -> None:
    shoot = tmp_path / "shoot"
    cam = shoot / "01_VIDEOS" / "A_CAM"
    cam.mkdir(parents=True)
    (cam / "broken.mp4").write_bytes(b"not a video")
    events = list(selects.analyse_all(shoot, 4.0))
    assert any(e["event"] == "clip_error" for e in events)
    assert events[-1]["failed"] == 1
