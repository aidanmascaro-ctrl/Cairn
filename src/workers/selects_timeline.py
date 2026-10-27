"""
Stage 5 — Selects: build the selects timeline in Resolve (specs/selects.md §5).

    plan_timeline(...)        pure: seconds → clipInfo pieces + markers
    build_selects_timeline()  drives Resolve under the resolve_api lock,
                              then reads the timeline back to verify

Layouts:
    context       V1 = every clip minus its selects, V2 = the selects at the
                  same position (playing the timeline still shows everything)
    selects_only  V1 = selects packed back-to-back in the chosen order
"""

from __future__ import annotations

import contextlib
import os
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import resolve_api
import selects
import selects_score as ss

LAYOUTS = ("context", "selects_only")
ORDERS = ("timeline", "score_desc", "score_asc", "duration_desc", "duration_asc")
MARKER_TAG = "cairn:select"

# Owner's colour-management standard (~/.claude/CLAUDE.md) — restated whenever
# a timeline is detached from project settings for a custom resolution.
COLOUR_STANDARD: dict[str, str] = {
    "colorScienceMode": "davinciYRGB",
    "colorSpaceTimeline": "DaVinci WG/Intermediate",
    "colorSpaceOutput": "Rec.709-A",
    "separateColorSpaceAndGamma": "0",
}


# ── Pure planning ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Piece:
    file: str
    track: int  # 1 or 2
    src_in: int  # native source frames, 0-based
    src_out: int  # exclusive
    record: int  # timeline frames from timeline start
    tl_frames: int  # expected length on the timeline
    is_select: bool
    segment: dict[str, Any] | None = None


def _sec_to_src(sec: float, src_fps: float) -> int:
    return round(sec * src_fps)


def _order_selects(
    items: list[tuple[str, float, dict[str, Any]]], order: str
) -> list[tuple[str, float, dict[str, Any]]]:
    keys = {
        "timeline": None,
        "score_desc": lambda it: -it[2]["mean_score"],
        "score_asc": lambda it: it[2]["mean_score"],
        "duration_desc": lambda it: -it[2]["duration_sec"],
        "duration_asc": lambda it: it[2]["duration_sec"],
    }
    key = keys.get(order)
    return list(items) if key is None else sorted(items, key=key)


def plan_timeline(
    clips: list[dict[str, Any]],
    tl_fps: float,
    layout: str = "context",
    order: str = "timeline",
) -> list[Piece]:
    """
    clips: rescore() clip dicts (file, analysed, duration_sec, fps, segments).
    Selects must carry `marker_color` already (assign_marker_bands).
    """
    if layout not in LAYOUTS:
        raise ValueError(f"layout must be one of {LAYOUTS}")
    if order not in ORDERS:
        raise ValueError(f"order must be one of {ORDERS}")
    if tl_fps <= 0:
        raise ValueError("timeline fps must be > 0")

    analysed = [c for c in clips if c.get("analysed") and float(c.get("duration_sec") or 0) > 0]
    pieces: list[Piece] = []

    if layout == "selects_only":
        flat = [
            (c["file"], float(c.get("fps") or tl_fps), s) for c in analysed for s in c["segments"]
        ]
        cursor = 0
        for file, src_fps, seg in _order_selects(flat, order):
            src_in = _sec_to_src(seg["start_sec"], src_fps)
            src_out = _sec_to_src(seg["end_sec"], src_fps)
            if src_out <= src_in:
                continue
            length = round((src_out - src_in) * tl_fps / src_fps)
            pieces.append(Piece(file, 1, src_in, src_out, cursor, length, True, seg))
            cursor += length
        return pieces

    cursor = 0
    for clip in analysed:
        src_fps = float(clip.get("fps") or tl_fps)
        duration = float(clip["duration_sec"])
        src_total = _sec_to_src(duration, src_fps)
        # Boundaries in source frames so neighbouring pieces abut exactly.
        bounds: list[tuple[int, int, dict[str, Any] | None]] = []
        last = 0
        for seg in sorted(clip["segments"], key=lambda s: s["start_sec"]):
            s_in = max(last, _sec_to_src(seg["start_sec"], src_fps))
            s_out = min(src_total, _sec_to_src(seg["end_sec"], src_fps))
            if s_out <= s_in:
                continue
            if s_in > last:
                bounds.append((last, s_in, None))
            bounds.append((s_in, s_out, seg))
            last = s_out
        if last < src_total:
            bounds.append((last, src_total, None))

        for src_in, src_out, seg in bounds:
            record = cursor + round(src_in * tl_fps / src_fps)
            end = cursor + round(src_out * tl_fps / src_fps)
            pieces.append(
                Piece(
                    clip["file"],
                    2 if seg else 1,
                    src_in,
                    src_out,
                    record,
                    end - record,
                    seg is not None,
                    seg,
                )
            )
        cursor += round(src_total * tl_fps / src_fps)
    return pieces


