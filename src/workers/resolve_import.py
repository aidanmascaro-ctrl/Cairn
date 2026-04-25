"""
Resolve media pool import — mirrors the manual drag-and-drop of a folder into Master.

Spawned by main.js as an isolated subprocess with a hard kill timeout.
Uses the Python Scripting API (not WorkflowIntegration JS) because only
the Python API supports folder-based recursive import and bin creation.

Requires DaVinci Resolve → Preferences → General →
  "External scripting using" → "Local"

Strategy A (preferred):
  mp.SetCurrentFolder(root)
  mp.ImportMedia([str(shoot_folder)])
  This is identical to dragging the root folder into Master — Resolve
  creates bins matching the filesystem hierarchy automatically.

Strategy B (fallback if A returns 0):
  Manually create the bin tree with AddSubFolder / SetCurrentFolder,
  then import per-bin with file paths.

Usage:
    python3 resolve_import.py <absolute_folder_path>

Output (stdout, one JSON line):
    {"ok": true, "count": N, "strategy": "folder"|"per-bin"}
    {"ok": false, "error": "..."}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_SCRIPTING_MODULES = (
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
    r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules",
)

VIDEO_EXTS = {".mp4", ".mov", ".mxf", ".avi", ".m2ts"}
PHOTO_EXTS = {".arw", ".jpg", ".jpeg", ".dng", ".cr2", ".cr3", ".nef", ".raf"}


def _out(payload: dict) -> None:
    print(json.dumps(payload), flush=True)


def _scan(directory: Path, exts: set[str]) -> list[str]:
    """Return absolute paths of media files in directory (one level, no recursion)."""
    if not directory.is_dir():
        return []
    return [str(f) for f in directory.iterdir() if f.is_file() and f.suffix.lower() in exts]


def _find_or_create(mp: Any, parent: Any, name: str) -> Any:
    """Return existing sub-bin named `name`, or create it under `parent`."""
    for sub in parent.GetSubFolderList():
        if sub.GetName() == name:
            return sub
    # AddSubFolder lives on MediaPool, not on MediaPoolFolder
    created = mp.AddSubFolder(parent, name)
    if created is None:
        raise RuntimeError(f"Resolve refused to create bin '{name}'")
    return created


def _import_into(mp: Any, bin_folder: Any, paths: list[str]) -> int:
    """Set active bin, import file paths, return count of imported clips."""
    if not paths:
        return 0
    mp.SetCurrentFolder(bin_folder)
    imported = mp.ImportMedia(paths)
    return len(imported) if imported else 0


_JPEG_EXTS = {".jpg", ".jpeg"}


def _import_stills(mp: Any, bin_folder: Any, paths: list[str]) -> int:
    """
    Import still image files ensuring every file lands as a discrete clip.

    Camera RAW formats (ARW, DNG, CR2, NEF, RAF) are imported as a single batch —
    Resolve never detects these as image sequences. JPEG files are imported one
    at a time: sequential JPEG filenames (DSC00001.JPG → DSC00581.JPG) trigger
    Resolve's sequence auto-detection when passed together, collapsing them into
    one sequence clip. A single-path ImportMedia call bypasses that detection.
    """
    if not paths:
        return 0
    mp.SetCurrentFolder(bin_folder)
    count = 0

    raw_paths = [p for p in paths if Path(p).suffix.lower() not in _JPEG_EXTS]
    jpg_paths = [p for p in paths if Path(p).suffix.lower() in _JPEG_EXTS]

    if raw_paths:
        result = mp.ImportMedia(raw_paths)
        count += len(result) if result else 0

    for path in jpg_paths:
        result = mp.ImportMedia([path])
        count += len(result) if result else 0

    return count


def _connect_resolve() -> tuple[Any, Any]:
    """
    Connect to Resolve and return (resolve, media_pool).
    Raises RuntimeError with a user-readable message on failure.
    """
    for p in _SCRIPTING_MODULES:
        if Path(p).exists() and p not in sys.path:
            sys.path.append(p)

    try:
        import DaVinciResolveScript as dvr  # type: ignore[import-untyped]  # noqa: N813
    except Exception as exc:
        # Catches ImportError, SystemError (ABI mismatch), OSError (dylib load fail), etc.
        raise RuntimeError(
            f"DaVinciResolveScript load failed ({type(exc).__name__}): {exc}. "
            "Is DaVinci Resolve Studio installed? Python version may be incompatible with "
            "the bundled scripting module (try Python 3.10 or 3.11)."
        ) from exc

    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise RuntimeError(
            "Resolve scripting is disabled. Enable it in: "
            "DaVinci Resolve → Preferences → General → "
            '"External scripting using" → Local'
        )

    pm = resolve.GetProjectManager()
    if pm is None:
        raise RuntimeError("GetProjectManager() returned None")

    project = pm.GetCurrentProject()
    if project is None:
        raise RuntimeError("No project is currently open in DaVinci Resolve")

    mp = project.GetMediaPool()
    if mp is None:
        raise RuntimeError("GetMediaPool() returned None")

    return resolve, mp


def _strategy_folder(mp: Any, root: Any, folder: Path) -> int:
    """
    Strategy A: pass the folder path to ImportMedia with Master as the active bin.
    This replicates dragging the folder into Master — Resolve creates bins that
    mirror the filesystem hierarchy.
    """
    mp.SetCurrentFolder(root)
    imported = mp.ImportMedia([str(folder)])
    return len(imported) if imported else 0


def _strategy_per_bin(mp: Any, root: Any, folder: Path) -> int:
    """
    Build the full Cairn bin tree and import each directory's files individually.

    Passes file paths (not folder paths) to ImportMedia so Resolve receives
    discrete clip items rather than a folder glob. Each bin maps 1-to-1 with a
    filesystem directory; Thumbnails is a sub-bin of 03_ASSETS.
    """
    shoot_bin = _find_or_create(mp, root, folder.name)
    videos_bin = _find_or_create(mp, shoot_bin, "01_VIDEOS")
    a_cam_bin = _find_or_create(mp, videos_bin, "A_CAM")
    b_cam_bin = _find_or_create(mp, videos_bin, "B_CAM")
    c_cam_bin = _find_or_create(mp, videos_bin, "C_CAM")
    photos_bin = _find_or_create(mp, shoot_bin, "02_PHOTOS")
    assets_bin = _find_or_create(mp, shoot_bin, "03_ASSETS")
    thumbnails_bin = _find_or_create(mp, assets_bin, "Thumbnails")

    count = 0
    count += _import_into(mp, a_cam_bin, _scan(folder / "01_VIDEOS" / "A_CAM", VIDEO_EXTS))
    count += _import_into(mp, b_cam_bin, _scan(folder / "01_VIDEOS" / "B_CAM", VIDEO_EXTS))
    count += _import_into(mp, c_cam_bin, _scan(folder / "01_VIDEOS" / "C_CAM", VIDEO_EXTS))
    # Photos and thumbnails use _import_stills so Resolve does not auto-group
    # sequential filenames (DSC00001 to DSC00581) into one image-sequence clip.
    count += _import_stills(mp, photos_bin, _scan(folder / "02_PHOTOS", PHOTO_EXTS))
    # Top-level 03_ASSETS only (not Thumbnails subdir — that goes into thumbnails_bin)
    count += _import_into(mp, assets_bin, _scan(folder / "03_ASSETS", VIDEO_EXTS | PHOTO_EXTS))
    count += _import_stills(
        mp, thumbnails_bin, _scan(folder / "03_ASSETS" / "Thumbnails", PHOTO_EXTS)
    )
    return count


def main() -> None:
    if len(sys.argv) < 2:
        _out({"ok": False, "error": "Usage: resolve_import.py <folder_path>"})
        sys.exit(1)

    folder = Path(sys.argv[1])
    if not folder.is_dir():
        _out({"ok": False, "error": f"Folder does not exist: {folder}"})
        sys.exit(1)

    try:
        _resolve, mp = _connect_resolve()
    except RuntimeError as exc:
        _out({"ok": False, "error": str(exc)})
        sys.exit(1)

    root = mp.GetRootFolder()

    # Per-bin import: build the bin tree explicitly and pass individual file paths
    # to ImportMedia. This is the only strategy — folder-path import (Strategy A)
    # was removed because Resolve's handling of still-photo sequences and sub-bin
    # recursion was unpredictable. Per-bin gives full control over bin structure
    # and ensures each file is imported as a discrete clip.
    try:
        count = _strategy_per_bin(mp, root, folder)
        _out({"ok": True, "count": count, "strategy": "per-bin"})
    except RuntimeError as exc:
        _out({"ok": False, "error": str(exc)})
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback as _tb

        _out(
            {
                "ok": False,
                "error": f"Unhandled {type(exc).__name__}: {exc}",
                "traceback": _tb.format_exc(),
            }
        )
        sys.exit(1)
