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


def find_or_create_bin(parent_folder: Any, name: str) -> Any:
    """
    Idempotent: returns the existing sub-folder named `name`, or creates it.
    Must be called with the Resolve lock held (caller's responsibility) or
    wrapped in a single-threaded context.
    """
    for sub in parent_folder.GetSubFolderList():
        if sub.GetName() == name:
            return sub
    created = parent_folder.AddSubFolder(name)
    if created is None:
        raise RuntimeError(f"Resolve refused to create bin '{name}'")
    return created


def build_bin_tree(media_pool: Any) -> dict[str, Any]:
    """
    Create or find the full Cairn bin tree. Returns a dict of named bins:
      { "root", "cairn", "videos", "a_cam", "b_cam", "photos", "assets" }
    """
    root = media_pool.GetRootFolder()
    cairn = find_or_create_bin(root, "Cairn")
    videos = find_or_create_bin(cairn, "01_VIDEOS")
    a_cam = find_or_create_bin(videos, "A_CAM")
    b_cam = find_or_create_bin(videos, "B_CAM")
    photos = find_or_create_bin(cairn, "02_PHOTOS")
    assets = find_or_create_bin(cairn, "03_ASSETS")
    return {
        "root": root,
        "cairn": cairn,
        "videos": videos,
        "a_cam": a_cam,
        "b_cam": b_cam,
        "photos": photos,
        "assets": assets,
    }


def import_clips(media_pool: Any, bin_folder: Any, paths: list[str]) -> list[Any]:
    """
    Import media files into `bin_folder`. Returns the list of MediaPoolItem
    objects created. Skips paths that are already imported (detected by path
    match against existing items).
    """
    if not paths:
        return []

    # Set active bin so ImportMedia puts clips in the right place
    media_pool.SetCurrentFolder(bin_folder)

    existing_paths = {
        item.GetClipProperty("File Path") for item in bin_folder.GetClipList() if item is not None
    }
    new_paths = [p for p in paths if p not in existing_paths]
    if not new_paths:
        return []

    clips = media_pool.ImportMedia(new_paths)
    return clips if clips is not None else []


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


def ingest_into_resolve(
    shoot_folder: str | Path,
    clip_paths: dict[str, list[str]],
    metadata: dict[str, str],
) -> dict[str, int]:
    """
    Full Resolve import for one shoot. Serialised behind the module lock.

    clip_paths keys: "a_cam", "photos", "assets"
    Returns: { "a_cam": N, "photos": N, "assets": N, "total": N }
    """
    _acquire_lock()
    try:
        resolve = get_resolve()
        pm = resolve.GetProjectManager()

        shoot_name = Path(shoot_folder).name
        project = pm.GetCurrentProject()
        if project is None:
            project = pm.CreateProject(shoot_name)
        if project is None:
            raise ResolveUnavailableError("Could not open or create a Resolve project.")

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

        a_cam_clips = import_clips(mp, bins["a_cam"], clip_paths.get("a_cam", []))
        counts["a_cam"] = len(a_cam_clips)
        for clip in a_cam_clips:
            set_clip_metadata(clip, clip_meta)

        photo_clips = import_clips(mp, bins["photos"], clip_paths.get("photos", []))
        counts["photos"] = len(photo_clips)
        for clip in photo_clips:
            set_clip_metadata(clip, clip_meta)

        asset_clips = import_clips(mp, bins["assets"], clip_paths.get("assets", []))
        counts["assets"] = len(asset_clips)

        counts["total"] = sum(counts.values())
        return counts

    finally:
        _release_lock()