def marker_note(seg: dict[str, Any], clip_name: str) -> tuple[str, str]:
    """(name, note) for a select marker."""
    groups = seg.get("group_scores") or {}
    parts = [f"mean {seg['mean_score']:.2f}", f"peak {seg['peak_score']:.2f}"]
    for g in ss.GROUPS:
        if groups.get(g) is not None:
            parts.append(f"{g} {groups[g]:.2f}")
    parts.append(f"{seg['duration_sec']:.1f}s")
    cam = seg.get("camera") or {}
    if cam.get("class"):
        parts.append(f"camera {cam['class']}")
    if seg.get("flags"):
        parts.append("flags " + ", ".join(seg["flags"]))
    if seg.get("forced"):
        parts.append("force-included")
    return f"Select {seg['mean_score']:.2f} · {Path(clip_name).stem}", " · ".join(parts)


# ── Resolve helpers (call only while holding the resolve_api lock) ───────────


def norm_path(path: str) -> str:
    """Compare paths the way the filesystem does: through symlinks, NFC Unicode."""
    return unicodedata.normalize("NFC", os.path.realpath(path))


def _index_media_pool(mp: Any) -> dict[str, Any]:
    """File Path → MediaPoolItem for the whole pool, one walk."""
    index: dict[str, Any] = {}
    stack = [mp.GetRootFolder()]
    while stack:
        folder = stack.pop()
        if folder is None:
            continue
        for clip in folder.GetClipList() or []:
            path = clip.GetClipProperty("File Path") if clip else None
            if path:
                index.setdefault(norm_path(str(path)), clip)
        stack.extend(folder.GetSubFolderList() or [])
    return index


def _unique_name(project: Any, base: str) -> str:
    names = set()
    for i in range(1, int(project.GetTimelineCount() or 0) + 1):
        tl = project.GetTimelineByIndex(i)
        if tl is not None:
            names.add(tl.GetName())
    if base not in names:
        return base
    n = 2
    while f"{base} ({n})" in names:
        n += 1
    return f"{base} ({n})"


def _modal_format(items: list[Any]) -> tuple[float, int, int]:
    fps_c: Counter[float] = Counter()
    res_c: Counter[tuple[int, int]] = Counter()
    for item in items:
        with contextlib.suppress(TypeError, ValueError):
            fps_c[round(float(item.GetClipProperty("FPS")), 3)] += 1
        raw = str(item.GetClipProperty("Resolution") or "")
        if "x" in raw:
            try:
                w, h = raw.lower().split("x", 1)
                res_c[(int(w), int(h))] += 1
            except ValueError:
                pass
    fps = fps_c.most_common(1)[0][0] if fps_c else 25.0
    w, h = res_c.most_common(1)[0][0] if res_c else (1920, 1080)
    return fps, w, h


def _fps_string(fps: float) -> str:
    return str(int(fps)) if float(fps).is_integer() else f"{fps:.3f}".rstrip("0").rstrip(".")


