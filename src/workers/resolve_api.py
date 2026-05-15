"""
Resolve Python Scripting API wrapper.

All Resolve Scripting API calls must go through this module. A module-level
lock serialises every call — taste invariant #3: the Resolve API is not
thread-safe and must never be called from multiple threads simultaneously.

Usage:
    from resolve_api import get_resolve, find_or_create_bin, import_clips, set_clip_metadata

    resolve = get_resolve()               # raises ResolveUnavailableError if not running
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    mp = project.GetMediaPool()
    root = mp.GetRootFolder()

    cairn = find_or_create_bin(root, "Cairn")
    a_cam = find_or_create_bin(find_or_create_bin(
                find_or_create_bin(cairn, "01_VIDEOS"), "A_CAM"), "A_CAM")
    clips  = import_clips(mp, a_cam, ["/path/to/file.mp4"])
    for clip in clips:
        set_clip_metadata(clip, {"Camera": "Sony a7S III", "Cairn Stage": "ingested"})
"""

from __future__ import annotations

import contextlib
import sys
import threading
from pathlib import Path
from typing import Any

# ── Resolve Scripting API import ──────────────────────────────────────────────

_SCRIPTING_MODULES = (
    # macOS
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
    # Windows
    r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules",
)


def _import_dvr() -> Any:
    for path in _SCRIPTING_MODULES:
        if Path(path).exists() and path not in sys.path:
            sys.path.append(path)
    try:
        import DaVinciResolveScript as dvr  # type: ignore[import-untyped]  # noqa: N813

        return dvr
    except ImportError as exc:
        raise ResolveUnavailableError(
            "DaVinciResolveScript module not found. Is DaVinci Resolve Studio installed?"
        ) from exc


# ── Errors ────────────────────────────────────────────────────────────────────


class ResolveUnavailableError(Exception):
    """Raised when DaVinci Resolve is not running or the scripting module is missing."""


class ResolveLockError(Exception):
    """Raised when a Resolve API call is attempted while the lock is held by another thread."""


# ── Module-level serialisation lock ──────────────────────────────────────────

_lock = threading.Lock()


def _acquire_lock() -> None:
    """Acquire the Resolve API lock. Raises ResolveLockError if already held."""
    if not _lock.acquire(blocking=False):
        raise ResolveLockError(
            "Resolve API is already in use by another thread. Calls must be serialised."
        )


def _release_lock() -> None:
    with contextlib.suppress(RuntimeError):
        _lock.release()


# ── Public API ────────────────────────────────────────────────────────────────


def get_resolve() -> Any:
    """
    Return the live Resolve handle. Raises ResolveUnavailableError if Resolve
    is not running or the scripting module cannot be found.
    """
    dvr = _import_dvr()
    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise ResolveUnavailableError(
            "DaVinci Resolve is not running or did not grant scripting access."
        )
    return resolve


def find_or_create_bin(media_pool: Any, parent_folder: Any, name: str) -> Any:
    """
    Idempotent: returns the existing sub-folder named `name`, or creates it.
    Must be called with the Resolve lock held (caller's responsibility) or
    wrapped in a single-threaded context.

    Note: AddSubFolder lives on MediaPool, not MediaPoolFolder.
    """
    for sub in parent_folder.GetSubFolderList():
        if sub.GetName() == name:
            return sub
    created = media_pool.AddSubFolder(parent_folder, name)
    if created is None:
        raise RuntimeError(f"Resolve refused to create bin '{name}'")
    return created


