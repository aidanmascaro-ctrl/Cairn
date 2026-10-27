"""
Stage 5 — Selects: scoring + segmentation (pure, no I/O).

Takes the per-sample measurements stored in a clip's sidecar (`selects.frames`)
and a `SelectsSettings`, and returns per-sample scores plus the runs of
qualifying samples that become selects. Every tuning change in the UI re-runs
this over the stored measurements; nothing is ever re-analysed.

See specs/selects.md §3 for the rules this file implements.
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

# ── Column layout of the stored measurements ─────────────────────────────────

COLUMNS: tuple[str, ...] = (
    "t",
    "sharpness",
    "detail",
    "focus_stability",
    "headroom",
    "luma",
    "contrast",
    "flicker",
    "camera_stillness",
    "subject_stillness",
    "motion_smoothness",
    "pan",
    "tilt",
    "zoom",
    "twist",
    "motion_confidence",
    "flags",
)
COL: dict[str, int] = {name: i for i, name in enumerate(COLUMNS)}

FLAG_BITS: dict[str, int] = {
    "clipping": 1,
    "duplicate": 2,
    "solid_color": 4,
    "hard_cut": 8,
    "motion_blur": 16,
    "shaky": 32,
    "low_light": 64,
}
FLAG_NAMES: tuple[str, ...] = tuple(FLAG_BITS)

TIERS: tuple[str, ...] = ("exclude", "disfavour", "neutral", "favour", "include")
DISFAVOUR_MULTIPLIER = 0.6
FAVOUR_MULTIPLIER = 1.2

GROUPS: tuple[str, ...] = ("focus", "exposure", "motion")

# sub-metric → (group, measurement column). Targets use the column as the value
# and the settings' target as the aim point.
SUB_METRICS: dict[str, tuple[str, str]] = {
    "sharpness": ("focus", "sharpness"),
    "detail": ("focus", "detail"),
    "focus_stability": ("focus", "focus_stability"),
    "headroom": ("exposure", "headroom"),
    "flicker": ("exposure", "flicker"),
    "luma_target": ("exposure", "luma"),
    "contrast_target": ("exposure", "contrast"),
    "camera_stillness": ("motion", "camera_stillness"),
    "subject_stillness": ("motion", "subject_stillness"),
    "motion_smoothness": ("motion", "motion_smoothness"),
}
TARGET_METRICS: frozenset[str] = frozenset({"luma_target", "contrast_target"})
TARGET_FALLOFF = 0.5  # a target metric reaches 0 this far from the aim point

CAMERA_AXES: tuple[str, ...] = ("pan", "tilt", "zoom", "twist")
# Per-sample magnitude that counts as "a full move" on each axis.
CAMERA_AXIS_SCALE: dict[str, float] = {"pan": 0.04, "tilt": 0.04, "zoom": 0.02, "twist": 0.02}
CAMERA_STATIC_THRESHOLD = 0.25
CAMERA_DOMINANT_RATIO = 1.5

SAMPLING_RATES: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)
EVERY_FRAME = 0.0  # sentinel: sample every frame

MARKER_COLOURS: tuple[str, ...] = ("Green", "Yellow", "Red")


# ── Settings ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SelectsSettings:
    preset_id: str = "all-rounder"
    sampling_rate: float = 4.0
    group_weights: dict[str, float] = field(
        default_factory=lambda: {"focus": 0.6, "exposure": 0.2, "motion": 0.2}
    )
    sub_weights: dict[str, float] = field(
        default_factory=lambda: {
            "sharpness": 0.5,
            "detail": 0.3,
            "focus_stability": 0.4,
            "headroom": 1.0,
            "flicker": 0.0,
            "luma_target": 0.0,
            "contrast_target": 0.0,
            "camera_stillness": 0.4,
            "subject_stillness": 0.3,
            "motion_smoothness": 0.6,
        }
    )
    targets: dict[str, float] = field(default_factory=lambda: {"luma": 0.45, "contrast": 0.4})
    score_threshold_lo: float = 0.80
    score_threshold_hi: float = 1.0
    min_segment_sec: float = 2.0
    max_segment_sec: float | None = None
    max_segments_per_clip: int | None = None
    dip_sec: float = 3.0
    handles_samples: int = 3
    flags: dict[str, str] = field(
        default_factory=lambda: {
            "clipping": "exclude",
            "duplicate": "neutral",
            "solid_color": "exclude",
            "hard_cut": "neutral",
            "motion_blur": "neutral",
            "shaky": "neutral",
            "low_light": "neutral",
        }
    )
    camera_filter: dict[str, Any] = field(
        default_factory=lambda: {
            "enabled": False,
            "static_only": False,
            "pan": 0.0,
            "tilt": 0.0,
            "zoom": 0.0,
            "twist": 0.0,
            "strictness": 0.0,
            "mirror": False,
        }
    )
    metrics_enabled: bool = True
    flags_enabled: bool = True


def default_settings() -> SelectsSettings:
    return SelectsSettings()


def _clamp(value: Any, lo: float, hi: float, fallback: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(v):
        return fallback
    return max(lo, min(hi, v))


def _optional_positive(value: Any) -> float | None:
    if value is None or value == "" or value == "none":
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def settings_from_dict(
    raw: dict[str, Any] | None, base: SelectsSettings | None = None
) -> SelectsSettings:
    """
    Build settings from a JSON-ish dict, validating every field against the
    default. Unknown keys are ignored; bad values fall back to the base value.
    Values arriving over IPC are untrusted, so everything is clamped here.
    """
    base = base or default_settings()
    raw = raw if isinstance(raw, dict) else {}

    group_raw = _section(raw, "group_weights")
    sub_raw = _section(raw, "sub_weights")
    target_raw = _section(raw, "targets")
    flag_raw = _section(raw, "flags")
    group_weights = {g: _clamp(group_raw.get(g), 0.0, 1.0, base.group_weights[g]) for g in GROUPS}
    sub_weights = {s: _clamp(sub_raw.get(s), -1.0, 1.0, base.sub_weights[s]) for s in SUB_METRICS}
    targets = {k: _clamp(target_raw.get(k), 0.0, 1.0, base.targets[k]) for k in base.targets}
    flags = {f: (flag_raw[f] if flag_raw.get(f) in TIERS else base.flags[f]) for f in FLAG_NAMES}
    cam_raw = _section(raw, "camera_filter")
    camera_filter: dict[str, Any] = {
        "enabled": bool(cam_raw.get("enabled", base.camera_filter["enabled"])),
        "static_only": bool(cam_raw.get("static_only", base.camera_filter["static_only"])),
        "strictness": _clamp(cam_raw.get("strictness"), 0.0, 1.0, base.camera_filter["strictness"]),
        "mirror": bool(cam_raw.get("mirror", base.camera_filter["mirror"])),
    }
    for axis in CAMERA_AXES:
        camera_filter[axis] = _clamp(cam_raw.get(axis), -1.0, 1.0, base.camera_filter[axis])

    lo = _clamp(raw.get("score_threshold_lo"), 0.0, 1.0, base.score_threshold_lo)
    hi = _clamp(raw.get("score_threshold_hi"), 0.0, 1.0, base.score_threshold_hi)
    if hi < lo:
        lo, hi = hi, lo

    max_per_clip_raw = _optional_positive(
        raw.get("max_segments_per_clip", base.max_segments_per_clip)
    )
    sampling = _clamp(raw.get("sampling_rate"), 0.0, 120.0, base.sampling_rate)

    return SelectsSettings(
        preset_id=_preset_id(raw.get("preset_id"), base.preset_id),
        sampling_rate=sampling,
        group_weights=group_weights,
        sub_weights=sub_weights,
        targets=targets,
        score_threshold_lo=lo,
        score_threshold_hi=hi,
        min_segment_sec=_clamp(raw.get("min_segment_sec"), 0.0, 3600.0, base.min_segment_sec),
        max_segment_sec=_optional_positive(raw.get("max_segment_sec", base.max_segment_sec)),
        # Round up: a cap of 0.5 means "at least one", never "drop everything".
        max_segments_per_clip=(
            min(10_000, math.ceil(max_per_clip_raw)) if max_per_clip_raw is not None else None
        ),
        dip_sec=_clamp(raw.get("dip_sec"), 0.0, 60.0, base.dip_sec),
        handles_samples=int(_clamp(raw.get("handles_samples"), 0, 50, base.handles_samples)),
        flags=flags,
        camera_filter=camera_filter,
        metrics_enabled=bool(raw.get("metrics_enabled", base.metrics_enabled)),
        flags_enabled=bool(raw.get("flags_enabled", base.flags_enabled)),
    )


_PRESET_ID = re.compile(r"^[a-z0-9-]{1,48}$")


def _preset_id(value: Any, fallback: str) -> str:
    """Short slug only. Settings files can come from someone else's shoot folder,
    and this id reaches the Claude prompt, so free text must not pass."""
    return value if isinstance(value, str) and _PRESET_ID.match(value) else fallback


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    """A nested settings block, or {} when it is missing or the wrong type."""
    value = raw.get(key)
    return value if isinstance(value, dict) else {}


def settings_to_dict(s: SelectsSettings) -> dict[str, Any]:
    return {
        "preset_id": s.preset_id,
        "sampling_rate": s.sampling_rate,
        "group_weights": dict(s.group_weights),
        "sub_weights": dict(s.sub_weights),
        "targets": dict(s.targets),
        "score_threshold_lo": s.score_threshold_lo,
        "score_threshold_hi": s.score_threshold_hi,
        "min_segment_sec": s.min_segment_sec,
        "max_segment_sec": s.max_segment_sec,
        "max_segments_per_clip": s.max_segments_per_clip,
        "dip_sec": s.dip_sec,
        "handles_samples": s.handles_samples,
        "flags": dict(s.flags),
        "camera_filter": dict(s.camera_filter),
        "metrics_enabled": s.metrics_enabled,
        "flags_enabled": s.flags_enabled,
    }


# ── Per-sample scoring ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScoredFrames:
    t: np.ndarray  # sample times (s)
    score: np.ndarray  # final per-sample score 0..1
    usable: np.ndarray  # bool — False when an exclude-tier flag fired
    forced: np.ndarray  # bool — True when an include-tier flag fired
    groups: dict[str, np.ndarray]  # per-group scores (NaN when the group contributes nothing)
    flags: np.ndarray  # int bitmask per sample


def _frames_array(frames: list[list[float]]) -> np.ndarray:
    arr = np.asarray(frames, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != len(COLUMNS):
        raise ValueError(f"frames must be n x {len(COLUMNS)}; got shape {arr.shape}")
    return arr


def _group_scores(arr: np.ndarray, s: SelectsSettings) -> dict[str, np.ndarray]:
    n = arr.shape[0]
    out: dict[str, np.ndarray] = {}
    for group in GROUPS:
        acc = np.zeros(n)
        total = 0.0
        for sub, (g, column) in SUB_METRICS.items():
            if g != group:
                continue
            w = s.sub_weights.get(sub, 0.0)
            if w == 0.0:
                continue
            v = arr[:, COL[column]]
            if sub in TARGET_METRICS:
                aim = s.targets["luma" if column == "luma" else "contrast"]
                term = np.clip(1.0 - np.abs(v - aim) / TARGET_FALLOFF, 0.0, 1.0)
            else:
                term = v if w > 0 else 1.0 - v
            acc += abs(w) * term
            total += abs(w)
        out[group] = acc / total if total > 0 else np.full(n, np.nan)
    return out


def _flag_effects(
    flags: np.ndarray, s: SelectsSettings
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (multiplier, usable, forced) per sample."""
    n = flags.shape[0]
    mult = np.ones(n)
    usable = np.ones(n, dtype=bool)
    forced = np.zeros(n, dtype=bool)
    if not s.flags_enabled:
        return mult, usable, forced
    for name, bit in FLAG_BITS.items():
        fired = (flags & bit) != 0
        tier = s.flags.get(name, "neutral")
        if tier == "exclude":
            usable &= ~fired
        elif tier == "include":
            forced |= fired
        elif tier == "disfavour":
            mult = np.where(fired, mult * DISFAVOUR_MULTIPLIER, mult)
        elif tier == "favour":
            mult = np.where(fired, mult * FAVOUR_MULTIPLIER, mult)
    forced &= usable  # exclude beats include
    return mult, usable, forced


