"""
Stage 5 — Selects: taste learning (specs/selects.md §6).

The editor rates selects ("keep" / "reject") and near misses ("keep" = should
have been a select). Ratings are time ranges in the clip, stored in the clip
sidecar, so they survive any re-tune. From them this module builds:

  examples(shoot)          numeric summaries of each rated range — the only
                           thing sent to Claude (no media, no file names)
  evaluate(shoot, s)       how many ratings a settings object reproduces —
                           the deterministic check on every Claude proposal
  user presets             learned settings saved to ~/.cairn/selects_presets.json
                           so a taste carries across shoots
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import classify
import numpy as np
import selects
import selects_presets as presets
import selects_score as ss
import sidecar

RATINGS = ("keep", "reject")
KEEP_COVERAGE = 0.5  # a rated range counts as "selected" when at least half of it is
USER_PRESETS_PATH: Path = Path.home() / ".cairn" / "selects_presets.json"
MIN_EXAMPLES = 6

SUMMARY_METRICS = (
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
)


# ── Ratings ──────────────────────────────────────────────────────────────────


def rating_key(start_sec: float, end_sec: float) -> str:
    return f"{start_sec:.2f}-{end_sec:.2f}"


def rate(
    shoot_folder: str | Path,
    clip_path: str,
    start_sec: float,
    end_sec: float,
    rating: str | None,
    source: str = "select",
) -> dict[str, Any]:
    """Store (or clear, when rating is None) one rating. Returns the clip's ratings."""
    if rating is not None and rating not in RATINGS:
        raise ValueError(f"rating must be one of {RATINGS} or null")
    if not (math.isfinite(start_sec) and math.isfinite(end_sec) and end_sec > start_sec >= 0):
        raise ValueError("start_sec and end_sec must be finite, with end_sec after start_sec")
    if source not in ("select", "near_miss"):
        raise ValueError("source must be 'select' or 'near_miss'")
    if clip_path not in classify.scan_video_clips(shoot_folder):
        raise ValueError(f"{Path(clip_path).name} is not a video clip in this shoot")

    record = sidecar.read_clip(shoot_folder, clip_path) or {}
    ratings = dict(record.get("select_ratings") or {})
    key = rating_key(start_sec, end_sec)
    if rating is None:
        ratings.pop(key, None)
    else:
        ratings[key] = {
            "rating": rating,
            "start_sec": round(float(start_sec), 3),
            "end_sec": round(float(end_sec), 3),
            "source": source,
            "rated_at": datetime.now(UTC).isoformat(),
        }
    sidecar.merge_clip(shoot_folder, clip_path, updates={"select_ratings": ratings})
    return ratings


# ── Examples ─────────────────────────────────────────────────────────────────


def summarise_range(
    block: dict[str, Any], start_sec: float, end_sec: float
) -> dict[str, Any] | None:
    """Mean of each measurement over [start, end), plus camera class and flag shares."""
    frames = np.asarray(block.get("frames") or [], dtype=np.float64)
    if frames.size == 0:
        return None
    t = frames[:, ss.COL["t"]]
    mask = (t >= start_sec - 1e-6) & (t < end_sec - 1e-6)
    if not mask.any():
        return None
    rows = frames[mask]
    out: dict[str, Any] = {m: round(float(rows[:, ss.COL[m]].mean()), 3) for m in SUMMARY_METRICS}
    cam = {a: float(rows[:, ss.COL[a]].mean()) for a in ss.CAMERA_AXES}
    out["camera_class"] = ss.camera_class(cam["pan"], cam["tilt"], cam["zoom"], cam["twist"])
    out["camera_move"] = round(sum(abs(v) for v in cam.values()), 4)
    flags = rows[:, ss.COL["flags"]].astype(np.int64)
    out["flag_share"] = {
        name: round(float(((flags & bit) != 0).mean()), 3)
        for name, bit in ss.FLAG_BITS.items()
        if ((flags & bit) != 0).any()
    }
    out["duration_sec"] = round(end_sec - start_sec, 2)
    out["samples"] = int(mask.sum())
    return out


def _rated(
    shoot_folder: str | Path,
) -> list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]]:
    """
    (clip_path, rating, selects block, range summary) for every rating that has
    measured samples. examples() and evaluate() both use this one list, so the
    ids Claude sees are the ids the evaluation reports.
    """
    out: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for path in classify.scan_video_clips(shoot_folder):
        record = selects.read_record(shoot_folder, path) or {}
        block = record.get("selects")
        if not block:
            continue
        for rating in (record.get("select_ratings") or {}).values():
            if rating.get("rating") not in RATINGS:
                continue
            summary = summarise_range(block, rating["start_sec"], rating["end_sec"])
            if summary is not None:
                out.append((path, rating, block, summary))
    return out


def examples(shoot_folder: str | Path) -> list[dict[str, Any]]:
    """Anonymised numeric examples for Claude: clip names become 'clip N'."""
    clip_ids: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for n, (path, rating, _block, summary) in enumerate(_rated(shoot_folder), start=1):
        clip_ids.setdefault(path, len(clip_ids) + 1)
        out.append(
            {
                "id": n,
                "clip": f"clip {clip_ids[path]}",
                "rating": rating["rating"],
                "source": rating.get("source", "select"),
                "start_sec": rating["start_sec"],
                **summary,
            }
        )
    return out


# ── Evaluation ───────────────────────────────────────────────────────────────


