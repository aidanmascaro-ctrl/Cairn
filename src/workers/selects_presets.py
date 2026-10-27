"""
Stage 5 — Selects: built-in presets (specs/selects.md §4).

Each preset is a set of overrides on top of the All-Rounder defaults in
selects_score.SelectsSettings. Descriptions are Cairn's own wording.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from selects_score import SelectsSettings, default_settings, settings_from_dict, settings_to_dict

_PRESET_DEFS: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    (
        "all-rounder",
        "All-Rounder",
        "The starting point. Favours crisp focus, clean exposure and a steady camera.",
        {},
    ),
    (
        "talking-head",
        "Talking Head",
        "Interviews and pieces to camera. Focus must hold; the person can move.",
        {
            "group_weights": {"focus": 0.65, "exposure": 0.2, "motion": 0.15},
            "sub_weights": {"camera_stillness": 0.6, "subject_stillness": 0.1},
            "dip_sec": 4.0,
            "min_segment_sec": 3.0,
        },
    ),
    (
        "social-media",
        "Social Media",
        "Short bright moments, capped at a few per clip.",
        {
            "sub_weights": {"contrast_target": 0.4},
            "targets": {"luma": 0.45, "contrast": 0.45},
            "score_threshold_lo": 0.75,
            "min_segment_sec": 1.0,
            "max_segment_sec": 8.0,
            "max_segments_per_clip": 3,
        },
    ),
    (
        "action-sport",
        "Action / Sport",
        "Turns the motion scoring around so movement scores higher.",
        {
            "group_weights": {"focus": 0.4, "exposure": 0.15, "motion": 0.45},
            "sub_weights": {
                "camera_stillness": -0.4,
                "subject_stillness": -0.5,
                "motion_smoothness": 0.6,
            },
            "flags": {"motion_blur": "favour", "shaky": "neutral"},
            "score_threshold_lo": 0.7,
        },
    ),
    (
        "cleanup",
        "Cleanup",
        "Keeps nearly everything and only throws out broken frames.",
        {
            "score_threshold_lo": 0.55,
            "min_segment_sec": 1.0,
            "flags": {
                "clipping": "exclude",
                "solid_color": "exclude",
                "duplicate": "exclude",
                "hard_cut": "exclude",
            },
        },
    ),
    (
        "long-takes",
        "Long Takes",
        "Only long unbroken shots, with short wobbles bridged over.",
        {
            "min_segment_sec": 8.0,
            "dip_sec": 6.0,
            "sub_weights": {"focus_stability": 0.8},
        },
    ),
    (
        "drone-aerial",
        "Drone / Aerial",
        "Steady flowing moves; jitter is thrown out.",
        {
            "sub_weights": {"motion_smoothness": 1.0, "camera_stillness": 0.0},
            "flags": {"shaky": "exclude"},
        },
    ),
    (
        "whip-pans",
        "Whip Pans",
        "Fast blurred moves for transitions.",
        {
            "sub_weights": {"sharpness": -0.3, "camera_stillness": -0.8},
            "flags": {"motion_blur": "favour"},
            "min_segment_sec": 0.5,
            "score_threshold_lo": 0.6,
        },
    ),
    (
        "locked-off",
        "Locked-Off",
        "Tripod shots only.",
        {"camera_filter": {"enabled": True, "static_only": True}},
    ),
)


def _merge_overrides(base: SelectsSettings, overrides: dict[str, Any]) -> SelectsSettings:
    merged: dict[str, Any] = settings_to_dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return settings_from_dict(merged, base)


def preset_settings(preset_id: str) -> SelectsSettings:
    for pid, _name, _desc, overrides in _PRESET_DEFS:
        if pid == preset_id:
            return replace(_merge_overrides(default_settings(), overrides), preset_id=pid)
    raise KeyError(f"Unknown preset: {preset_id!r}")


def list_presets() -> list[dict[str, Any]]:
    return [
        {
            "id": pid,
            "name": name,
            "description": desc,
            "settings": settings_to_dict(preset_settings(pid)),
        }
        for pid, name, desc, _ in _PRESET_DEFS
    ]