def _camera_match(arr: np.ndarray, s: SelectsSettings) -> np.ndarray:
    cf = s.camera_filter
    n = arr.shape[0]
    if not cf.get("enabled"):
        return np.ones(n)
    if cf.get("static_only"):
        return arr[:, COL["camera_stillness"]]
    matches: list[np.ndarray] = []
    sharpen = 1.0 + 3.0 * float(cf.get("strictness", 0.0))
    for axis in CAMERA_AXES:
        want = float(cf.get(axis, 0.0))
        if want == 0.0:
            continue
        v = arr[:, COL[axis]]
        magnitude = np.minimum(1.0, np.abs(v) / CAMERA_AXIS_SCALE[axis])
        same_sign = np.sign(v) == np.sign(want)
        direction = np.where(same_sign | bool(cf.get("mirror")), 1.0, 0.0)
        strength = np.minimum(1.0, magnitude / abs(want))
        matches.append((direction * strength) ** sharpen)
    if not matches:
        return np.ones(n)
    return np.mean(np.stack(matches), axis=0)


def score_frames(frames: list[list[float]], settings: SelectsSettings) -> ScoredFrames:
    arr = _frames_array(frames)
    n = arr.shape[0]
    flags = arr[:, COL["flags"]].astype(np.int64)
    groups = _group_scores(arr, settings)

    if settings.metrics_enabled:
        num = np.zeros(n)
        den = 0.0
        for g in GROUPS:
            gw = settings.group_weights.get(g, 0.0)
            if gw <= 0 or np.isnan(groups[g]).all():
                continue
            num += gw * groups[g]
            den += gw
        base = num / den if den > 0 else np.ones(n)
    else:
        base = np.ones(n)

    mult, usable, forced = _flag_effects(flags, settings)
    # NaN-safe: a damaged measurement scores 0 rather than poisoning JSON or comparisons.
    score = np.clip(np.nan_to_num(base * mult * _camera_match(arr, settings), nan=0.0), 0.0, 1.0)
    return ScoredFrames(
        t=arr[:, COL["t"]], score=score, usable=usable, forced=forced, groups=groups, flags=flags
    )


