"""
Tests for src/workers/selects_score.py — scoring + segmentation rules (specs/selects.md §3).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
import selects_presets as sp
import selects_score as ss

RATE = 4.0


def frame(
    t: float,
    *,
    sharpness: float = 0.9,
    detail: float = 0.8,
    focus_stability: float = 1.0,
    headroom: float = 1.0,
    luma: float = 0.45,
    contrast: float = 0.4,
    flicker: float = 1.0,
    camera_stillness: float = 1.0,
    subject_stillness: float = 1.0,
    motion_smoothness: float = 1.0,
    pan: float = 0.0,
    tilt: float = 0.0,
    zoom: float = 0.0,
    twist: float = 0.0,
    motion_confidence: float = 0.9,
    flags: int = 0,
) -> list[float]:
    return [
        t,
        sharpness,
        detail,
        focus_stability,
        headroom,
        luma,
        contrast,
        flicker,
        camera_stillness,
        subject_stillness,
        motion_smoothness,
        pan,
        tilt,
        zoom,
        twist,
        motion_confidence,
        float(flags),
    ]


def good_frames(n: int, **kw: Any) -> list[list[float]]:
    return [frame(i / RATE, **kw) for i in range(n)]


def bad_frames(n: int, start: int = 0) -> list[list[float]]:
    return [frame((start + i) / RATE, sharpness=0.1, detail=0.1) for i in range(n)]


def block(frames: list[list[float]], rate: float = RATE) -> dict[str, Any]:
    return {"frames": frames, "sampling_rate": rate, "duration_sec": len(frames) / rate}


def segs(
    frames: list[list[float]], settings: ss.SelectsSettings | None = None
) -> list[dict[str, Any]]:
    return ss.score_clip(block(frames), settings or ss.default_settings())["segments"]


# ── Settings ─────────────────────────────────────────────────────────────────


class TestSettings:
    def test_round_trip_is_stable(self) -> None:
        s = ss.default_settings()
        assert ss.settings_from_dict(ss.settings_to_dict(s)) == s

    def test_bad_values_fall_back_and_clamp(self) -> None:
        s = ss.settings_from_dict(
            {
                "score_threshold_lo": "nope",
                "score_threshold_hi": 7,
                "min_segment_sec": -3,
                "sub_weights": {"sharpness": 9},
                "flags": {"shaky": "bogus"},
                "max_segments_per_clip": "none",
                "camera_filter": {"pan": -4, "strictness": 2},
            }
        )
        assert s.score_threshold_lo == 0.8
        assert s.score_threshold_hi == 1.0
        assert s.min_segment_sec == 0.0
        assert s.sub_weights["sharpness"] == 1.0
        assert s.flags["shaky"] == "neutral"
        assert s.max_segments_per_clip is None
        assert s.camera_filter["pan"] == -1.0
        assert s.camera_filter["strictness"] == 1.0

    def test_swapped_thresholds_are_reordered(self) -> None:
        s = ss.settings_from_dict({"score_threshold_lo": 0.9, "score_threshold_hi": 0.5})
        assert (s.score_threshold_lo, s.score_threshold_hi) == (0.5, 0.9)

    def test_every_preset_builds_and_keeps_its_id(self) -> None:
        presets = sp.list_presets()
        assert presets[0]["id"] == "all-rounder"
        for p in presets:
            built = sp.preset_settings(p["id"])
            assert built.preset_id == p["id"]
            assert ss.settings_from_dict(p["settings"]) == built

    def test_unknown_preset_raises(self) -> None:
        with pytest.raises(KeyError):
            sp.preset_settings("nope")

    def test_action_sport_rewards_movement(self) -> None:
        s = sp.preset_settings("action-sport")
        assert s.sub_weights["camera_stillness"] < 0
        assert s.flags["motion_blur"] == "favour"


# ── Scoring ──────────────────────────────────────────────────────────────────


class TestScoreFrames:
    def test_perfect_frame_scores_one(self) -> None:
        scored = ss.score_frames([frame(0.0, sharpness=1.0, detail=1.0)], ss.default_settings())
        assert scored.score[0] == pytest.approx(1.0)

    def test_soft_frame_scores_lower_than_sharp(self) -> None:
        s = ss.default_settings()
        sharp = ss.score_frames([frame(0.0, sharpness=0.9)], s).score[0]
        soft = ss.score_frames([frame(0.0, sharpness=0.2)], s).score[0]
        assert soft < sharp

    def test_negative_slider_flips_direction(self) -> None:
        s = ss.settings_from_dict({"sub_weights": {"camera_stillness": -0.4}})
        moving = ss.score_frames([frame(0.0, camera_stillness=0.1)], s).score[0]
        still = ss.score_frames([frame(0.0, camera_stillness=1.0)], s).score[0]
        assert moving > still

    def test_group_with_no_enabled_submetric_contributes_nothing(self) -> None:
        s = ss.settings_from_dict(
            {
                "sub_weights": {
                    "camera_stillness": 0,
                    "subject_stillness": 0,
                    "motion_smoothness": 0,
                }
            }
        )
        scored = ss.score_frames([frame(0.0, camera_stillness=0.0, sharpness=1.0, detail=1.0)], s)
        assert np.isnan(scored.groups["motion"][0])
        assert scored.score[0] == pytest.approx(1.0)

    def test_target_metric_peaks_at_target(self) -> None:
        s = ss.settings_from_dict({"sub_weights": {"luma_target": 1.0}, "targets": {"luma": 0.5}})
        on = ss.score_frames([frame(0.0, luma=0.5)], s).groups["exposure"][0]
        off = ss.score_frames([frame(0.0, luma=0.9)], s).groups["exposure"][0]
        assert on > off

    def test_metrics_off_scores_one(self) -> None:
        s = ss.settings_from_dict({"metrics_enabled": False})
        assert ss.score_frames([frame(0.0, sharpness=0.0, detail=0.0)], s).score[0] == 1.0

    def test_disfavour_and_favour_multipliers(self) -> None:
        base = ss.score_frames([frame(0.0)], ss.default_settings()).score[0]
        dis = ss.settings_from_dict({"flags": {"shaky": "disfavour"}})
        fav = ss.settings_from_dict({"flags": {"shaky": "favour"}})
        shaky = frame(0.0, flags=ss.FLAG_BITS["shaky"])
        assert ss.score_frames([shaky], dis).score[0] == pytest.approx(base * 0.6)
        assert ss.score_frames([shaky], fav).score[0] == pytest.approx(min(1.0, base * 1.2))

    def test_exclude_beats_include(self) -> None:
        s = ss.settings_from_dict({"flags": {"clipping": "exclude", "shaky": "include"}})
        both = frame(0.0, flags=ss.FLAG_BITS["clipping"] | ss.FLAG_BITS["shaky"])
        scored = ss.score_frames([both], s)
        assert not scored.usable[0]
        assert not scored.forced[0]

    def test_flags_off_ignores_exclusions(self) -> None:
        s = ss.settings_from_dict({"flags_enabled": False})
        scored = ss.score_frames([frame(0.0, flags=ss.FLAG_BITS["clipping"])], s)
        assert scored.usable[0]

    def test_static_only_filter_multiplies_by_stillness(self) -> None:
        s = ss.settings_from_dict({"camera_filter": {"enabled": True, "static_only": True}})
        base = ss.score_frames([frame(0.0)], ss.default_settings()).score[0]
        moving = ss.score_frames([frame(0.0, camera_stillness=0.5)], s).score[0]
        assert moving == pytest.approx(base * 0.5 * 0.5 + 0.0, abs=0.25)
        assert moving < base

    def test_pan_filter_prefers_matching_direction(self) -> None:
        s = ss.settings_from_dict({"camera_filter": {"enabled": True, "pan": 0.5}})
        right = ss.score_frames([frame(0.0, pan=0.04)], s).score[0]
        left = ss.score_frames([frame(0.0, pan=-0.04)], s).score[0]
        still = ss.score_frames([frame(0.0, pan=0.0)], s).score[0]
        assert right > left
        assert right > still
        mirrored = ss.settings_from_dict(
            {"camera_filter": {"enabled": True, "pan": 0.5, "mirror": True}}
        )
        assert ss.score_frames([frame(0.0, pan=-0.04)], mirrored).score[0] == pytest.approx(right)

    def test_wrong_column_count_raises(self) -> None:
        with pytest.raises(ValueError):
            ss.score_frames([[0.0, 1.0]], ss.default_settings())


# ── Segmentation ─────────────────────────────────────────────────────────────


class TestSegments:
    def test_one_long_good_run_is_one_select(self) -> None:
        out = segs(good_frames(40))
        assert len(out) == 1
        assert out[0]["start_sec"] == 0.0
        assert out[0]["end_sec"] == pytest.approx(10.0)
        assert out[0]["mean_score"] > 0.8

    def test_short_run_is_dropped_by_min_length(self) -> None:
        frames = (
            bad_frames(10) + [frame((10 + i) / RATE) for i in range(4)] + bad_frames(10, start=14)
        )
        assert segs(frames) == []  # 1 s < 2 s minimum
        loose = ss.settings_from_dict({"min_segment_sec": 1.0, "handles_samples": 0})
        out = segs(frames, loose)
        assert len(out) == 1
        assert out[0]["start_sec"] == pytest.approx(2.5)
        assert out[0]["end_sec"] == pytest.approx(3.5)

    def test_dip_shorter_than_dip_length_is_bridged(self) -> None:
        frames = (
            good_frames(12) + bad_frames(4, start=12) + [frame((16 + i) / RATE) for i in range(12)]
        )
        s = ss.settings_from_dict({"dip_sec": 3.0, "handles_samples": 0})
        out = segs(frames, s)
        assert len(out) == 1
        assert out[0]["end_sec"] == pytest.approx(7.0)

    def test_dip_at_or_over_dip_length_splits(self) -> None:
        frames = (
            good_frames(12) + bad_frames(12, start=12) + [frame((24 + i) / RATE) for i in range(12)]
        )
        s = ss.settings_from_dict({"dip_sec": 3.0, "handles_samples": 0})
        assert len(segs(frames, s)) == 2
        bridged = ss.settings_from_dict({"dip_sec": 4.0, "handles_samples": 0})
        assert len(segs(frames, bridged)) == 1

    def test_excluded_sample_always_splits(self) -> None:
        clip = ss.FLAG_BITS["clipping"]
        frames = (
            good_frames(12) + [frame(3.0, flags=clip)] + [frame((13 + i) / RATE) for i in range(12)]
        )
        s = ss.settings_from_dict({"dip_sec": 6.0, "handles_samples": 0})
        out = segs(frames, s)
        assert len(out) == 2
        assert all("clipping" not in seg["flags"] for seg in out)

    def test_max_length_splits_into_equal_pieces(self) -> None:
        s = ss.settings_from_dict({"max_segment_sec": 4.0, "handles_samples": 0})
        out = segs(good_frames(40), s)
        assert len(out) == 3
        assert all(seg["duration_sec"] <= 4.0 for seg in out)
        assert out[-1]["end_sec"] == pytest.approx(10.0)

    def test_handles_extend_but_stop_at_unusable_and_neighbour(self) -> None:
        clip = ss.FLAG_BITS["clipping"]
        frames = (
            [frame(0.0, flags=clip)]
            + bad_frames(3, start=1)
            + [frame((4 + i) / RATE) for i in range(12)]
            + bad_frames(2, start=16)
            + [frame((18 + i) / RATE) for i in range(12)]
            + bad_frames(5, start=30)
        )
        s = ss.settings_from_dict({"dip_sec": 0.0, "handles_samples": 3})
        out = segs(frames, s)
        assert len(out) == 2
        first, second = out
        # left handle: 3 samples back from index 4 would hit index 1..3 (usable) — index 0 excluded
        assert first["start_sample"] == 1
        # right handle of first stops at the second's start (shared gap of 2)
        assert first["end_sample"] == 18
        assert second["start_sample"] == 18
        assert second["end_sample"] == 33
        # core score unaffected by the handles
        assert first["mean_score"] > 0.8

    def test_max_per_clip_keeps_highest_scoring(self) -> None:
        frames = (
            [frame(i / RATE, sharpness=0.95) for i in range(12)]
            + bad_frames(16, start=12)
            + [frame((28 + i) / RATE, sharpness=0.7, detail=0.6) for i in range(12)]
            + bad_frames(16, start=40)
            + [frame((56 + i) / RATE, sharpness=0.85) for i in range(12)]
        )
        s = ss.settings_from_dict(
            # bad frames score ~0.64, the weakest good run ~0.87 — threshold sits between
            {
                "max_segments_per_clip": 2,
                "score_threshold_lo": 0.85,
                "handles_samples": 0,
                "dip_sec": 0,
            }
        )
        out = segs(frames, s)
        assert [seg["start_sec"] for seg in out] == [0.0, 14.0]

    def test_force_include_survives_threshold_and_cap(self) -> None:
        inc = ss.FLAG_BITS["low_light"]
        frames = (
            bad_frames(8)
            + [frame((8 + i) / RATE, sharpness=0.1, detail=0.1, flags=inc) for i in range(10)]
            + bad_frames(8, start=18)
        )
        s = ss.settings_from_dict(
            {"flags": {"low_light": "include"}, "max_segments_per_clip": 0, "handles_samples": 0}
        )
        out = segs(frames, s)
        assert len(out) == 1
        assert out[0]["forced"] is True
        assert "low_light" in out[0]["flags"]

    def test_segment_fields_and_camera_summary(self) -> None:
        out = segs(good_frames(16, pan=0.05))
        seg = out[0]
        assert seg["camera"]["class"] == "pan_right"
        assert seg["group_scores"]["focus"] is not None
        assert seg["peak_sec"] >= seg["start_sec"]
        assert seg["duration_sec"] == pytest.approx(seg["end_sec"] - seg["start_sec"])

    def test_select_reaching_last_sample_runs_to_clip_end(self) -> None:
        # 40 samples at 4/s cover 10.0 s, but the clip is 10.04 s long: no 1-frame sliver.
        out = ss.score_clip(
            {"frames": good_frames(40), "sampling_rate": RATE, "duration_sec": 10.04},
            ss.default_settings(),
        )["segments"]
        assert out[-1]["end_sec"] == pytest.approx(10.04)

    def test_empty_clip_yields_nothing(self) -> None:
        assert (
            ss.score_clip(
                {"frames": [], "sampling_rate": 4.0, "duration_sec": 0}, ss.default_settings()
            )["segments"]
            == []
        )


# ── Camera class, bands, summary ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("pan", "tilt", "zoom", "twist", "expected"),
    [
        (0.0, 0.0, 0.0, 0.0, "static"),
        (0.05, 0.0, 0.0, 0.0, "pan_right"),
        (-0.05, 0.0, 0.0, 0.0, "pan_left"),
        (0.0, 0.05, 0.0, 0.0, "tilt_down"),
        (0.0, -0.05, 0.0, 0.0, "tilt_up"),
        (0.0, 0.0, 0.1, 0.0, "zoom_in"),
        (0.0, 0.0, -0.1, 0.0, "zoom_out"),
        (0.0, 0.0, 0.0, 0.03, "twist_cw"),
        (0.0, 0.0, 0.0, -0.03, "twist_ccw"),
        (0.04, 0.04, 0.0, 0.0, "mixed"),
    ],
)
def test_camera_class(pan: float, tilt: float, zoom: float, twist: float, expected: str) -> None:
    assert ss.camera_class(pan, tilt, zoom, twist) == expected


def test_camera_axes_are_per_second() -> None:
    # Units are per second of footage: slow handheld drift is static, a gentle
    # pan across a third of the frame in ten seconds is still a pan.
    assert ss.camera_class(0.01, 0.005, 0.0, 0.0) == "static"
    assert ss.camera_class(0.05, 0.0, 0.0, 0.0) == "pan_right"


def test_static_class_agrees_with_measured_stillness() -> None:
    import selects_metrics

    assert not hasattr(selects_metrics, "PAN_FULL")
    # camera_stillness = 1 - sum(|axis| / scale); "static" is that sum < threshold.
    pan = ss.CAMERA_AXIS_SCALE["pan"] * (ss.CAMERA_STATIC_THRESHOLD - 0.01)
    assert ss.camera_class(pan, 0.0, 0.0, 0.0) == "static"
    pan = ss.CAMERA_AXIS_SCALE["pan"] * (ss.CAMERA_STATIC_THRESHOLD + 0.01)
    assert ss.camera_class(pan, 0.0, 0.0, 0.0) == "pan_right"


def test_every_segment_carries_its_rating_key() -> None:
    # Times on 1/16 s, where Python and JS rounding of .2f used to disagree.
    frames = [frame(i / 16.0) for i in range(80)]
    seg = ss.score_clip(
        {"frames": frames, "sampling_rate": 16.0, "duration_sec": 5.0},
        ss.settings_from_dict({"handles_samples": 0}),
    )["segments"][0]
    assert seg["key"] == ss.rating_key(seg["start_sec"], seg["end_sec"])


def test_marker_bands_are_tertiles() -> None:
    segments = [{"mean_score": s} for s in (0.81, 0.85, 0.9, 0.93, 0.95, 0.99)]
    banded = ss.assign_marker_bands(segments)
    assert [b["marker_color"] for b in banded] == [
        "Red",
        "Red",
        "Yellow",
        "Yellow",
        "Green",
        "Green",
    ]
    assert ss.assign_marker_bands([]) == []
    assert ss.assign_marker_bands([{"mean_score": 0.9}])[0]["marker_color"] == "Green"


def test_summarise_totals() -> None:
    clips = [
        {
            "analysed": True,
            "duration_sec": 10.0,
            "segments": [{"duration_sec": 2.5}, {"duration_sec": 2.5}],
        },
        {"analysed": False, "duration_sec": 10.0, "segments": []},
    ]
    out = ss.summarise(clips)
    assert out == {
        "clip_count": 2,
        "analysed_count": 1,
        "select_count": 2,
        "selected_sec": 5.0,
        "footage_sec": 20.0,
        "selected_pct": 25.0,
    }