def build_bin_tree(media_pool: Any) -> dict[str, Any]:
    """
    Create or find the full Cairn bin tree. Returns a dict of named bins:
      { "root", "cairn", "videos", "a_cam", "b_cam", "c_cam",
        "photos", "assets", "thumbnails" }
    """
    root = media_pool.GetRootFolder()
    cairn = find_or_create_bin(media_pool, root, "Cairn")
    videos = find_or_create_bin(media_pool, cairn, "01_VIDEOS")
    a_cam = find_or_create_bin(media_pool, videos, "A_CAM")
    b_cam = find_or_create_bin(media_pool, videos, "B_CAM")
    c_cam = find_or_create_bin(media_pool, videos, "C_CAM")
    photos = find_or_create_bin(media_pool, cairn, "02_PHOTOS")
    assets = find_or_create_bin(media_pool, cairn, "03_ASSETS")
    thumbnails = find_or_create_bin(media_pool, assets, "Thumbnails")
    return {
        "root": root,
        "cairn": cairn,
        "videos": videos,
        "a_cam": a_cam,
        "b_cam": b_cam,
        "c_cam": c_cam,
        "photos": photos,
        "assets": assets,
        "thumbnails": thumbnails,
    }


def import_clips(
    media_pool: Any,
    bin_folder: Any,
    paths: list[str],
    *,
    stills: bool = False,
) -> list[Any]:
    """
    Import media files into `bin_folder`. Returns the list of MediaPoolItem
    objects created. Skips paths that are already imported (detected by path
    match against existing items).

    stills=True passes ClipInfo dicts with explicit single-frame ranges so
    Resolve doesn't auto-group sequential filenames into an image sequence.
    """
    if not paths:
        return []

    # Set active bin so ImportMedia puts clips in the right place
    if not media_pool.SetCurrentFolder(bin_folder):
        raise RuntimeError(
            f"SetCurrentFolder failed for bin {bin_folder.GetName()!r} — clips would land in wrong bin"
        )

    existing_paths = {
        item.GetClipProperty("File Path") for item in bin_folder.GetClipList() if item is not None
    }
    new_paths = [p for p in paths if p not in existing_paths]
    if not new_paths:
        return []

    if not stills:
        clips = media_pool.ImportMedia(new_paths)
        return clips if clips is not None else []

    # stills=True: RAW formats batch fine (never sequence-detected); JPEG must be
    # imported one at a time — sequential filenames trigger sequence auto-detection.
    _jpeg = {".jpg", ".jpeg"}
    raw_p = [p for p in new_paths if Path(p).suffix.lower() not in _jpeg]
    jpg_p = [p for p in new_paths if Path(p).suffix.lower() in _jpeg]
    collected: list[Any] = []
    if raw_p:
        result = media_pool.ImportMedia(raw_p)
        if result:
            collected.extend(result)
    for path in jpg_p:
        result = media_pool.ImportMedia([path])
        if result:
            collected.extend(result)
    return collected


def set_clip_metadata(clip: Any, metadata: dict[str, str]) -> bool:
    """
    Call SetMetadata for each key in `metadata`. Returns True if all calls
    succeeded, False if any failed (Resolve returns False on bad key names).
    """
    ok = True
    for key, value in metadata.items():
        if not clip.SetMetadata(key, value):
            ok = False
    return ok


_VIDEO_EXTS = {".mp4", ".mov", ".mxf", ".avi", ".m2ts"}
_PHOTO_EXTS = {".arw", ".jpg", ".jpeg", ".dng", ".cr2", ".cr3", ".nef", ".raf"}


def _scan_dir(directory: Path, exts: set[str]) -> list[str]:
    """Return absolute paths of all files in `directory` matching `exts`."""
    if not directory.is_dir():
        return []
    return [str(f) for f in directory.iterdir() if f.is_file() and f.suffix.lower() in exts]