# ── Segmentation ─────────────────────────────────────────────────────────────


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous True runs as [start, end) index pairs."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


def _bridge_dips(
    runs: list[tuple[int, int]], usable: np.ndarray, dip_samples: float
) -> list[tuple[int, int]]:
    """Merge neighbouring runs whose gap is shorter than the dip and contains no unusable sample."""
    merged: list[tuple[int, int]] = []
    for start, end in runs:
        if merged:
            prev_start, prev_end = merged[-1]
            gap = start - prev_end
            if gap < dip_samples and bool(usable[prev_end:start].all()):
                merged[-1] = (prev_start, end)
                continue
        merged.append((start, end))
    return merged


def _split_long(
    runs: list[tuple[int, int]], max_samples: int | None, min_samples: float = 1.0
) -> list[tuple[int, int]]:
    """Split runs longer than max into equal pieces, never into pieces shorter than min."""
    if max_samples is None:
        return runs
    max_samples = max(1, max_samples)
    out: list[tuple[int, int]] = []
    for start, end in runs:
        length = end - start
        if length <= max_samples:
            out.append((start, end))
            continue
        pieces = math.ceil(length / max_samples)
        # When max and min conflict, the minimum wins: fewer, longer pieces.
        pieces = max(1, min(pieces, math.floor(length / max(1.0, min_samples))))
        edges = np.linspace(start, end, pieces + 1).round().astype(int)
        out.extend((int(a), int(b)) for a, b in itertools.pairwise(edges) if b > a)
    return out


