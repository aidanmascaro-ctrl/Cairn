"""
Tests for src/workers/selects_learn.py — ratings, anonymised examples,
deterministic evaluation of proposals, near misses and user presets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import selects
import selects_learn as sl
import selects_score as ss
import sidecar

RATE = 4.0


def row(t: float, sharpness: float) -> list[float]:
    base = dict.fromkeys(ss.COLUMNS, 0.0)
    base.update(
        t=t,
        sharpness=sharpness,
        detail=sharpness,
        focus_stability=1.0,
        headroom=1.0,
        luma=0.45,
        contrast=0.4,
        flicker=1.0,
        camera_stillness=1.0,
        subject_stillness=1.0,
        motion_smoothness=1.0,
        motion_confidence=0.9,
    )
    return [base[c] for c in ss.COLUMNS]


def block(levels: list[tuple[int, float]]) -> dict[str, Any]:
    """levels: [(sample_count, sharpness), ...] laid end to end."""
    frames: list[list[float]] = []
    for count, sharp in levels:
        frames.extend(row(len(frames) / RATE, sharp) for _ in range(count))
    return {
        "version": selects.BLOCK_VERSION,
        "sampling_rate": RATE,
        "requested_rate": RATE,
        "fps": 25.0,
        "duration_sec": len(frames) / RATE,
        "frames": frames,
    }


@pytest.fixture
def shoot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[str]]:
    folder = tmp_path / "shoot"
    cam = folder / "01_VIDEOS" / "A_CAM"
    cam.mkdir(parents=True)
    paths = []
    # clip 1: sharp 0-5 s (~0.93), soft 5-10 s (~0.64), mid 10-15 s (~0.75) ; clip 2: all sharp 0-8 s
    layouts = [[(20, 0.95), (20, 0.1), (20, 0.375)], [(32, 0.95)]]
    for i, layout in enumerate(layouts, start=1):
        clip = cam / f"C000{i}.MP4"
        clip.write_bytes(b"")
        sidecar.merge_clip(
            folder, str(clip), updates={"selects": block(layout)}, new_stage="selects"
        )
        paths.append(str(clip))
    monkeypatch.setattr(sl, "USER_PRESETS_PATH", tmp_path / "home" / "selects_presets.json")
    selects._invalidate_cache(folder)
    return folder, paths


class TestRatings:
    def test_rate_stores_and_clears(self, shoot: tuple[Path, list[str]]) -> None:
        folder, (clip, _) = shoot
        sl.rate(folder, clip, 0.0, 5.0, "keep")
        stored = sidecar.read_clip(folder, clip)["select_ratings"]
        assert stored["0.00-5.00"]["rating"] == "keep"
        assert stored["0.00-5.00"]["source"] == "select"
        sl.rate(folder, clip, 0.0, 5.0, None)
        assert sidecar.read_clip(folder, clip)["select_ratings"] == {}

    def test_rate_keeps_other_sidecar_data(self, shoot: tuple[Path, list[str]]) -> None:
        folder, (clip, _) = shoot
        sl.rate(folder, clip, 0.0, 5.0, "reject")
        record = sidecar.read_clip(folder, clip)
        assert "selects" in record["stages_complete"]
        assert record["selects"]["frames"]

    @pytest.mark.parametrize(
        ("start", "end", "rating", "source"),
        [
            (0, 5, "maybe", "select"),
            (5, 5, "keep", "select"),
            (-1, 5, "keep", "select"),
            (0, 5, "keep", "other"),
        ],
    )
    def test_rate_validates(
        self, shoot: tuple[Path, list[str]], start: float, end: float, rating: str, source: str
    ) -> None:
        folder, (clip, _) = shoot
        with pytest.raises(ValueError):
            sl.rate(folder, clip, start, end, rating, source)


class TestExamples:
    def test_examples_are_anonymised_numbers(self, shoot: tuple[Path, list[str]]) -> None:
        folder, (c1, c2) = shoot
        sl.rate(folder, c1, 0.0, 5.0, "keep")
        sl.rate(folder, c1, 5.0, 10.0, "reject")
        sl.rate(folder, c2, 0.0, 8.0, "keep", source="near_miss")
        ex = sl.examples(folder)
        assert [e["clip"] for e in ex] == ["clip 1", "clip 1", "clip 2"]
        assert all("C000" not in str(e) for e in ex)
        keep, reject = ex[0], ex[1]
        assert keep["sharpness"] > reject["sharpness"]
        assert keep["samples"] == 20
        assert keep["camera_class"] == "static"
        assert ex[2]["source"] == "near_miss"

    def test_summarise_range_outside_clip(self) -> None:
        assert sl.summarise_range(block([(8, 0.9)]), 10.0, 12.0) is None
        assert sl.summarise_range({"frames": []}, 0.0, 1.0) is None


class TestEvaluate:
    def test_evaluate_scores_each_rating(self, shoot: tuple[Path, list[str]]) -> None:
        folder, (c1, _) = shoot
        sl.rate(folder, c1, 0.0, 5.0, "keep")
        sl.rate(folder, c1, 5.0, 10.0, "reject")
        sl.rate(folder, c1, 10.0, 15.0, "keep")  # the mid section — below 0.8 by default
        out = sl.evaluate(folder, ss.default_settings())
        assert (out["total"], out["correct"]) == (3, 2)
        assert out["keeps_kept"] == 1 and out["keeps_total"] == 2
        assert out["rejects_dropped"] == 1
        looser = ss.settings_from_dict({"score_threshold_lo": 0.7, "dip_sec": 0})
        assert sl.evaluate(folder, looser)["correct"] == 3

    def test_evaluate_with_no_ratings(self, shoot: tuple[Path, list[str]]) -> None:
        folder, _ = shoot
        assert sl.evaluate(folder, ss.default_settings())["accuracy"] == 0.0


def test_near_misses_sit_just_under_threshold() -> None:
    b = block([(20, 0.95), (20, 0.1), (20, 0.375)])
    settings = ss.settings_from_dict({"dip_sec": 0})
    selected = ss.score_clip(b, settings)["segments"]
    misses = ss.near_misses(b, settings, selected)
    assert [m["core_start_sec"] for m in misses] == [10.0]
    assert all(ss.coverage(selected, m["start_sec"], m["end_sec"]) < 0.25 for m in misses)


def test_rescore_returns_near_misses_and_ratings(shoot: tuple[Path, list[str]]) -> None:
    folder, (c1, _) = shoot
    sl.rate(folder, c1, 0.0, 5.0, "keep")
    out = selects.rescore(folder, ss.settings_from_dict({"dip_sec": 0}), persist=False)
    first = out["clips"][0]
    assert first["near_misses"]
    assert "0.00-5.00" in first["ratings"]


class TestLearnContext:
    def test_not_ready_until_enough_ratings(self, shoot: tuple[Path, list[str]]) -> None:
        folder, (c1, _) = shoot
        sl.rate(folder, c1, 0.0, 5.0, "keep")
        ctx = sl.learn_context(folder, ss.default_settings())
        assert ctx["ready"] is False
        assert "Rate at least" in ctx["reason"]

    def test_ready_with_mixed_ratings(self, shoot: tuple[Path, list[str]]) -> None:
        folder, (c1, _) = shoot
        for i in range(3):
            sl.rate(folder, c1, i, i + 1.5, "keep")
            sl.rate(folder, c1, 5 + i, 6.5 + i, "reject")
        ctx = sl.learn_context(folder, ss.default_settings())
        assert ctx["ready"] is True
        assert ctx["baseline"]["total"] == 6
        assert ctx["presets"][0]["id"] == "all-rounder"


class TestUserPresets:
    def test_save_list_and_resolve(self, shoot: tuple[Path, list[str]]) -> None:
        tuned = ss.settings_from_dict({"score_threshold_lo": 0.7})
        entry = sl.save_user_preset("Aidan's Taste!", tuned, "learned", "because", 0.9)
        assert entry["id"] == "user-aidan-s-taste"
        ids = [p["id"] for p in sl.list_all_presets()]
        assert ids[-1] == "user-aidan-s-taste"
        resolved = sl.resolve_preset("user-aidan-s-taste")
        assert resolved.score_threshold_lo == 0.7
        assert resolved.preset_id == "user-aidan-s-taste"

    def test_saving_same_name_replaces(self, shoot: tuple[Path, list[str]]) -> None:
        sl.save_user_preset("Mine", ss.default_settings())
        sl.save_user_preset("Mine", ss.settings_from_dict({"dip_sec": 1.0}))
        user = [p for p in sl.list_all_presets() if not p["builtin"]]
        assert len(user) == 1
        assert user[0]["settings"]["dip_sec"] == 1.0

    def test_blank_name_rejected(self, shoot: tuple[Path, list[str]]) -> None:
        with pytest.raises(ValueError):
            sl.save_user_preset("   ", ss.default_settings())

    def test_corrupt_file_is_ignored(self, shoot: tuple[Path, list[str]]) -> None:
        sl.USER_PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
        sl.USER_PRESETS_PATH.write_text("{nope")
        assert all(p["builtin"] for p in sl.list_all_presets())

    def test_unknown_preset(self) -> None:
        with pytest.raises(KeyError):
            sl.resolve_preset("user-nobody")