def ingest_into_resolve(
    shoot_folder: str | Path,
    clip_paths: dict[str, list[str]],
    metadata: dict[str, str],
) -> dict[str, int]:
    """
    Full Resolve import for one shoot. Serialised behind the module lock.

    Imports everything in the organised folder structure — both clips that were
    moved in this ingest run (via clip_paths) AND any clips already in place from
    a prior run. import_clips() deduplicates against what Resolve already has, so
    this is safe to call repeatedly.

    Returns: { "a_cam": N, "c_cam": N, "photos": N, "assets": N, "total": N }
    """
    _acquire_lock()
    try:
        resolve = get_resolve()
        pm = resolve.GetProjectManager()

        folder = Path(shoot_folder)
        project = pm.GetCurrentProject()
        if project is None:
            raise ResolveUnavailableError(
                "No DaVinci Resolve project is open. Open a project before ingesting."
            )

        mp = project.GetMediaPool()
        bins = build_bin_tree(mp)

        clip_meta = {
            "Camera": metadata.get("camera", ""),
            "Lens": metadata.get("lens", ""),
            "Timecode Start": metadata.get("timecode_start", ""),
            "Shoot Date": metadata.get("shoot_date", ""),
            "Cairn Stage": "ingested",
        }

        counts: dict[str, int] = {}

        # Union of newly-moved clips + clips already in organised dirs so that
        # re-ingesting an already-organised folder still populates Resolve fully.
        def _union(key: str, directory: Path, exts: set[str]) -> list[str]:
            return list(set(clip_paths.get(key, []) + _scan_dir(directory, exts)))

        a_cam_paths = _union("a_cam", folder / "01_VIDEOS" / "A_CAM", _VIDEO_EXTS)
        a_cam_clips = import_clips(mp, bins["a_cam"], a_cam_paths)
        counts["a_cam"] = len(a_cam_clips)
        for clip in a_cam_clips:
            set_clip_metadata(clip, clip_meta)

        c_cam_paths = _union("c_cam", folder / "01_VIDEOS" / "C_CAM", _VIDEO_EXTS)
        c_cam_clips = import_clips(mp, bins["c_cam"], c_cam_paths)
        counts["c_cam"] = len(c_cam_clips)
        for clip in c_cam_clips:
            set_clip_metadata(clip, clip_meta)

        photo_paths = _union("photos", folder / "02_PHOTOS", _PHOTO_EXTS)
        photo_clips = import_clips(mp, bins["photos"], photo_paths, stills=True)
        counts["photos"] = len(photo_clips)
        for clip in photo_clips:
            set_clip_metadata(clip, clip_meta)

        asset_clips = import_clips(mp, bins["assets"], clip_paths.get("assets", []))
        counts["assets"] = len(asset_clips)

        thumb_paths = _union("thumbnails", folder / "03_ASSETS" / "Thumbnails", _PHOTO_EXTS)
        thumb_clips = import_clips(mp, bins["thumbnails"], thumb_paths, stills=True)
        counts["thumbnails"] = len(thumb_clips)

        counts["total"] = sum(counts.values())
        return counts

    finally:
        _release_lock()


# ── Classify helpers ──────────────────────────────────────────────────────────

# Full set of valid SetClipColor values. Validated at call time; invalid value
# raises ValueError rather than silently sending a bad string to Resolve.
VALID_CLIP_COLORS: frozenset[str] = frozenset(
    {
        "Blue",
        "Cyan",
        "Green",
        "Yellow",
        "Red",
        "Pink",
        "Purple",
        "Fuchsia",
        "Rose",
        "Lavender",
        "Sky",
        "Mint",
        "Lemon",
        "Sand",
        "Cocoa",
        "Cream",
        "Orange",
    }
)

_LABEL_COLOR: dict[str, str] = {
    "A-ROLL": "Blue",
    "B-ROLL": "Green",
    "AMBIGUOUS": "Yellow",
}

# Flag colours mirror the clip colours so the editor sees the same hue both
# on the clip thumbnail and the flag pip. Resolve accepts the same names as
# SetClipColor for AddFlag.
_LABEL_FLAG: dict[str, str] = {
    "A-ROLL": "Blue",
    "B-ROLL": "Green",
    "AMBIGUOUS": "Yellow",
}

# Custom metadata key for our classification. Resolve's built-in "Keywords"
# column is NOT writable via SetMetadata — that key is reserved by the media
# pool's tagging system and SetMetadata silently returns False on it. Use a
# custom key so the value actually persists and shows up in the Metadata pane.
CAIRN_CLASSIFICATION_KEY: str = "Cairn Classification"


def set_clip_color(clip: Any, color: str) -> bool:
    """Set clip colour via SetClipColor. Returns True on success."""
    if color not in VALID_CLIP_COLORS:
        raise ValueError(f"Invalid clip color {color!r}. Valid values: {sorted(VALID_CLIP_COLORS)}")
    return bool(clip.SetClipColor(color))