def _add_handles(
    runs: list[tuple[int, int]], usable: np.ndarray, handles: int, n: int
) -> list[tuple[int, int]]:
    """Extend each run by up to `handles` samples each side, stopping at an unusable sample, the neighbour, or the clip edge."""
    if handles <= 0:
        return runs
    out: list[tuple[int, int]] = []
    for i, (start, end) in enumerate(runs):
        lo_limit = out[-1][1] if out else 0
        hi_limit = runs[i + 1][0] if i + 1 < len(runs) else n
        new_start = start
        for _ in range(handles):
            if new_start - 1 < lo_limit or not usable[new_start - 1]:
                break
            new_start -= 1
        new_end = end
        for _ in range(handles):
            if new_end >= hi_limit or not usable[new_end]:
                break
            new_end += 1
        out.append((new_start, new_end))
    return out


def camera_class(pan: float, tilt: float, zoom: float, twist: float) -> str:
    norms = {
        "pan": abs(pan) / CAMERA_AXIS_SCALE["pan"],
        "tilt": abs(tilt) / CAMERA_AXIS_SCALE["tilt"],
        "zoom": abs(zoom) / CAMERA_AXIS_SCALE["zoom"],
        "twist": abs(twist) / CAMERA_AXIS_SCALE["twist"],
    }
    if sum(norms.values()) < CAMERA_STATIC_THRESHOLD:
        return "static"
    axis, top = max(norms.items(), key=lambda kv: kv[1])
    others = [v for k, v in norms.items() if k != axis]
    if top < CAMERA_DOMINANT_RATIO * max(others, default=0.0):
        return "mixed"
    signed = {"pan": pan, "tilt": tilt, "zoom": zoom, "twist": twist}[axis]
    labels = {
        "pan": ("pan_left", "pan_right"),
        "tilt": ("tilt_up", "tilt_down"),
        "zoom": ("zoom_out", "zoom_in"),
        "twist": ("twist_ccw", "twist_cw"),
    }[axis]
    return labels[1] if signed > 0 else labels[0]