def _create_timeline(
    project: Any, mp: Any, name: str, fps: float, width: int, height: int
) -> tuple[Any, list[str]]:
    """
    New empty timeline at the footage format. Only detaches from project settings
    when the resolution differs, and then restates the colour standard and reads
    it back (SetSetting returns True whether or not it took).
    """
    warnings: list[str] = []
    if int(project.GetTimelineCount() or 0) == 0:
        # Project frame rate is only settable before the first timeline exists.
        project.SetSetting("timelineFrameRate", _fps_string(fps))
    timeline = mp.CreateEmptyTimeline(name)
    if timeline is None:
        raise RuntimeError(f"CreateEmptyTimeline({name!r}) returned None")

    proj_w = str(project.GetSetting("timelineResolutionWidth"))
    proj_h = str(project.GetSetting("timelineResolutionHeight"))
    if (proj_w, proj_h) != (str(width), str(height)):
        wanted = {
            "useCustomSettings": "1",
            "timelineResolutionWidth": str(width),
            "timelineResolutionHeight": str(height),
            **COLOUR_STANDARD,
        }
        for key, value in wanted.items():
            timeline.SetSetting(key, value)
        for key, value in wanted.items():
            got = str(timeline.GetSetting(key))
            if got != value:
                warnings.append(f"Timeline setting {key} is {got!r}, wanted {value!r}")

    tl_fps = float(timeline.GetSetting("timelineFrameRate") or fps)
    if abs(tl_fps - fps) > 0.01:
        warnings.append(
            f"Timeline runs at {tl_fps:g} fps (project rate); footage is {fps:g} fps. "
            "Resolve will conform the clips."
        )
    return timeline, warnings


def _link_audio(timeline: Any, items: list[Any]) -> int:
    """
    Make sure every appended video item is linked to its own audio, matched by
    same source name + start + duration (never position alone).

    Resolve 21 returns only the video items from a clipInfo append and links
    their audio itself; SetClipsLinked returns False on an already-linked group.
    So: read each video item's links first, link only what isn't, and count
    from the read-back rather than from any call's return value.
    """
    audio_index: dict[tuple[str, int, int], list[Any]] = defaultdict(list)
    for i in range(1, int(timeline.GetTrackCount("audio") or 0) + 1):
        for audio in timeline.GetItemListInTrack("audio", i) or []:
            audio_index[_key(audio)].append(audio)

    linked = 0
    for video in items:
        if video is None or _track_type(video) != "video":
            continue
        name = _source_name(video)
        if not _is_linked(video, name):
            partners = audio_index.get(_key(video), [])
            if partners:
                timeline.SetClipsLinked([video, *partners], True)
        if _is_linked(video, name):
            linked += 1
    return linked


def _key(item: Any) -> tuple[str, int, int]:
    return (_source_name(item), int(item.GetStart()), int(item.GetDuration()))


def _track_type(item: Any) -> str:
    kind = item.GetTrackTypeAndIndex()
    return str(kind[0]) if kind else ""


def _source_name(item: Any) -> str:
    mpi = item.GetMediaPoolItem()
    return str(mpi.GetName() if mpi is not None else item.GetName())


def _is_linked(item: Any, name: str) -> bool:
    """Linked to at least one item, and only to items from the same source clip."""
    partners = item.GetLinkedItems() or []
    return bool(partners) and all(_source_name(p) == name for p in partners)


def _clear_markers(obj: Any) -> int:
    """Delete Cairn's select markers. Returns how many could NOT be deleted."""
    failed = 0
    for frame_id, info in (obj.GetMarkers() or {}).items():
        if str(info.get("customData", "")) == MARKER_TAG and not obj.DeleteMarkerAtFrame(
            int(frame_id)
        ):
            failed += 1
    return failed


def _append(mp: Any, pieces: list[Piece], pool: dict[str, Any], start: int) -> list[Any]:
    infos = [
        {
            "mediaPoolItem": pool[p.file],
            "startFrame": p.src_in,
            "endFrame": p.src_out,
            "trackIndex": p.track,
            "recordFrame": start + p.record,
        }
        for p in pieces
    ]
    return list(mp.AppendToTimeline(infos) or []) if infos else []