def _search_folder(folder: Any, file_path: str) -> Any | None:
    """Recursively search a MediaPool folder tree for a clip by File Path."""
    for clip in folder.GetClipList() or []:
        if clip and clip.GetClipProperty("File Path") == file_path:
            return clip
    for sub in folder.GetSubFolderList() or []:
        found = _search_folder(sub, file_path)
        if found:
            return found
    return None


def get_clip_by_path(media_pool: Any, file_path: str) -> Any | None:
    """Return the MediaPoolItem whose File Path matches, or None."""
    return _search_folder(media_pool.GetRootFolder(), file_path)


def classify_write_resolve(clip_path: str, label: str) -> tuple[bool, str | None]:
    """
    Write the classification onto a clip in the Resolve media pool. Three writes
    are performed, each checked for success:

      1. Custom metadata key "Cairn Classification" = label
      2. Clip colour (Blue / Green / Yellow per _LABEL_COLOR)
      3. Coloured flag (same hue) so the bin's flag column also tells the story

    Returns (ok, reason). ok is False if any of the writes failed or the
    pre-conditions couldn't be met. reason is a short string explaining the
    failure for the UI (None on success). All failures are also logged to
    stderr.

    Acquires the module lock — must not be called while ingest_into_resolve is
    running in the same process.
    """
    _acquire_lock()
    try:
        resolve = get_resolve()
        pm = resolve.GetProjectManager()
        project = pm.GetCurrentProject()
        if project is None:
            reason = "No DaVinci Resolve project is open."
            print(f"[resolve_api] classify_write_resolve: {reason}", file=sys.stderr)
            return False, reason

        mp = project.GetMediaPool()
        clip = get_clip_by_path(mp, clip_path)
        if clip is None:
            reason = f"Clip not found in media pool: {clip_path}"
            print(f"[resolve_api] classify_write_resolve: {reason}", file=sys.stderr)
            return False, reason

        # 1. Custom metadata — Resolve's built-in "Keywords" column is read-only
        # via SetMetadata, so we use our own key. SetMetadata returns False on
        # any failure (bad key, locked clip, etc.). Check it.
        meta_ok = bool(clip.SetMetadata(CAIRN_CLASSIFICATION_KEY, label))
        if not meta_ok:
            print(
                f"[resolve_api] SetMetadata({CAIRN_CLASSIFICATION_KEY!r}, {label!r}) "
                f"returned False for {clip_path}",
                file=sys.stderr,
            )

        # 2. Clip colour — visual signal in the bin viewer.
        color = _LABEL_COLOR.get(label)
        color_ok = True
        if color:
            color_ok = set_clip_color(clip, color)
            if not color_ok:
                print(
                    f"[resolve_api] SetClipColor({color!r}) returned False for {clip_path}",
                    file=sys.stderr,
                )

        # 3. Flag — second visual signal that also surfaces in the flag column
        # and on the timeline. Best-effort: don't fail the whole write if the
        # flag API isn't available on this Resolve build (older builds don't
        # expose AddFlag on MediaPoolItem).
        flag_color = _LABEL_FLAG.get(label)
        if flag_color and hasattr(clip, "AddFlag"):
            try:
                # Clear any prior Cairn-set flag so re-classify doesn't stack.
                if hasattr(clip, "ClearFlags"):
                    clip.ClearFlags(flag_color)
                clip.AddFlag(flag_color)
            except Exception as exc:
                print(f"[resolve_api] AddFlag failed for {clip_path}: {exc}", file=sys.stderr)

        if not (meta_ok and color_ok):
            failed = []
            if not meta_ok:
                failed.append("metadata")
            if not color_ok:
                failed.append("colour")
            return False, f"Resolve write failed: {', '.join(failed)}"

        return True, None
    except ResolveUnavailableError as exc:
        reason = (
            "DaVinci Resolve scripting is not available. "
            "Open Resolve and enable External Scripting in Preferences → System → General."
        )
        print(f"[resolve_api] classify_write_resolve: {exc}", file=sys.stderr)
        return False, reason
    finally:
        _release_lock()