def _segment_record(
    arr: np.ndarray,
    scored: ScoredFrames,
    core: tuple[int, int],
    full: tuple[int, int],
    rate: float,
    duration_sec: float,
) -> dict[str, Any]:
    cs, ce = core
    fs, fe = full
    seg_scores = scored.score[cs:ce]
    peak_idx = int(cs + np.argmax(seg_scores))
    group_scores = {
        g: (
            None
            if np.isnan(scored.groups[g][cs:ce]).all()
            else round(float(np.nanmean(scored.groups[g][cs:ce])), 4)
        )
        for g in GROUPS
    }
    fired = int(np.bitwise_or.reduce(scored.flags[cs:ce])) if ce > cs else 0
    cam = {axis: float(np.mean(arr[cs:ce, COL[axis]])) for axis in CAMERA_AXES}
    step = 1.0 / rate if rate > 0 else 0.0
    start_sec = float(scored.t[fs])
    end_sec = float(scored.t[fe - 1]) + step
    if duration_sec > 0:
        # A select that holds the clip's last sample runs to the true end (no sliver
        # left behind). Anywhere else, never past the clip.
        end_sec = duration_sec if fe == len(scored.t) else min(end_sec, duration_sec)
    return {
        "start_sec": round(start_sec, 4),
        "end_sec": round(end_sec, 4),
        "duration_sec": round(end_sec - start_sec, 4),
        "core_start_sec": round(float(scored.t[cs]), 4),
        "core_end_sec": round(min(duration_sec, float(scored.t[ce - 1]) + step), 4),
        "start_sample": fs,
        "end_sample": fe,
        "mean_score": round(float(seg_scores.mean()), 4),
        "peak_score": round(float(seg_scores.max()), 4),
        "peak_sec": round(float(scored.t[peak_idx]), 4),
        "group_scores": group_scores,
        "flags": [name for name, bit in FLAG_BITS.items() if fired & bit],
        "camera": {
            **{k: round(v, 5) for k, v in cam.items()},
            "confidence": round(float(np.mean(arr[cs:ce, COL["motion_confidence"]])), 4),
            "class": camera_class(cam["pan"], cam["tilt"], cam["zoom"], cam["twist"]),
        },
        "forced": bool(scored.forced[cs:ce].any()),
    }


