"""
pytest suite for src/workers/vision.py

Tests:
- _frame_timestamps: 5 frames for normal clips, 3 for short clips, end-frame safety
- keywords_from_tags: correct flattening + deduplication of empty strings
- scan_broll_clips: filters to B-ROLL only, skips already-vision'd, skips no-sidecar
- estimate_vision: correct cost calculation
- extract_frames: ffmpeg called with correct args; FrameExtractionError on zero-size output
- tag_all idempotency: already-tagged clip is skipped, no API call
- tag_all cloud routing: calls tag_broll_with_claude, writes sidecar, yields clip_tagged
- tag_all error handling: VisionTaggingError → clip_error event, pipeline continues
- tag_all judge sampling: judge called for sampled clip, judge_flagged emitted on low score
- tag_all local fallback: cloud_allowed=False → immediate vision_complete with skipped count
- _cleanup_frames: files and empty directory removed
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src" / "workers"))

import sidecar as sid
import vision as v

# ── _frame_timestamps ─────────────────────────────────────────────────────────


def test_frame_timestamps_normal_clip():
    ts = v._frame_timestamps(30.0)
    assert len(ts) == 5
    assert ts[0] == 0.0
    assert abs(ts[1] - 7.5) < 0.01
    assert abs(ts[2] - 15.0) < 0.01
    assert abs(ts[3] - 22.5) < 0.01
    assert ts[4] == pytest.approx(29.9, abs=0.1)


def test_frame_timestamps_short_clip():
    ts = v._frame_timestamps(1.5)
    assert len(ts) == 3
    assert ts[0] == 0.0
    assert ts[1] == pytest.approx(0.75, abs=0.01)
    assert ts[2] == pytest.approx(1.4, abs=0.1)


def test_frame_timestamps_end_frame_never_exceeds_duration():
    for dur in [2.0, 10.0, 60.0, 600.0]:
        ts = v._frame_timestamps(dur)
        assert all(t < dur for t in ts), f"timestamp >= duration for dur={dur}"


# ── keywords_from_tags ────────────────────────────────────────────────────────


def test_keywords_from_tags_basic():
    tags = {
        "subjects": ["hands", "potter's wheel", "wet clay"],
        "actions": ["shaping", "spinning", "centering"],
        "shot_type": "close-up",
        "colour_palette": ["warm tungsten", "earthy ochre", "deep brown"],
        "lighting_style": "key from left",
        "mood": "intimate",
        "environment": "indoor studio",
        "time_of_day": "indoor",
        "camera_motion": "static tripod",
        "confidence": 0.88,
    }
    kw = v.keywords_from_tags(tags)
    assert "hands" in kw
    assert "close-up" in kw
    assert "warm tungsten" in kw
    assert "key" in kw
    # Only first 2 actions
    assert "shaping" in kw
    assert "spinning" in kw
    assert "centering" not in kw


def test_keywords_from_tags_no_empty_strings():
    tags = {
        "subjects": ["table"],
        "actions": [],
        "shot_type": "wide",
        "colour_palette": [],
        "lighting_style": "",
        "mood": "",
        "environment": "",
        "time_of_day": "indoor",
        "camera_motion": "static tripod",
        "confidence": 0.5,
    }
    kw = v.keywords_from_tags(tags)
    assert ",," not in kw
    assert not kw.startswith(", ")
    assert not kw.endswith(", ")


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_video(directory: Path, name: str) -> Path:
    f = directory / name
    f.write_bytes(b"\x00" * 100)
    return f


# ── scan_broll_clips ──────────────────────────────────────────────────────────


def test_scan_broll_clips_returns_broll_only(tmp_path):
    shoot = tmp_path / "shoot"
    acam = shoot / "01_VIDEOS" / "A_CAM"
    acam.mkdir(parents=True)
    aroll = _make_video(acam, "aroll.mp4")
    broll = _make_video(acam, "broll.mp4")
    unclassified = _make_video(acam, "unclass.mp4")

    sid.merge_clip(shoot, str(aroll), {"classification": "A-ROLL"}, new_stage="classify")
    sid.merge_clip(shoot, str(broll), {"classification": "B-ROLL"}, new_stage="classify")
    # unclassified: no sidecar

    result = v.scan_broll_clips(shoot)
    assert str(broll) in result
    assert str(aroll) not in result
    assert str(unclassified) not in result


def test_scan_broll_clips_skips_already_tagged(tmp_path):
    shoot = tmp_path / "shoot"
    acam = shoot / "01_VIDEOS" / "A_CAM"
    acam.mkdir(parents=True)
    clip = _make_video(acam, "broll.mp4")

    sid.merge_clip(shoot, str(clip), {"classification": "B-ROLL"}, new_stage="classify")
    sid.merge_clip(
        shoot,
        str(clip),
        {"vision": {"provider": "anthropic", "provider_model": v.ANTHROPIC_MODEL}},
        new_stage="vision",
    )

    result = v.scan_broll_clips(shoot)
    assert result == []


# ── estimate_vision ───────────────────────────────────────────────────────────


def test_estimate_vision_cost():
    result = v.estimate_vision(["a.mp4", "b.mp4", "c.mp4"])
    assert result["clip_count"] == 3
    expected = round(3 * v.ANTHROPIC_VISION_COST_PER_CLIP_USD, 4)
    assert result["estimated_cost_usd"] == pytest.approx(expected, rel=1e-3)


def test_estimate_vision_empty():
    result = v.estimate_vision([])
    assert result["clip_count"] == 0
    assert result["estimated_cost_usd"] == 0.0


# ── extract_frames ────────────────────────────────────────────────────────────


def test_extract_frames_calls_ffmpeg(tmp_path):
    frames_dir = tmp_path / "frames"

    def _fake_run(cmd, **kwargs):
        # Create a non-empty fake JPEG for each frame
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)
        result = MagicMock()
        result.returncode = 0
        return result

    with (
        patch.object(v, "get_clip_duration_sec", return_value=30.0),
        patch("subprocess.run", side_effect=_fake_run),
    ):
        paths = v.extract_frames("/fake/clip.mp4", frames_dir)

    assert len(paths) == 5
    assert all(Path(p).exists() for p in paths)


def test_extract_frames_raises_on_ffmpeg_failure(tmp_path):
    frames_dir = tmp_path / "frames"

    def _failing_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 1
        result.stderr = b"error"
        return result

    with (
        patch.object(v, "get_clip_duration_sec", return_value=10.0),
        patch("subprocess.run", side_effect=_failing_run),
        pytest.raises(v.FrameExtractionError),
    ):
        v.extract_frames("/fake/clip.mp4", frames_dir)


# ── tag_all: idempotency ──────────────────────────────────────────────────────


def test_tag_all_idempotency(tmp_path):
    """Already-tagged clip is skipped; no API call."""
    shoot = tmp_path / "shoot"
    acam = shoot / "01_VIDEOS" / "A_CAM"
    acam.mkdir(parents=True)
    clip = _make_video(acam, "broll.mp4")

    sid.merge_clip(shoot, str(clip), {"classification": "B-ROLL"}, new_stage="classify")
    sid.merge_clip(
        shoot,
        str(clip),
        {"vision": {"provider_model": v.ANTHROPIC_MODEL}},
        new_stage="vision",
    )

    with patch.object(v, "tag_broll_with_claude") as mock_api:
        events = list(v.tag_all([str(clip)], shoot, "key"))

    mock_api.assert_not_called()
    skipped = [e for e in events if e.get("event") == "clip_skipped"]
    assert len(skipped) == 1


# ── tag_all: cloud routing ────────────────────────────────────────────────────


def _mock_tags() -> dict:
    return {
        "subjects": ["wheel"],
        "actions": ["spinning"],
        "shot_type": "close-up",
        "mood": "calm",
        "environment": "studio",
        "colour_palette": ["warm orange"],
        "time_of_day": "indoor",
        "lighting_style": "key left",
        "camera_motion": "static tripod",
        "confidence": 0.9,
    }


def test_tag_all_writes_sidecar(tmp_path):
    shoot = tmp_path / "shoot"
    acam = shoot / "01_VIDEOS" / "A_CAM"
    acam.mkdir(parents=True)
    clip = _make_video(acam, "broll.mp4")

    sid.merge_clip(shoot, str(clip), {"classification": "B-ROLL"}, new_stage="classify")

    with (
        patch.object(v, "extract_frames", return_value=["/tmp/f1.jpg", "/tmp/f2.jpg"]),
        patch.object(v, "tag_broll_with_claude", return_value=_mock_tags()),
        patch.object(v, "get_clip_duration_sec", return_value=10.0),
        patch.object(v, "_cleanup_frames"),
        patch.object(
            v,
            "judge_tags_with_claude",
            return_value={
                "subjects_score": 0.9,
                "actions_score": 0.9,
                "shot_type_score": 0.9,
                "mood_score": 0.9,
                "overall_score": 0.9,
                "low_confidence_fields": [],
            },
        ),
    ):
        events = list(v.tag_all([str(clip)], shoot, "key"))

    sc = sid.read_clip(shoot, str(clip))
    assert sc is not None
    assert "vision" in sc["stages_complete"]
    assert sc["vision"]["provider"] == v.PROVIDER_ANTHROPIC
    assert sc["vision"]["tags"]["shot_type"] == "close-up"

    tagged = [e for e in events if e.get("event") == "clip_tagged"]
    assert len(tagged) == 1
    assert "keywords" in tagged[0]


# ── tag_all: error handling ───────────────────────────────────────────────────


def test_tag_all_api_error_yields_clip_error(tmp_path):
    """VisionTaggingError → clip_error event, pipeline continues."""
    shoot = tmp_path / "shoot"
    acam = shoot / "01_VIDEOS" / "A_CAM"
    acam.mkdir(parents=True)
    clip = _make_video(acam, "broll.mp4")
    sid.merge_clip(shoot, str(clip), {"classification": "B-ROLL"}, new_stage="classify")

    with (
        patch.object(v, "extract_frames", return_value=["/tmp/f1.jpg"]),
        patch.object(v, "tag_broll_with_claude", side_effect=v.VisionTaggingError("no tool call")),
        patch.object(v, "get_clip_duration_sec", return_value=10.0),
        patch.object(v, "_cleanup_frames"),
    ):
        events = list(v.tag_all([str(clip)], shoot, "key"))

    errors = [e for e in events if e.get("event") == "clip_error"]
    assert len(errors) == 1
    assert "no tool call" in errors[0]["reason"]

    complete = [e for e in events if e.get("event") == "vision_complete"]
    assert complete[0]["errors"] == 1


# ── tag_all: judge flagging ───────────────────────────────────────────────────


def test_tag_all_judge_flags_low_score(tmp_path):
    """Judge with overall_score < threshold emits judge_flagged."""
    shoot = tmp_path / "shoot"
    acam = shoot / "01_VIDEOS" / "A_CAM"
    acam.mkdir(parents=True)
    clip = _make_video(acam, "broll.mp4")
    sid.merge_clip(shoot, str(clip), {"classification": "B-ROLL"}, new_stage="classify")

    low_judge = {
        "subjects_score": 0.3,
        "actions_score": 0.3,
        "shot_type_score": 0.9,
        "mood_score": 0.9,
        "overall_score": 0.3,
        "low_confidence_fields": ["subjects", "actions"],
    }

    with (
        patch.object(v, "extract_frames", return_value=["/tmp/f1.jpg"]),
        patch.object(v, "tag_broll_with_claude", return_value=_mock_tags()),
        patch.object(v, "get_clip_duration_sec", return_value=10.0),
        patch.object(v, "_cleanup_frames"),
        patch.object(v, "judge_tags_with_claude", return_value=low_judge),
        # Force 100% sample rate so the single clip is always judged
        patch.object(v, "JUDGE_SAMPLE_RATE", 1.0),
    ):
        events = list(v.tag_all([str(clip)], shoot, "key"))

    flagged = [e for e in events if e.get("event") == "judge_flagged"]
    assert len(flagged) == 1
    assert "subjects" in flagged[0]["low_fields"]


# ── tag_all: local fallback ───────────────────────────────────────────────────


def test_tag_all_local_fallback(tmp_path):
    """cloud_allowed=False → immediate vision_complete with all clips skipped."""
    shoot = tmp_path / "shoot"
    acam = shoot / "01_VIDEOS" / "A_CAM"
    acam.mkdir(parents=True)
    clip = _make_video(acam, "broll.mp4")
    sid.merge_clip(shoot, str(clip), {"classification": "B-ROLL"}, new_stage="classify")

    with patch.object(v, "tag_broll_with_claude") as mock_api:
        events = list(v.tag_all([str(clip)], shoot, "", cloud_allowed=False))

    mock_api.assert_not_called()
    complete = [e for e in events if e.get("event") == "vision_complete"]
    assert len(complete) == 1
    assert complete[0]["skipped"] == 1


# ── _cleanup_frames ───────────────────────────────────────────────────────────


def test_cleanup_frames_removes_files_and_dir(tmp_path):
    frames_dir = tmp_path / "frames" / "abc123"
    frames_dir.mkdir(parents=True)
    f1 = frames_dir / "01.jpg"
    f2 = frames_dir / "02.jpg"
    f1.write_bytes(b"data")
    f2.write_bytes(b"data")

    v._cleanup_frames([str(f1), str(f2)])

    assert not f1.exists()
    assert not f2.exists()
    assert not frames_dir.exists()