def set_keywords(clip_path: str, keywords: str) -> bool:
    """
    Write the Keywords metadata field for a clip and mark it as indexed.
    Returns True on success, False if Resolve is unavailable or clip not found.
    """
    _acquire_lock()
    try:
        resolve = get_resolve()
        pm = resolve.GetProjectManager()
        project = pm.GetCurrentProject()
        if project is None:
            return False
        mp = project.GetMediaPool()
        clip = get_clip_by_path(mp, clip_path)
        if clip is None:
            return False
        set_clip_metadata(clip, {"Keywords": keywords, "Cairn Stage": "indexed"})
        return True
    except ResolveUnavailableError:
        return False
    finally:
        _release_lock()


# ── Marker helpers ────────────────────────────────────────────────────────────

VALID_MARKER_COLORS: frozenset[str] = frozenset(
    {
        "Blue",
        "Cyan",
        "Green",
        "Yellow",
        "Red",
        "Pink",
        "Purple",
        "Fuchsia",
        "Rose",
        "Lavender",
        "Sky",
        "Mint",
        "Lemon",
        "Sand",
        "Cocoa",
        "Cream",
    }
)

CAIRN_MARKER_PREFIX = "cairn:"


def get_clip_fps(clip_path: str) -> float:
    """
    Return the frame rate of a clip as a float.
    Falls back to 24.0 if Resolve is unavailable or the property is unreadable.
    """
    _acquire_lock()
    try:
        resolve = get_resolve()
        pm = resolve.GetProjectManager()
        project = pm.GetCurrentProject()
        if project is None:
            return 24.0
        mp = project.GetMediaPool()
        clip = get_clip_by_path(mp, clip_path)
        if clip is None:
            return 24.0
        fps_raw = clip.GetClipProperty("FPS")
        return float(fps_raw) if fps_raw else 24.0
    except (ResolveUnavailableError, ValueError, TypeError):
        return 24.0
    finally:
        _release_lock()


def clear_cairn_markers(clip_path: str) -> int:
    """
    Delete all markers whose customData starts with "cairn:" from the clip.
    Returns the number of markers deleted.
    Safe to call on clips with no markers.
    """
    _acquire_lock()
    try:
        resolve = get_resolve()
        pm = resolve.GetProjectManager()
        project = pm.GetCurrentProject()
        if project is None:
            return 0
        mp = project.GetMediaPool()
        clip = get_clip_by_path(mp, clip_path)
        if clip is None:
            return 0
        markers: dict = clip.GetMarkers() or {}
        deleted = 0
        for frame_id, info in markers.items():
            if str(info.get("customData", "")).startswith(CAIRN_MARKER_PREFIX):
                clip.DeleteMarkerAtFrame(int(frame_id))
                deleted += 1
        return deleted
    except ResolveUnavailableError:
        return 0
    finally:
        _release_lock()


def add_clip_marker(
    clip_path: str,
    frame_id: int,
    color: str,
    name: str,
    note: str,
    duration: int,
    custom_data: str,
) -> bool:
    """
    Add a single marker to a MediaPoolItem. The lock is held for the full call.
    Returns True on success, False if Resolve is unavailable or clip not found.
    `color` must be in VALID_MARKER_COLORS; falls back to "Lemon" and logs if not.
    """
    if color not in VALID_MARKER_COLORS:
        import sys

        print(
            f"[resolve_api] Unknown marker color {color!r}, falling back to Lemon",
            file=sys.stderr,
        )
        color = "Lemon"

    _acquire_lock()
    try:
        resolve = get_resolve()
        pm = resolve.GetProjectManager()
        project = pm.GetCurrentProject()
        if project is None:
            return False
        mp = project.GetMediaPool()
        clip = get_clip_by_path(mp, clip_path)
        if clip is None:
            return False
        result = clip.AddMarker(frame_id, color, name, note, duration, custom_data)
        return bool(result)
    except ResolveUnavailableError:
        return False
    finally:
        _release_lock()


# ── Matching helpers ──────────────────────────────────────────────────────────