def evaluate(shoot_folder: str | Path, settings: ss.SelectsSettings) -> dict[str, Any]:
    """
    Replay settings over the rated ranges. A "keep" is right when at least half
    of it is selected; a "reject" is right when less than half of it is.
    """
    details: list[dict[str, Any]] = []
    scored_cache: dict[str, list[dict[str, Any]]] = {}
    for n, (path, rating, block, _summary) in enumerate(_rated(shoot_folder), start=1):
        if path not in scored_cache:
            scored_cache[path] = ss.score_clip(block, settings)["segments"]
        cover = ss.coverage(scored_cache[path], rating["start_sec"], rating["end_sec"])
        predicted = "keep" if cover >= KEEP_COVERAGE else "reject"
        details.append(
            {
                "id": n,
                "rating": rating["rating"],
                "predicted": predicted,
                "coverage": round(cover, 3),
                "correct": predicted == rating["rating"],
            }
        )
    total = len(details)
    correct = sum(1 for d in details if d["correct"])
    keeps = [d for d in details if d["rating"] == "keep"]
    rejects = [d for d in details if d["rating"] == "reject"]
    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 3) if total else 0.0,
        "keeps_kept": sum(1 for d in keeps if d["correct"]),
        "keeps_total": len(keeps),
        "rejects_dropped": sum(1 for d in rejects if d["correct"]),
        "rejects_total": len(rejects),
        "details": details,
    }


# ── User presets ─────────────────────────────────────────────────────────────


def _load_user_presets() -> list[dict[str, Any]]:
    """Learned presets. An unreadable file is moved aside, so saving can't overwrite it."""
    if not USER_PRESETS_PATH.is_file():
        return []
    try:
        data = json.loads(USER_PRESETS_PATH.read_text())
        if not isinstance(data, dict) or not isinstance(data.get("presets", []), list):
            raise ValueError("unexpected structure")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        backup = USER_PRESETS_PATH.with_name(f"{USER_PRESETS_PATH.name}.corrupt-{stamp}")
        with contextlib.suppress(OSError):
            USER_PRESETS_PATH.replace(backup)
        print(
            f"[selects_learn] unreadable {USER_PRESETS_PATH}: {exc}; kept as {backup.name}",
            file=sys.stderr,
        )
        return []
    return [
        p
        for p in data.get("presets", [])
        if isinstance(p, dict) and str(p.get("id", "")).startswith("user-")
    ]


def list_all_presets() -> list[dict[str, Any]]:
    builtin = [{**p, "builtin": True} for p in presets.list_presets()]
    user = [
        {
            "id": p["id"],
            "name": p.get("name", p["id"]),
            "description": p.get("description", ""),
            "settings": ss.settings_to_dict(ss.settings_from_dict(p.get("settings"))),
            "builtin": False,
            "rationale": p.get("rationale", ""),
            "accuracy": p.get("accuracy"),
        }
        for p in _load_user_presets()
    ]
    return builtin + user


def _slug(name: str) -> str:
    """Readable id for a name; non-Latin names get a short hash so they stay distinct."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    if slug:
        return slug
    return "taste-" + hashlib.sha1(name.encode()).hexdigest()[:8]


def save_user_preset(
    name: str,
    settings: ss.SelectsSettings,
    description: str = "",
    rationale: str = "",
    accuracy: float | None = None,
) -> dict[str, Any]:
    name = name.strip()[:60]
    if not name:
        raise ValueError("preset name is required")
    preset_id = f"user-{_slug(name)}"
    entry = {
        "id": preset_id,
        "name": name,
        "description": description.strip()[:200],
        "rationale": rationale.strip()[:2000],
        "accuracy": accuracy,
        "settings": ss.settings_to_dict(replace_preset_id(settings, preset_id)),
        "saved_at": datetime.now(UTC).isoformat(),
    }
    others = [p for p in _load_user_presets() if p.get("id") != preset_id]
    USER_PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = USER_PRESETS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"version": 1, "presets": [*others, entry]}, indent=2))
    tmp.replace(USER_PRESETS_PATH)
    return entry


def replace_preset_id(settings: ss.SelectsSettings, preset_id: str) -> ss.SelectsSettings:
    return ss.settings_from_dict({**ss.settings_to_dict(settings), "preset_id": preset_id})


def resolve_preset(preset_id: str) -> ss.SelectsSettings:
    """Built-in or user preset by id."""
    for p in list_all_presets():
        if p["id"] == preset_id:
            return ss.settings_from_dict(p["settings"])
    raise KeyError(f"Unknown preset: {preset_id!r}")


# ── Learn context (what Node hands Claude) ───────────────────────────────────


def learn_context(shoot_folder: str | Path, settings: ss.SelectsSettings) -> dict[str, Any]:
    ex = examples(shoot_folder)
    baseline = evaluate(shoot_folder, settings)
    keeps = sum(1 for e in ex if e["rating"] == "keep")
    rejects = len(ex) - keeps
    ready = len(ex) >= MIN_EXAMPLES and keeps >= 2 and rejects >= 2
    reason = None
    if not ready:
        reason = (
            f"Rate at least {MIN_EXAMPLES} selects or near misses, with at least 2 keeps and 2 rejects "
            f"(you have {keeps} keep, {rejects} reject)."
        )
    return {
        "ready": ready,
        "reason": reason,
        "examples": ex,
        "settings": ss.settings_to_dict(settings),
        "baseline": baseline,
        "presets": [{"id": p["id"], "name": p["name"]} for p in list_all_presets()],
    }
