"""
Stage 4 — Build assembly timeline.

Reads classified clips from sidecars, then in DaVinci Resolve:
  1. Determines target resolution + FPS (modal across A-roll then B-roll)
  2. Creates a new timeline named "<shoot> — Cairn Assembly"
     (suffixed if one already exists, never overwritten)
  3. Adds A-roll clips to V1 / A1 in chronological filename order
  4. Adds a V2 video track and A2 audio track
  5. Locks V1 / A1 and appends B-roll clips so they land on V2 / A2
  6. Unlocks V1 / A1
  7. Skips AMBIGUOUS clips (user confirms them via the Classify panel first)

Pure local Python on the sidecar side; all Resolve calls drain through the
serial lock in resolve_api (taste invariant #3).
"""

from __future__ import annotations

import contextlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import resolve_api
import sidecar

# ── Sidecar reading ───────────────────────────────────────────────────────────


def _classified_clips(shoot_folder: Path) -> dict[str, list[str]]:
    """
    Walk the .cairn sidecar directory and return classified clip paths grouped
    by label. Only clips whose sidecar has 'classify' in stages_complete are
    included. Each list is sorted by source_path so order is deterministic.
    """
    bins: dict[str, list[str]] = {"A-ROLL": [], "B-ROLL": [], "AMBIGUOUS": []}
    cairn_dir = sidecar.sidecar_root(shoot_folder)  # <shoot>/.cairn for Offload shoots
    if not cairn_dir.is_dir():
        return bins

    for jf in sorted(cairn_dir.glob("*.json")):
        try:
            data = json.loads(jf.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if "classify" not in data.get("stages_complete", []):
            continue
        label = data.get("classification")
        path = data.get("source_path")
        if label in bins and isinstance(path, str):
            bins[label].append(path)

    for label in bins:
        bins[label].sort()
    return bins


# ── Resolution + frame-rate inference ─────────────────────────────────────────


def _parse_resolution(value: Any) -> tuple[int, int] | None:
    """Parse "3840x2160" or {"width":3840,"height":2160} into (w, h)."""
    if isinstance(value, str) and "x" in value:
        try:
            w, h = value.split("x", 1)
            return int(w), int(h)
        except (ValueError, TypeError):
            return None
    if isinstance(value, dict):
        try:
            return int(value["width"]), int(value["height"])
        except (KeyError, ValueError, TypeError):
            return None
    return None


def _modal_settings(
    mp: Any, clip_paths: list[str], sample_cap: int = 30
) -> tuple[float, tuple[int, int]]:
    """
    Read FPS + resolution from up to `sample_cap` clips. Return the most
    common values. Falls back to (24.0, (1920, 1080)) if nothing readable.
    """
    fps_values: list[float] = []
    res_values: list[tuple[int, int]] = []

    for path in clip_paths[:sample_cap]:
        clip = resolve_api.get_clip_by_path(mp, path)
        if clip is None:
            continue

        fps_raw = clip.GetClipProperty("FPS")
        if fps_raw:
            with contextlib.suppress(TypeError, ValueError):
                fps_values.append(round(float(fps_raw), 3))

        res = _parse_resolution(clip.GetClipProperty("Resolution"))
        if res is not None:
            res_values.append(res)

    fps = Counter(fps_values).most_common(1)[0][0] if fps_values else 24.0
    resolution = Counter(res_values).most_common(1)[0][0] if res_values else (1920, 1080)
    return fps, resolution


# ── Timeline name uniqueness ──────────────────────────────────────────────────


def _existing_timeline_names(project: Any) -> set[str]:
    """Return the set of timeline names already in the project."""
    names: set[str] = set()
    count = project.GetTimelineCount() or 0
    for i in range(1, int(count) + 1):
        tl = project.GetTimelineByIndex(i)
        if tl is not None:
            n = tl.GetName()
            if isinstance(n, str):
                names.add(n)
    return names


def _unique_timeline_name(project: Any, base: str) -> str:
    """Return base, or base + ' (N)' such that the name is not in use."""
    existing = _existing_timeline_names(project)
    if base not in existing:
        return base
    n = 2
    while f"{base} ({n})" in existing:
        n += 1
    return f"{base} ({n})"


# ── Timeline assembly ─────────────────────────────────────────────────────────


def _resolve_clips(mp: Any, paths: list[str]) -> tuple[list[Any], list[str]]:
    """
    Look up each path in the media pool. Returns (clips, missing_paths).
    Clips imported into the wrong bin or never imported show up as missing.
    """
    clips: list[Any] = []
    missing: list[str] = []
    for p in paths:
        clip = resolve_api.get_clip_by_path(mp, p)
        if clip is None:
            missing.append(p)
        else:
            clips.append(clip)
    return clips, missing


def _set_track_lock_safely(timeline: Any, track_type: str, index: int, locked: bool) -> str | None:
    """SetTrackLock if available; returns an error message on failure, None on success."""
    fn = getattr(timeline, "SetTrackLock", None)
    if not callable(fn):
        return None
    try:
        fn(track_type, index, locked)
        return None
    except (RuntimeError, TypeError) as exc:
        action = "lock" if locked else "unlock"
        return f"SetTrackLock({track_type!r}, {index}, {action}) failed: {exc}"


def _add_track_safely(timeline: Any, track_type: str) -> str | None:
    """AddTrack if available; returns an error message on failure, None on success."""
    fn = getattr(timeline, "AddTrack", None)
    if not callable(fn):
        return None
    try:
        fn(track_type)
        return None
    except (RuntimeError, TypeError) as exc:
        return f"AddTrack({track_type!r}) failed: {exc}"


def build_timeline(shoot_folder: str | Path, name: str | None = None) -> dict[str, Any]:
    """
    Build the assembly timeline. Returns a dict:
      {
        "ok": bool,
        "name": str,                    # actual timeline name (may be suffixed)
        "fps": float,
        "resolution": "WxH",
        "a_count": int,                 # A-roll clips placed on V1/A1
        "b_count": int,                 # B-roll clips placed on V2/A2
        "ambiguous_count": int,         # clips skipped (need user review)
        "missing_paths": list[str],     # clips classified but not in media pool
        "warnings": list[str],          # non-fatal issues
        "error": str | None,
      }

    Never raises on Resolve unavailability or empty input — returns ok:false
    with a clear error message instead.
    """
    folder = Path(shoot_folder).resolve()
    if not folder.is_dir():
        return _err(f"Shoot folder does not exist: {folder}")

    classified = _classified_clips(folder)
    a_paths = classified["A-ROLL"]
    b_paths = classified["B-ROLL"]
    ambiguous_count = len(classified["AMBIGUOUS"])

    if not a_paths and not b_paths:
        return _err(
            "No classified clips found. Run Classify first, then build the assembly timeline."
        )

    base_name = name or f"{folder.name} — Cairn Assembly"
    warnings: list[str] = []

    resolve_api._acquire_lock()
    try:
        try:
            resolve = resolve_api.get_resolve()
        except resolve_api.ResolveUnavailableError as exc:
            return _err(str(exc))

        pm = resolve.GetProjectManager()
        if pm is None:
            return _err("GetProjectManager() returned None")

        project = pm.GetCurrentProject()
        if project is None:
            return _err("No DaVinci Resolve project is open")

        mp = project.GetMediaPool()
        if mp is None:
            return _err("GetMediaPool() returned None")

        # Modal FPS + resolution from A-roll first (preferred), else B-roll
        sample = a_paths if a_paths else b_paths
        fps, (width, height) = _modal_settings(mp, sample)

        # Project framerate is global once the first timeline is created.
        # SetSetting returns False if Resolve refuses; we record a warning
        # but proceed using whatever the project ends up with.
        if project.SetSetting("timelineFrameRate", str(fps)) is False:
            current = project.GetSetting("timelineFrameRate")
            warnings.append(
                f"Project framerate locked at {current}; "
                f"requested {fps} ignored. Existing timelines determine fps."
            )
        if project.SetSetting("timelineResolutionWidth", str(width)) is False:
            warnings.append(
                f"Resolution width {width} rejected by Resolve — using project default."
            )
        if project.SetSetting("timelineResolutionHeight", str(height)) is False:
            warnings.append(
                f"Resolution height {height} rejected by Resolve — using project default."
            )

        timeline_name = _unique_timeline_name(project, base_name)
        timeline = mp.CreateEmptyTimeline(timeline_name)
        if timeline is None:
            return _err(f"CreateEmptyTimeline({timeline_name!r}) returned None")

        # Resolve paths to MediaPoolItems before any append calls
        a_clips, a_missing = _resolve_clips(mp, a_paths)
        b_clips, b_missing = _resolve_clips(mp, b_paths)
        missing_paths = a_missing + b_missing

        a_count = 0
        b_count = 0

        # ── A-roll → V1 / A1 ─────────────────────────────────────────────────
        if a_clips:
            appended = mp.AppendToTimeline(a_clips)
            a_count = len(appended) if appended else 0
            if a_count != len(a_clips):
                warnings.append(f"AppendToTimeline returned {a_count}/{len(a_clips)} A-roll clips.")

        # ── B-roll → V2 / A2 ─────────────────────────────────────────────────
        if b_clips:
            # Add the destination tracks before locking V1/A1, otherwise the
            # next AppendToTimeline would have nowhere to land.
            for msg in (
                _add_track_safely(timeline, "video"),
                _add_track_safely(timeline, "audio"),
            ):
                if msg is not None:
                    warnings.append(msg)
            for msg in (
                _set_track_lock_safely(timeline, "video", 1, True),
                _set_track_lock_safely(timeline, "audio", 1, True),
            ):
                if msg is not None:
                    warnings.append(msg)
            try:
                appended = mp.AppendToTimeline(b_clips)
                b_count = len(appended) if appended else 0
                if b_count != len(b_clips):
                    warnings.append(
                        f"AppendToTimeline returned {b_count}/{len(b_clips)} B-roll clips."
                    )
            finally:
                for msg in (
                    _set_track_lock_safely(timeline, "video", 1, False),
                    _set_track_lock_safely(timeline, "audio", 1, False),
                ):
                    if msg is not None:
                        warnings.append(msg)

        # Make the new timeline the active one
        project.SetCurrentTimeline(timeline)

        return {
            "ok": True,
            "name": timeline_name,
            "fps": fps,
            "resolution": f"{width}x{height}",
            "a_count": a_count,
            "b_count": b_count,
            "ambiguous_count": ambiguous_count,
            "missing_paths": missing_paths,
            "warnings": warnings,
            "error": None,
        }
    finally:
        resolve_api._release_lock()


def _err(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "name": None,
        "fps": None,
        "resolution": None,
        "a_count": 0,
        "b_count": 0,
        "ambiguous_count": 0,
        "missing_paths": [],
        "warnings": [],
        "error": message,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: timeline.py <shoot_folder> [<name>]", file=sys.stderr)
        sys.exit(1)

    name = sys.argv[2] if len(sys.argv) >= 3 else None
    print(json.dumps(build_timeline(sys.argv[1], name), indent=2))
