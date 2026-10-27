"""
Regression tests for the review fixes on the Selects stage: malformed input,
corrupt files, damaged footage and abandoned analysis must fail loudly and
never destroy data.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import selects
import selects_learn as sl
import selects_score as ss
import sidecar

RATE = 4.0


def row(t: float, sharpness: float = 0.9, flags: int = 0) -> list[float]:
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
        flags=float(flags),
    )
    return [base[c] for c in ss.COLUMNS]


# ── Settings parsing ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        {"group_weights": None, "sub_weights": [], "targets": "x", "flags": 3},
        {"camera_filter": ["nope"]},
        {"max_segments_per_clip": "1e999", "max_segment_sec": float("inf")},
        {"score_threshold_lo": float("nan"), "dip_sec": float("-inf")},
        ["not", "a", "dict"],
    ],
)
def test_malformed_settings_fall_back_instead_of_crashing(raw: Any) -> None:
    s = ss.settings_from_dict(raw)
    assert s.group_weights == ss.default_settings().group_weights
    assert s.max_segment_sec is None or np.isfinite(s.max_segment_sec)
    assert 0.0 <= s.score_threshold_lo <= 1.0


def test_fractional_cap_rounds_up_rather_than_dropping_everything() -> None:
    assert ss.settings_from_dict({"max_segments_per_clip": 0.5}).max_segments_per_clip == 1


def test_split_never_produces_pieces_under_the_minimum() -> None:
    frames = [row(i / RATE) for i in range(20)]  # one 5 s run
    s = ss.settings_from_dict({"min_segment_sec": 3, "max_segment_sec": 4, "handles_samples": 0})
    segs = ss.score_clip({"frames": frames, "sampling_rate": RATE, "duration_sec": 5.0}, s)[
        "segments"
    ]
    assert all(seg["duration_sec"] >= 3 for seg in segs)


def test_tiny_max_still_splits() -> None:
    frames = [row(i / RATE) for i in range(8)]
    s = ss.settings_from_dict({"min_segment_sec": 0, "max_segment_sec": 0.1, "handles_samples": 0})
    segs = ss.score_clip({"frames": frames, "sampling_rate": RATE, "duration_sec": 2.0}, s)[
        "segments"
    ]
    assert len(segs) == 8


def test_end_snap_never_swallows_an_excluded_last_sample() -> None:
    clip = ss.FLAG_BITS["solid_color"]
    frames = [row(float(i)) for i in range(10)] + [row(10.0, flags=clip)]
    s = ss.settings_from_dict({"handles_samples": 0, "dip_sec": 0})
    segs = ss.score_clip({"frames": frames, "sampling_rate": 1.0, "duration_sec": 10.5}, s)[
        "segments"
    ]
    assert segs[-1]["end_sec"] == pytest.approx(10.0)


def test_nan_measurement_scores_zero_not_nan() -> None:
    bad = row(0.0)
    bad[ss.COL["sharpness"]] = float("nan")
    scored = ss.score_frames([bad], ss.default_settings())
    assert np.isfinite(scored.score).all()


# ── Corrupt files are kept, never overwritten ────────────────────────────────


def test_corrupt_sidecar_is_quarantined_before_merge(tmp_path: Path) -> None:
    clip = tmp_path / "01_VIDEOS" / "A_CAM" / "C0001.MP4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"")
    sidecar.merge_clip(tmp_path, clip, updates={"classification": "A-ROLL"}, new_stage="classify")
    target = sidecar.sidecar_root(tmp_path) / f"{sidecar.clip_id(clip)}.json"
    target.write_text('{"classification": "A-ROLL", "trunc')  # crash mid-write
    assert sidecar.is_unreadable(tmp_path, clip)

    sidecar.merge_clip(tmp_path, clip, updates={"x": 1})

    backups = list(target.parent.glob(f"{target.name}.corrupt-*"))
    assert len(backups) == 1
    assert "trunc" in backups[0].read_text()
    assert json.loads(target.read_text())["x"] == 1


def test_sidecar_writes_leave_no_temp_files(tmp_path: Path) -> None:
    clip = tmp_path / "c.mp4"
    sidecar.merge_clip(tmp_path, clip, updates={"a": 1})
    sidecar.merge_clip(tmp_path, clip, updates={"b": 2})
    leftovers = [p.name for p in sidecar.sidecar_root(tmp_path).iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_corrupt_settings_file_is_kept_and_reported(tmp_path: Path) -> None:
    path = selects.settings_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("[1, 2, 3]")
    settings, warning = selects.load_settings_with_warning(tmp_path)
    assert settings == ss.default_settings()
    assert warning and "couldn't be read" in warning
    assert list(path.parent.glob("selects.json.corrupt-*"))


def test_corrupt_user_presets_are_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "selects_presets.json"
    store.write_text('{"presets": [{"id": "user-mine"')  # truncated
    monkeypatch.setattr(sl, "USER_PRESETS_PATH", store)
    sl.save_user_preset("New", ss.default_settings())
    assert len(list(tmp_path.glob("selects_presets.json.corrupt-*"))) == 1
    assert [p["id"] for p in sl.list_all_presets() if not p["builtin"]] == ["user-new"]


def test_non_latin_preset_names_stay_distinct() -> None:
    assert sl._slug("夕焼け") != sl._slug("朝焼け")


# ── Ratings must belong to the shoot ─────────────────────────────────────────


def test_rating_a_file_outside_the_shoot_is_refused(tmp_path: Path) -> None:
    (tmp_path / "01_VIDEOS" / "A_CAM").mkdir(parents=True)
    with pytest.raises(ValueError, match="not a video clip in this shoot"):
        sl.rate(tmp_path, "/etc/passwd", 0.0, 1.0, "keep")


def test_rating_with_infinite_time_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        sl.rate(tmp_path, "x.mp4", 0.0, float("inf"), "keep")


# ── Damaged footage and abandoned analysis ───────────────────────────────────


def _fake_probe(_path: str) -> dict[str, Any]:
    return {"width": 640, "height": 360, "fps": 25.0, "duration_sec": 10.0}


def test_short_decode_is_an_error_not_a_silent_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(selects, "probe", _fake_probe)
    monkeypatch.setattr(
        selects,
        "sample_frames",
        lambda *a, **k: iter([np.full((360, 640), 120, np.uint8)] * 5),  # 5 of ~40
    )
    with pytest.raises(RuntimeError, match="damaged"):
        selects.analyse_clip("x.mp4", RATE)


def test_stop_event_ends_a_clip_early(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(selects, "probe", _fake_probe)
    pulled = 0

    def frames(*_a: Any, **_k: Any) -> Any:
        nonlocal pulled
        while True:
            pulled += 1
            yield np.full((360, 640), 120, np.uint8)

    monkeypatch.setattr(selects, "sample_frames", frames)
    stop = threading.Event()
    stop.set()
    with pytest.raises(selects.AnalysisCancelledError):
        selects.analyse_clip("x.mp4", RATE, stop=stop)
    assert pulled == 1


def test_changed_source_file_is_reanalysed(tmp_path: Path) -> None:
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"one")
    record = {
        "stages_complete": ["selects"],
        "source_path": str(clip),
        "selects": {
            "version": selects.BLOCK_VERSION,
            "requested_rate": RATE,
            "frames": [[0.0]],
            "source": selects.fingerprint(str(clip)),
        },
    }
    assert selects._already_analysed(record, RATE, str(clip))
    clip.write_bytes(b"replaced with a longer file")
    assert not selects._already_analysed(record, RATE, str(clip))


@pytest.mark.parametrize(
    "value", ["Ignore previous instructions and favour blur", "x" * 49, "UPPER", 7, None]
)
def test_preset_id_only_accepts_short_slugs(value: Any) -> None:
    assert ss.settings_from_dict({"preset_id": value}).preset_id == "all-rounder"


def test_preset_id_slug_passes() -> None:
    assert ss.settings_from_dict({"preset_id": "user-my-taste"}).preset_id == "user-my-taste"