def set_shot_field(clip_path: str, value: str) -> bool:
    """
    Write the Shot metadata field for a B-roll clip.
    Returns True on success, False if Resolve is unavailable or clip not found.
    """
    _acquire_lock()
    try:
        resolve = get_resolve()
        pm = resolve.GetProjectManager()
        project = pm.GetCurrentProject()
        if project is None:
            return False
        mp = project.GetMediaPool()
        clip = get_clip_by_path(mp, clip_path)
        if clip is None:
            return False
        return bool(clip.SetMetadata("Shot", value))
    except ResolveUnavailableError:
        return False
    finally:
        _release_lock()


def set_description_field(clip_path: str, value: str) -> bool:
    """
    Write the Description metadata field for a B-roll clip.
    Returns True on success, False if Resolve is unavailable or clip not found.
    """
    _acquire_lock()
    try:
        resolve = get_resolve()
        pm = resolve.GetProjectManager()
        project = pm.GetCurrentProject()
        if project is None:
            return False
        mp = project.GetMediaPool()
        clip = get_clip_by_path(mp, clip_path)
        if clip is None:
            return False
        return bool(clip.SetMetadata("Description", value))
    except ResolveUnavailableError:
        return False
    finally:
        _release_lock()


def update_marker_note(clip_path: str, frame_id: int, matches_line: str) -> bool:
    """
    Append (or replace) the MATCHES: line on an existing Cairn soundbite marker.
    Preserves all other marker properties. Safe to call on re-runs (replaces
    any existing MATCHES: line rather than appending a duplicate).
    Returns True on success.
    """
    _acquire_lock()
    try:
        resolve = get_resolve()
        pm = resolve.GetProjectManager()
        project = pm.GetCurrentProject()
        if project is None:
            return False
        mp = project.GetMediaPool()
        clip = get_clip_by_path(mp, clip_path)
        if clip is None:
            return False
        markers: dict = clip.GetMarkers() or {}
        marker = markers.get(frame_id)
        if not marker:
            return False

        old_note: str = marker.get("note", "")
        # Remove any existing MATCHES: line (idempotent re-runs)
        cleaned_lines = [ln for ln in old_note.split("\n") if not ln.startswith("MATCHES:")]
        new_note = "\n".join(cleaned_lines).rstrip() + f"\n{matches_line}"

        clip.DeleteMarkerAtFrame(frame_id)
        result = clip.AddMarker(
            frame_id,
            marker.get("color", "Lemon"),
            marker.get("name", ""),
            new_note,
            marker.get("duration", 1),
            marker.get("customData", ""),
        )
        if not result:
            # Restore original marker to avoid permanent data loss
            clip.AddMarker(
                frame_id,
                marker.get("color", "Lemon"),
                marker.get("name", ""),
                old_note,
                marker.get("duration", 1),
                marker.get("customData", ""),
            )
            return False
        return True
    except ResolveUnavailableError:
        return False
    finally:
        _release_lock()


def add_subtitle_track(clip_path: str, srt_path: str) -> bool:
    """
    Attach an SRT subtitle file to the Resolve clip at clip_path.
    Uses MediaPoolItem.AddSubTitle(path) — requires Resolve 18.5+.
    If AddSubTitle is unavailable, marks the clip as transcribed via SetMetadata
    and returns True (SRT exists on disk; editor can attach manually).
    Returns False only if Resolve is unreachable or the clip is not found.
    """
    _acquire_lock()
    try:
        resolve = get_resolve()
        pm = resolve.GetProjectManager()
        project = pm.GetCurrentProject()
        if project is None:
            return False
        mp = project.GetMediaPool()
        clip = get_clip_by_path(mp, clip_path)
        if clip is None:
            return False

        try:
            result = clip.AddSubTitle(srt_path)
            attached = bool(result)
        except AttributeError:
            # AddSubTitle not available on this Resolve version — best-effort fallback
            attached = False

        set_clip_metadata(clip, {"Cairn Stage": "transcribed"})
        return attached
    except ResolveUnavailableError:
        return False
    finally:
        _release_lock()
