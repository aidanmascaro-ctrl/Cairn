"""
Stage 5 — Selects: per-frame measurements (specs/selects.md §2).

Pure image maths on grayscale frames (uint8 or float 0..1). No I/O.
Every metric lands in 0..1 with 1.0 at the "good" end, except luma/contrast
(fixed scales) and the signed camera-motion axes (per second of footage).

Pipeline per clip:
    statics = [measure_static(f) for f in frames]
    motions = [measure_motion(prev, cur, rate) for prev, cur in pairs]
    rows    = finalise(times, statics, motions)
"""

from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np
from selects_score import CAMERA_AXIS_SCALE, COLUMNS, FLAG_BITS

# ── Calibration constants ──
# Tuned 2026-10-27 against Cutdown's All-Rounder selects on 40 ACME clips (Sony
# XAVC 4K, SLog3 + Rec.709): coordinate descent on 20, checked on the other 20.
# Overlap with Cutdown on the held-out clips went from 0.49 to 0.68 IoU.

GRID = 6  # focus grid cells per side
TOP_CELLS = 3  # sharpness = mean of the sharpest N cells
SHARPNESS_SCALE = 0.01  # Laplacian variance giving 1 - 1/e sharpness
DARK_CELL_LUMA = 0.08  # cells darker than this are down-weighted (noise ≠ detail)
DETAIL_EDGE = 0.08  # Sobel magnitude counted as an edge
DETAIL_FULL = 0.08  # edge fraction that reads as fully detailed
CRUSH_LEVEL = 1.0 / 255.0
BLOW_LEVEL = 254.0 / 255.0
HEADROOM_GAIN = 8.0
CONTRAST_FULL = 0.3
LETTERBOX_LUMA = 0.02
LETTERBOX_MAX_FRACTION = 0.25

MOTION_WIDTH = 480  # long edge for optical flow
MAX_FEATURES = 300
# "Fully moving" speeds come from selects_score.CAMERA_AXIS_SCALE (per second), so
# camera_stillness and the camera classes/filter can never disagree on units.
SUBJECT_DIFF_FULL = 0.08

WINDOW = 2  # temporal window = ±2 samples
FOCUS_STABILITY_STD = 0.08
FLICKER_STD = 0.015
SMOOTHNESS_STD = 0.10

CLIPPING_FRACTION = 0.015
DUPLICATE_DIFF = 0.0015
SOLID_STD = 0.01
HARD_CUT_DIFF = 0.25
HARD_CUT_HIST = 0.5
BLUR_SHARPNESS = 0.25
BLUR_STILLNESS = 0.5
SHAKY_STILLNESS = 0.6
SHAKY_SMOOTHNESS = 0.4
LOW_LIGHT_LUMA = 0.08


# ── Helpers ──────────────────────────────────────────────────────────────────


def to_float(gray: np.ndarray) -> np.ndarray:
    if gray.dtype == np.uint8:
        return gray.astype(np.float32) / 255.0
    return gray.astype(np.float32)


def crop_letterbox(img: np.ndarray) -> np.ndarray:
    """Trim near-black bars from each edge (at most a quarter of the frame per side)."""
    h, w = img.shape
    rows = img.mean(axis=1)
    cols = img.mean(axis=0)
    max_r = int(h * LETTERBOX_MAX_FRACTION)
    max_c = int(w * LETTERBOX_MAX_FRACTION)

    def _edge(profile: np.ndarray, limit: int) -> int:
        n = 0
        while n < limit and profile[n] < LETTERBOX_LUMA:
            n += 1
        return n

    top = _edge(rows, max_r)
    bottom = _edge(rows[::-1], max_r)
    left = _edge(cols, max_c)
    right = _edge(cols[::-1], max_c)
    cropped = img[top : h - bottom, left : w - right]
    # A frame that is black everywhere is a solid frame, not a letterbox.
    if cropped.size == 0 or float(cropped.mean()) < LETTERBOX_LUMA:
        return img
    return cropped