def derive_segments(
    frames: list[list[float]],
    scored: ScoredFrames,
    settings: SelectsSettings,
    rate: float,
    duration_sec: float,
) -> list[dict[str, Any]]:
    arr = _frames_array(frames)
    n = arr.shape[0]
    if n == 0 or rate <= 0:
        return []

    in_range = (scored.score >= settings.score_threshold_lo) & (
        scored.score <= settings.score_threshold_hi
    )
    qualifying = scored.forced | (in_range & scored.usable)

    runs = _runs(qualifying)
    runs = _bridge_dips(runs, scored.usable, settings.dip_sec * rate)

    min_samples = max(1.0, settings.min_segment_sec * rate)
    runs = [(a, b) for a, b in runs if (b - a) >= min_samples]
    max_samples = round(settings.max_segment_sec * rate) if settings.max_segment_sec else None
    runs = _split_long(runs, max_samples, min_samples)

    full_runs = _add_handles(runs, scored.usable, settings.handles_samples, n)
    segments = [
        _segment_record(arr, scored, core, full, rate, duration_sec)
        for core, full in zip(runs, full_runs, strict=True)
    ]

    cap = settings.max_segments_per_clip
    if cap is not None and len(segments) > cap:
        forced = [s for s in segments if s["forced"]]
        rest = sorted((s for s in segments if not s["forced"]), key=lambda s: -s["mean_score"])
        keep = forced + rest[: max(0, cap - len(forced))]
        segments = sorted(keep, key=lambda s: s["start_sec"])
    return segments


# ── Clip-level entry point ───────────────────────────────────────────────────


def score_clip(selects_block: dict[str, Any], settings: SelectsSettings) -> dict[str, Any]:
    """
    Score one clip's stored measurements. Returns:
      {"scores": [..per-sample rounded..], "segments": [...], "sample_count": n,
       "qualifying_count": q}
    """
    frames = selects_block.get("frames") or []
    rate = float(selects_block.get("sampling_rate") or 0.0)
    duration = float(selects_block.get("duration_sec") or 0.0)
    if not frames:
        return {"scores": [], "segments": [], "sample_count": 0, "qualifying_count": 0}
    scored = score_frames(frames, settings)
    segments = derive_segments(frames, scored, settings, rate, duration)
    in_range = (scored.score >= settings.score_threshold_lo) & (
        scored.score <= settings.score_threshold_hi
    )
    qualifying = int((scored.forced | (in_range & scored.usable)).sum())
    return {
        "scores": [round(float(v), 3) for v in scored.score],
        "usable": [bool(v) for v in scored.usable],
        "segments": segments,
        "sample_count": len(frames),
        "qualifying_count": qualifying,
    }


NEAR_MISS_MARGIN = 0.1  # near misses = selects at threshold - margin that aren't selected


def coverage(segments: list[dict[str, Any]], start: float, end: float) -> float:
    """Fraction of [start, end) covered by the given segments."""
    span = end - start
    if span <= 0:
        return 0.0
    covered = sum(max(0.0, min(end, s["end_sec"]) - max(start, s["start_sec"])) for s in segments)
    return min(1.0, covered / span)


def near_misses(
    block: dict[str, Any], settings: SelectsSettings, segments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Runs that become selects with the threshold lowered by NEAR_MISS_MARGIN, minus what is already selected."""
    looser = replace(
        settings, score_threshold_lo=max(0.0, settings.score_threshold_lo - NEAR_MISS_MARGIN)
    )
    candidates = score_clip(block, looser)["segments"]
    return [c for c in candidates if coverage(segments, c["start_sec"], c["end_sec"]) < 0.25]


def assign_marker_bands(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Three-band split by mean score across the given selects: top third Green, middle Yellow, bottom Red."""
    if not segments:
        return []
    # Rank from the top so a lone select (or the best of two) is Green.
    order = sorted(range(len(segments)), key=lambda i: -segments[i]["mean_score"])
    n = len(segments)
    colours = [""] * n
    for rank, idx in enumerate(order):
        colours[idx] = MARKER_COLOURS[min(2, (rank * 3) // n)]
    return [{**seg, "marker_color": colours[i]} for i, seg in enumerate(segments)]


def summarise(clips: list[dict[str, Any]]) -> dict[str, Any]:
    """Totals across scored clips: how many selects, how long, what share of footage."""
    total_footage = sum(float(c.get("duration_sec") or 0.0) for c in clips)
    segs = [s for c in clips for s in c.get("segments", [])]
    selected = sum(float(s["duration_sec"]) for s in segs)
    return {
        "clip_count": len(clips),
        "analysed_count": sum(1 for c in clips if c.get("analysed")),
        "select_count": len(segs),
        "selected_sec": round(selected, 2),
        "footage_sec": round(total_footage, 2),
        "selected_pct": round(100.0 * selected / total_footage, 1) if total_footage > 0 else 0.0,
    }