def _readback(timeline: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for kind, prefix in (("video", "V"), ("audio", "A")):
        for i in range(1, int(timeline.GetTrackCount(kind) or 0) + 1):
            out[f"{prefix}{i}"] = len(timeline.GetItemListInTrack(kind, i) or [])
    return out


# ── Entry point ──────────────────────────────────────────────────────────────


def build_selects_timeline(
    shoot_folder: str | Path,
    settings: ss.SelectsSettings | None = None,
    layout: str = "context",
    order: str = "timeline",
    include_markers: bool = True,
    clip_markers: bool = True,
    name: str | None = None,
) -> dict[str, Any]:
    folder = Path(shoot_folder).resolve()
    scored = selects.rescore(folder, settings, persist=True)
    clips: list[dict[str, Any]] = [c for c in scored["clips"] if c.get("analysed")]
    if not any(c["segments"] for c in clips):
        return _err(
            "No selects at these settings. Lower the score threshold or analyse the footage first."
        )

    resolve_api._acquire_lock()
    try:
        try:
            resolve = resolve_api.get_resolve()
        except resolve_api.ResolveUnavailableError as exc:
            return _err(str(exc))
        project = resolve.GetProjectManager().GetCurrentProject()
        if project is None:
            return _err("No DaVinci Resolve project is open")
        mp = project.GetMediaPool()
        index = _index_media_pool(mp)
        pool = {
            c["file"]: index[norm_path(c["file"])] for c in clips if norm_path(c["file"]) in index
        }
        missing = [str(c["file"]) for c in clips if c["file"] not in pool]
        present = [c for c in clips if c["file"] in pool]
        if not any(c["segments"] for c in present):
            return _err(
                "None of the clips with selects are in the Resolve media pool. Run Ingest first.",
                missing,
            )
        # Bands come from the selects actually going into this timeline.
        banded = iter(ss.assign_marker_bands([s for c in present for s in c["segments"]]))
        present = [{**c, "segments": [next(banded) for _ in c["segments"]]} for c in present]

        fps, width, height = _modal_format([pool[c["file"]] for c in present])
        timeline_name = _unique_name(project, name or f"{folder.name} — Cairn Selects")
        timeline, warnings = _create_timeline(project, mp, timeline_name, fps, width, height)
        try:
            result = _fill_timeline(
                project, mp, timeline, pool, present, layout, order, include_markers, clip_markers
            )
        except Exception as exc:
            removed = bool(mp.DeleteTimelines([timeline]))
            note = (
                "The half-built timeline was removed."
                if removed
                else (f"A half-built timeline “{timeline_name}” is still in the project.")
            )
            return _err(f"Building the timeline failed: {exc}. {note}", missing)
        return {
            "ok": True,
            "name": timeline_name,
            "layout": layout,
            "fps": result["fps"],
            "resolution": f"{width}x{height}",
            "select_count": result["select_count"],
            "piece_count": result["piece_count"],
            "linked_count": result["linked_count"],
            "markers_added": result["markers_added"],
            "clip_markers_added": result["clip_markers_added"],
            "tracks": result["tracks"],
            "missing_paths": missing,
            "warnings": warnings + result["warnings"],
            "error": None,
        }
    finally:
        resolve_api._release_lock()


def _fill_timeline(
    project: Any,
    mp: Any,
    timeline: Any,
    pool: dict[str, Any],
    present: list[dict[str, Any]],
    layout: str,
    order: str,
    include_markers: bool,
    clip_markers: bool,
) -> dict[str, Any]:
    """Lay the pieces into the new timeline and verify every step by reading it back."""
    warnings: list[str] = []
    if not project.SetCurrentTimeline(timeline) or (
        (project.GetCurrentTimeline() or timeline).GetName() != timeline.GetName()
    ):
        # AppendToTimeline writes to the *current* timeline; never risk the user's.
        raise RuntimeError("Resolve wouldn't switch to the new timeline, so nothing was added")
    tl_fps = float(timeline.GetSetting("timelineFrameRate") or 0) or 25.0
    start = int(timeline.GetStartFrame() or 0)

    pieces = plan_timeline(present, tl_fps, layout, order)
    if layout == "context" and not (
        timeline.AddTrack("video") and timeline.AddTrack("audio", "stereo")
    ):
        raise RuntimeError("Resolve wouldn't add the V2/A2 tracks for the selects")
    items: list[Any] = []
    for track in (1, 2):
        wanted = [p for p in pieces if p.track == track]
        got = _append(mp, wanted, pool, start)
        if len(got) < len(wanted):
            warnings.append(f"Resolve placed {len(got)} of {len(wanted)} pieces on V{track}.")
        items.extend(got)
    linked = _link_audio(timeline, items)
    if linked < len(items):
        warnings.append(f"{len(items) - linked} clip(s) aren't linked to their own audio.")

    select_pieces = [p for p in pieces if p.is_select and p.segment is not None]
    planned = sum(len(c["segments"]) for c in present)
    if len(select_pieces) < planned:
        warnings.append(f"{planned - len(select_pieces)} select(s) were too short to place.")
    names = {str(c["file"]): str(c["name"]) for c in present}
    markers_added = (
        _add_timeline_markers(timeline, select_pieces, names, warnings) if include_markers else 0
    )
    clip_added = (
        _add_clip_markers([pool[c["file"]] for c in present], select_pieces, pool, names, warnings)
        if clip_markers
        else 0
    )

    readback = _readback(timeline)
    for track, count in Counter(f"V{p.track}" for p in pieces).items():
        if readback.get(track, 0) != count:
            warnings.append(f"{track} has {readback.get(track, 0)} clips; planned {count}.")
    return {
        "fps": tl_fps,
        "select_count": len(select_pieces),
        "piece_count": len(pieces),
        "linked_count": linked,
        "markers_added": markers_added,
        "clip_markers_added": clip_added,
        "tracks": readback,
        "warnings": warnings,
    }


def _add_timeline_markers(
    timeline: Any, select_pieces: list[Piece], names: dict[str, str], warnings: list[str]
) -> int:
    if _clear_markers(timeline):
        warnings.append("Some old Cairn markers on the timeline couldn't be removed.")
    added = 0
    for p in select_pieces:
        seg = p.segment or {}
        label, note = marker_note(seg, names[p.file])
        if timeline.AddMarker(
            p.record, seg.get("marker_color", "Green"), label, note, max(1, p.tl_frames), MARKER_TAG
        ):
            added += 1
    if added < len(select_pieces):
        warnings.append(f"Added {added} of {len(select_pieces)} timeline markers.")
    return added


def _add_clip_markers(
    clips_in_shoot: list[Any],
    select_pieces: list[Piece],
    pool: dict[str, Any],
    names: dict[str, str],
    warnings: list[str],
) -> int:
    # Clear on every clip in the build, so a clip that lost its selects loses its old markers too.
    failed = sum(_clear_markers(item) for item in clips_in_shoot)
    if failed:
        warnings.append(f"{failed} old Cairn clip marker(s) couldn't be removed.")
    added = 0
    for p in select_pieces:
        seg = p.segment or {}
        label, note = marker_note(seg, names[p.file])
        if pool[p.file].AddMarker(
            p.src_in,
            seg.get("marker_color", "Green"),
            label,
            note,
            max(1, p.src_out - p.src_in),
            MARKER_TAG,
        ):
            added += 1
    if added < len(select_pieces):
        warnings.append(f"Added {added} of {len(select_pieces)} source-clip markers.")
    return added


def _err(message: str, missing: list[str] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "name": None,
        "layout": None,
        "fps": None,
        "resolution": None,
        "select_count": 0,
        "piece_count": 0,
        "linked_count": 0,
        "markers_added": 0,
        "clip_markers_added": 0,
        "tracks": {},
        "missing_paths": missing or [],
        "warnings": [],
        "error": message,
    }