def _sharpness(img: np.ndarray) -> float:
    lap = cv2.Laplacian(img, cv2.CV_32F, ksize=3)
    h, w = img.shape
    ch, cw = max(1, h // GRID), max(1, w // GRID)
    cells: list[float] = []
    for gy in range(GRID):
        for gx in range(GRID):
            ys, xs = gy * ch, gx * cw
            cell_lap = lap[ys : ys + ch, xs : xs + cw]
            cell_img = img[ys : ys + ch, xs : xs + cw]
            if cell_lap.size == 0:
                continue
            weight = min(1.0, float(cell_img.mean()) / DARK_CELL_LUMA)
            cells.append(float(cell_lap.var()) * weight)
    if not cells:
        return 0.0
    top = sorted(cells, reverse=True)[:TOP_CELLS]
    return 1.0 - math.exp(-float(np.mean(top)) / SHARPNESS_SCALE)


def _detail(img: np.ndarray) -> float:
    gx = cv2.Sobel(img, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(img, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy) / 4.0  # Sobel 3x3 gain is 4
    return min(1.0, float((mag > DETAIL_EDGE).mean()) / DETAIL_FULL)


def measure_static(gray: np.ndarray) -> dict[str, float]:
    """Single-frame focus + exposure measurements."""
    full = to_float(gray)
    img = crop_letterbox(full)
    crushed = float((img <= CRUSH_LEVEL).mean())
    blown = float((img >= BLOW_LEVEL).mean())
    std = float(img.std())
    return {
        "sharpness": _sharpness(img),
        "detail": _detail(img),
        "headroom": 1.0 - min(1.0, HEADROOM_GAIN * (crushed + blown)),
        "luma": float(img.mean()),
        "contrast": min(1.0, std / CONTRAST_FULL),
        "clip_fraction": crushed + blown,
        "std": std,
    }


def _small(img: np.ndarray) -> np.ndarray:
    h, w = img.shape
    scale = MOTION_WIDTH / max(h, w)
    if scale >= 1.0:
        return img
    return cv2.resize(
        img, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA
    )


def _histogram_correlation(a: np.ndarray, b: np.ndarray) -> float:
    ha = cv2.calcHist([a], [0], None, [32], [0.0, 1.0])
    hb = cv2.calcHist([b], [0], None, [32], [0.0, 1.0])
    return float(cv2.compareHist(ha, hb, cv2.HISTCMP_CORREL))


def _estimate_transform(prev8: np.ndarray, cur8: np.ndarray) -> tuple[np.ndarray | None, float]:
    found = cv2.goodFeaturesToTrack(
        prev8, maxCorners=MAX_FEATURES, qualityLevel=0.01, minDistance=8
    )
    if found is None or len(found) < 8:
        return None, 0.0
    pts = np.asarray(found, dtype=np.float32)
    nxt, status, _err = cv2.calcOpticalFlowPyrLK(
        prev8, cur8, pts, np.empty_like(pts), winSize=(21, 21), maxLevel=3
    )
    if nxt is None or status is None:
        return None, 0.0
    good = status.reshape(-1) == 1
    if good.sum() < 8:
        return None, 0.0
    matrix, inliers = cv2.estimateAffinePartial2D(
        pts[good], nxt[good], method=cv2.RANSAC, ransacReprojThreshold=2.0
    )
    if matrix is None or inliers is None:
        return None, 0.0
    return matrix, float(inliers.sum()) / float(len(pts))


def measure_motion(prev: np.ndarray, cur: np.ndarray, rate: float) -> dict[str, float]:
    """
    Camera vs subject motion between two consecutive samples.
    Axes are per second of footage so they do not depend on the sampling rate.
    Sign convention is the camera's move: pan > 0 right, tilt > 0 down,
    zoom > 0 in, twist > 0 clockwise.
    """
    a = _small(to_float(prev))
    b = _small(to_float(cur))
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    h, w = a.shape
    raw_diff = float(np.abs(a - b).mean())
    hist_corr = _histogram_correlation(a, b)

    a8 = (a * 255).astype(np.uint8)
    b8 = (b * 255).astype(np.uint8)
    matrix, confidence = _estimate_transform(a8, b8)
    per_sec = max(rate, 1e-6)
    if matrix is None:
        pan = tilt = zoom = twist = 0.0
        warped = a
    else:
        sa, sb = float(matrix[0, 0]), float(matrix[1, 0])
        scale = math.hypot(sa, sb)
        angle = math.atan2(sb, sa)
        # Content moves opposite to the camera.
        pan = -float(matrix[0, 2]) / w * per_sec
        tilt = -float(matrix[1, 2]) / h * per_sec
        zoom = math.log(max(scale, 1e-6)) * per_sec
        twist = -angle * per_sec
        warped = cv2.warpAffine(a, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE)

    camera_amount = (
        abs(pan) / CAMERA_AXIS_SCALE["pan"]
        + abs(tilt) / CAMERA_AXIS_SCALE["tilt"]
        + abs(zoom) / CAMERA_AXIS_SCALE["zoom"]
        + abs(twist) / CAMERA_AXIS_SCALE["twist"]
    )
    margin_y, margin_x = h // 10, w // 10
    core_diff = float(
        np.abs(
            warped[margin_y : h - margin_y, margin_x : w - margin_x]
            - b[margin_y : h - margin_y, margin_x : w - margin_x]
        ).mean()
    )
    return {
        "pan": pan,
        "tilt": tilt,
        "zoom": zoom,
        "twist": twist,
        "motion_confidence": confidence,
        "camera_stillness": 1.0 - min(1.0, camera_amount),
        "subject_stillness": 1.0 - min(1.0, core_diff / SUBJECT_DIFF_FULL),
        "raw_diff": raw_diff,
        "hist_corr": hist_corr,
    }


STILL_MOTION: dict[str, float] = {
    "pan": 0.0,
    "tilt": 0.0,
    "zoom": 0.0,
    "twist": 0.0,
    "motion_confidence": 0.0,
    "camera_stillness": 1.0,
    "subject_stillness": 1.0,
    "raw_diff": 0.0,
    "hist_corr": 1.0,
}


def _window_std(values: np.ndarray, detrend: bool = False) -> np.ndarray:
    n = len(values)
    out = np.zeros(n)
    for i in range(n):
        lo, hi = max(0, i - WINDOW), min(n, i + WINDOW + 1)
        win = values[lo:hi]
        if len(win) < 2:
            continue
        if detrend:
            x = np.arange(len(win), dtype=np.float64)
            win = win - np.polyval(np.polyfit(x, win, 1), x)
        out[i] = float(np.std(win))
    return out


def _flags(
    static: dict[str, float], motion: dict[str, float], row: dict[str, float], has_prev: bool
) -> int:
    bits = 0
    if static["clip_fraction"] >= CLIPPING_FRACTION:
        bits |= FLAG_BITS["clipping"]
    if static["std"] < SOLID_STD:
        bits |= FLAG_BITS["solid_color"]
    if static["luma"] < LOW_LIGHT_LUMA:
        bits |= FLAG_BITS["low_light"]
    if has_prev and motion["raw_diff"] < DUPLICATE_DIFF:
        bits |= FLAG_BITS["duplicate"]
    if has_prev and motion["raw_diff"] > HARD_CUT_DIFF and motion["hist_corr"] < HARD_CUT_HIST:
        bits |= FLAG_BITS["hard_cut"]
    if row["sharpness"] < BLUR_SHARPNESS and row["camera_stillness"] < BLUR_STILLNESS:
        bits |= FLAG_BITS["motion_blur"]
    if row["camera_stillness"] < SHAKY_STILLNESS and row["motion_smoothness"] < SHAKY_SMOOTHNESS:
        bits |= FLAG_BITS["shaky"]
    return bits


def finalise(
    times: list[float],
    statics: list[dict[str, float]],
    motions: list[dict[str, float]],
) -> list[list[float]]:
    """
    Combine per-frame measurements into stored rows (COLUMNS order).
    `motions[i]` is the motion from sample i-1 to i; `motions` has len(statics) - 1 items.
    The first sample borrows the second sample's motion (it has no predecessor).
    """
    n = len(statics)
    if n == 0:
        return []
    if len(times) != n or len(motions) != max(0, n - 1):
        raise ValueError(f"finalise: {n} statics, {len(times)} times, {len(motions)} motions")

    per_sample = [motions[0] if motions else STILL_MOTION, *motions]
    sharp = np.array([s["sharpness"] for s in statics])
    luma = np.array([s["luma"] for s in statics])
    vel = np.array([[m["pan"], m["tilt"]] for m in per_sample])

    focus_stability = 1.0 - np.minimum(1.0, _window_std(sharp) / FOCUS_STABILITY_STD)
    flicker = 1.0 - np.minimum(1.0, _window_std(luma, detrend=True) / FLICKER_STD)
    jitter = np.sqrt(_window_std(vel[:, 0]) ** 2 + _window_std(vel[:, 1]) ** 2)
    smoothness = 1.0 - np.minimum(1.0, jitter / SMOOTHNESS_STD)

    rows: list[list[float]] = []
    for i in range(n):
        s, m = statics[i], per_sample[i]
        row: dict[str, Any] = {
            "t": times[i],
            "sharpness": s["sharpness"],
            "detail": s["detail"],
            "focus_stability": float(focus_stability[i]),
            "headroom": s["headroom"],
            "luma": s["luma"],
            "contrast": s["contrast"],
            "flicker": float(flicker[i]),
            "camera_stillness": m["camera_stillness"],
            "subject_stillness": m["subject_stillness"],
            "motion_smoothness": float(smoothness[i]),
            "pan": m["pan"],
            "tilt": m["tilt"],
            "zoom": m["zoom"],
            "twist": m["twist"],
            "motion_confidence": m["motion_confidence"],
        }
        row["flags"] = _flags(s, motions[i - 1] if i > 0 else STILL_MOTION, row, has_prev=i > 0)
        rows.append([_round(row[c]) for c in COLUMNS])
    return rows


def _round(v: float) -> float:
    f = float(v)
    return round(f, 4) if math.isfinite(f) else 0.0
