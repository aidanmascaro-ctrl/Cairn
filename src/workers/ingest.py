"""
Stage 1 — Ingest worker.

Port of organise.py adapted for the Cairn plugin bridge:
- Emits JSON progress events to stdout (bridge protocol)
- Extended XML metadata extraction (camera, lens, timecode)
- Dry-run mode for tests
- Returns a structured result dict

Never modifies source media. Uses shutil.move only.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from send2trash import send2trash

if TYPE_CHECKING:
    pass

# ── File type constants (verbatim from organise.py) ───────────────────────────

VIDEO_EXTS = {".mp4", ".mov", ".mxf", ".avi", ".m2ts"}
PHOTO_EXTS = {".arw", ".jpg", ".jpeg", ".dng", ".cr2", ".cr3", ".nef", ".raf", ".xmp"}
ASSET_EXTS = {".lut", ".cube", ".bin"}
ASSET_NAMES = {"MEDIAPRO.XML", "DATABASE.BIN"}

SONY_SHELLS = {
    "DCIM",
    "M4ROOT",
    "PRIVATE",
    "GENERAL",
    "CLIP",
    "THMBNL",
    "SUB",
    "TAKE",
    "DATABASE",
    "100MSDCF",
    "101MSDCF",
    "102MSDCF",
    "103MSDCF",
    "104MSDCF",
}

TEMPLATE_DIRS = [
    "01_VIDEOS/A_CAM",
    "01_VIDEOS/B_CAM",
    "01_VIDEOS/C_CAM",
    "02_PHOTOS",
    "03_ASSETS",
    "04_EXPORT",
]

SKIP_NAMES = {
    "TEMPLATE",
    "template one",
    ".Spotlight-V100",
    ".fseventsd",
    ".Trashes",
    "System Volume Information",
}

SYSTEM_PATH_PREFIXES = ["/System/", "/Windows/", "/Program Files/"]


# ── DJI detection ────────────────────────────────────────────────────────────


def is_dji_clip(name: str) -> bool:
    """True for files whose name starts with DJI_ (case-insensitive)."""
    return name.upper().startswith("DJI_")


# ── Thumbnail detection ───────────────────────────────────────────────────────

# Matches Sony's T01/T02 thumbnail suffix: "C9418T01" → base "C9418"
_THUMB_SUFFIX_RE = re.compile(r"^(.+)T\d+$", re.IGNORECASE)
# Sony camera-generated thumbnail: single letter + digits + T + digits (e.g. C9132T01).
# Treated as a thumbnail regardless of whether a matching video exists in the folder —
# thumbnails from a different card session won't share a number range with the videos.
_SONY_THUMB_RE = re.compile(r"^[A-Za-z]\d+T\d+$", re.IGNORECASE)


def is_thumbnail_jpeg(stem: str, video_stems: set[str]) -> bool:
    """
    Return True if this JPEG stem belongs to a camera-generated thumbnail.

    Three patterns:
      1. Exact match       — C9418.JPG  alongside C9418.MP4
      2. Sony T suffix     — C9418T01.JPG, base stem matches a video in this folder
      3. Sony pattern only — C9132T01.JPG where no matching video exists (different card session)
    """
    lower = stem.lower()
    if lower in video_stems:
        return True
    m = _THUMB_SUFFIX_RE.match(stem)
    if m and m.group(1).lower() in video_stems:
        return True
    return bool(_SONY_THUMB_RE.match(stem))


# ── Mode detection ────────────────────────────────────────────────────────────


def is_sony_card(folder: Path) -> bool:
    return (folder / "DCIM").is_dir() or (folder / "M4ROOT").is_dir()


def already_organised(folder: Path) -> bool:
    return any((folder / d).is_dir() for d in ["01_VIDEOS", "02_PHOTOS"])


def has_loose_media(folder: Path) -> bool:
    return any(
        f.is_file() and f.suffix.lower() in VIDEO_EXTS | PHOTO_EXTS for f in folder.rglob("*")
    )


# ── XML metadata extraction ───────────────────────────────────────────────────


def extract_sony_metadata(folder: Path) -> dict[str, str]:
    """
    Extended XML metadata extraction. Returns a dict with keys:
      shoot_date, camera, lens, timecode_start
    All values default to empty string when the field is absent.
    """
    meta: dict[str, str] = {
        "shoot_date": "",
        "camera": "",
        "lens": "",
        "timecode_start": "",
    }

    # MEDIAPRO.XML — shoot date + camera model
    for xml_path in folder.rglob("MEDIAPRO.XML"):
        try:
            root = ET.parse(xml_path).getroot()
            for node in root.iter("Contents"):
                d = node.get("CreationDate", "")
                if d and not meta["shoot_date"]:
                    with contextlib.suppress(ValueError):
                        meta["shoot_date"] = datetime.strptime(d[:10], "%Y-%m-%d").strftime(
                            "%y%m%d"
                        )
            for node in root.iter("Device"):
                m = node.get("modelName", "")
                if m and not meta["camera"]:
                    meta["camera"] = m
        except ET.ParseError:
            pass

    # Per-clip XML — lens + timecode (Sony a7S III, FX3, ZV-E1 etc.)
    for xml_path in folder.rglob("*.XML"):
        if xml_path.name == "MEDIAPRO.XML":
            continue
        try:
            root = ET.parse(xml_path).getroot()

            # Shoot date fallback from clip XML
            if not meta["shoot_date"]:
                for node in root.iter():
                    for attr in ("startDate", "CreationDate"):
                        d = node.get(attr, "")
                        if d and len(d) >= 10:
                            with contextlib.suppress(ValueError):
                                meta["shoot_date"] = datetime.strptime(d[:10], "%Y-%m-%d").strftime(
                                    "%y%m%d"
                                )

            # Camera model from clip XML
            if not meta["camera"]:
                for node in root.iter("Device"):
                    m = node.get("modelName", "")
                    if m:
                        meta["camera"] = m

            # Lens
            if not meta["lens"]:
                for node in root.iter("LensUnitMetadata"):
                    make = node.get("make", "")
                    model = node.get("model", "")
                    lens = f"{make} {model}".strip()
                    if lens:
                        meta["lens"] = lens

            # Timecode: first LTC change table entry
            if not meta["timecode_start"]:
                for node in root.iter("Ltc"):
                    tc = node.get("value", "")
                    if tc:
                        meta["timecode_start"] = tc
                        break

        except ET.ParseError:
            pass

    # Fallback shoot_date to today
    if not meta["shoot_date"]:
        meta["shoot_date"] = datetime.now().strftime("%y%m%d")

    return meta


# ── File collection ───────────────────────────────────────────────────────────


def collect_files(folder: Path) -> dict[str, list[Path]]:
    buckets: dict[str, list[Path]] = {
        "videos": [],
        "dji": [],
        "photos": [],
        "thumbnails": [],
        "assets": [],
    }
    thmbnl = folder / "M4ROOT" / "THMBNL"
    clip = folder / "M4ROOT" / "CLIP"

    # Pass 1 — gather all in-scope files and record video stems so we can
    # identify co-located JPEG thumbnails (e.g. C9418.JPG beside C9418.MP4)
    # in the second pass without having to scan twice.
    all_files: list[Path] = []
    video_stems: set[str] = set()

    for path in folder.rglob("*"):
        if not path.is_file() or path.name == ".DS_Store":
            continue
        rel = path.relative_to(folder).parts
        if rel and rel[0] in {"01_VIDEOS", "02_PHOTOS", "03_ASSETS", "04_EXPORT"}:
            continue
        all_files.append(path)
        if path.suffix.lower() in VIDEO_EXTS:
            video_stems.add(path.stem.lower())

    # Pass 2 — classify
    for path in all_files:
        ext = path.suffix.lower()

        if thmbnl.exists() and thmbnl in path.parents:
            # Sony THMBNL folder — dedicated thumbnail directory
            buckets["thumbnails"].append(path)
        elif ext in VIDEO_EXTS:
            if is_dji_clip(path.name):
                buckets["dji"].append(path)
            else:
                buckets["videos"].append(path)
        elif ext == ".xml":
            if clip.exists() and clip in path.parents:
                # Sony per-clip XML — travels with the video
                if is_dji_clip(path.stem):
                    buckets["dji"].append(path)
                else:
                    buckets["videos"].append(path)
            else:
                buckets["assets"].append(path)
        elif ext in {".jpg", ".jpeg"} and is_thumbnail_jpeg(path.stem, video_stems):
            # Co-located JPEG whose stem matches a video file (exact or Sony T01/T02
            # suffix pattern) — camera-generated proxy, not a real photo.
            buckets["thumbnails"].append(path)
        elif ext in PHOTO_EXTS:
            buckets["photos"].append(path)
        elif ext == "":
            # Extension-less files are photo editing companions (e.g. Photomator
            # Edit Documents, which carry the same stem as the RAW they belong to).
            buckets["photos"].append(path)
        elif ext in ASSET_EXTS or path.name in ASSET_NAMES:
            buckets["assets"].append(path)
        else:
            buckets["assets"].append(path)

    return buckets


# ── File moves ────────────────────────────────────────────────────────────────


def _collision_safe_dest(dest: Path, src: Path) -> Path:
    """Return dest path, appending _1, _2 … if it already exists."""
    if not dest.exists() or dest == src:
        return dest
    stem, suffix, counter = src.stem, src.suffix, 1
    candidate = dest.parent / f"{stem}_{counter}{suffix}"
    while candidate.exists():
        counter += 1
        candidate = dest.parent / f"{stem}_{counter}{suffix}"
    return candidate


def move_files(
    files: list[Path],
    dest: Path,
    dry_run: bool = False,
) -> Iterator[dict]:
    """Move files to dest, yielding one progress event per file."""
    if not files:
        return
    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)
    for src in files:
        dst = _collision_safe_dest(dest / src.name, src)
        if src == dst:
            continue
        if not dry_run:
            shutil.move(str(src), str(dst))
        yield {
            "event": "file_moved",
            "src": str(src),
            "dst": str(dst),
            "dry_run": dry_run,
        }


# ── Empty shell cleanup ───────────────────────────────────────────────────────

# macOS / Windows metadata files that should never block directory removal
_JUNK_FILES = {".DS_Store", ".localized", "Thumbs.db", "desktop.ini"}

# Template dirs we created — never touch these
_KEEP_TOPS = {"01_VIDEOS", "02_PHOTOS", "03_ASSETS", "04_EXPORT"}


def _is_empty_after_junk(dp: Path) -> bool:
    """True if dp has no meaningful content (ignores OS junk files)."""
    for child in dp.iterdir():
        if child.is_file() and child.name in _JUNK_FILES:
            continue
        return False
    return True


def remove_empty_shells(folder: Path, dry_run: bool = False) -> int:
    """
    Move to Trash any directory inside `folder` that is empty after file moves.
    Works bottom-up (topdown=False) so nested empties are cleaned before their
    parents are checked. Ignores macOS .DS_Store files — removes them first so
    they don't block directory removal. Never touches the template directories.
    """
    count = 0
    for dirpath, _dirnames, _filenames in os.walk(folder, topdown=False):
        dp = Path(dirpath)
        if dp == folder:
            continue
        # Never touch the template dirs we created
        rel = dp.relative_to(folder).parts
        if rel and rel[0] in _KEEP_TOPS:
            continue
        if not _is_empty_after_junk(dp):
            continue
        if dry_run:
            count += 1
        else:
            try:
                # Remove OS junk files so the directory is truly empty first
                for child in dp.iterdir():
                    if child.is_file() and child.name in _JUNK_FILES:
                        child.unlink()
                # send2trash handles internal drives, external drives, and
                # Windows Recycle Bin correctly — no manual ~/.Trash needed
                send2trash(str(dp))
                count += 1
            except Exception:  # send2trash raises OSError or TrashPermissionError
                pass
    return count


# ── Pre-flight checks ─────────────────────────────────────────────────────────


class IngestError(Exception):
    pass


def preflight(folder: Path) -> None:
    if not folder.exists():
        raise IngestError(f"Path does not exist: {folder}")
    if not folder.is_dir():
        raise IngestError(f"Path is not a directory: {folder}")

    abs_str = str(folder.resolve())
    for prefix in SYSTEM_PATH_PREFIXES:
        if abs_str.startswith(prefix):
            raise IngestError(f"Refusing to organise a system path: {folder}")

    if not os.access(folder, os.W_OK):
        raise IngestError(f"Path is not writable: {folder}")


# ── Core ingest ───────────────────────────────────────────────────────────────


def run(
    path: str | Path,
    name_override: str | None = None,
    dry_run: bool = False,
) -> Iterator[dict]:
    """
    Full ingest pipeline. Yields progress dicts, finally yields the
    completion event:
      {"event": "complete", "folder": str, "files_moved": int,
       "metadata": {...}, "clip_paths": {"a_cam": [...], "photos": [...], ...}}
    Raises IngestError on pre-flight failure.
    """
    folder = Path(path).resolve()
    preflight(folder)

    # ── Rename ───────────────────────────────────────────────────────────────
    meta = extract_sony_metadata(folder)
    current = folder.name

    if not current[:6].isdigit():
        shoot_name = name_override or current
        new_name = f"{meta['shoot_date']} {shoot_name}"
        dest = folder.parent / new_name
        if not dry_run and not dest.exists():
            folder.rename(dest)
            folder = dest
        elif dry_run:
            folder = dest  # logical rename for progress reporting
        yield {"event": "renamed", "from": current, "to": folder.name, "dry_run": dry_run}
    elif name_override:
        date_prefix = current[:6]
        new_name = f"{date_prefix} {name_override}"
        dest = folder.parent / new_name
        if not dry_run and not dest.exists():
            folder.rename(dest)
            folder = dest
        elif dry_run:
            folder = dest
        yield {"event": "renamed", "from": current, "to": folder.name, "dry_run": dry_run}

    # ── Template dirs ─────────────────────────────────────────────────────────
    for rel in TEMPLATE_DIRS:
        target = folder / rel
        if not dry_run:
            target.mkdir(parents=True, exist_ok=True)
        yield {"event": "dir_created", "path": str(target), "dry_run": dry_run}

    # ── Ensure .cairn sidecar dir ─────────────────────────────────────────────
    cairn_dir = folder / "04_EXPORT" / ".cairn"
    if not dry_run:
        cairn_dir.mkdir(parents=True, exist_ok=True)

    # ── Collect and move ──────────────────────────────────────────────────────
    buckets = collect_files(folder)
    files_moved = 0
    clip_paths: dict[str, list[str]] = {
        "a_cam": [],
        "c_cam": [],
        "photos": [],
        "assets": [],
    }

    for event in move_files(buckets["videos"], folder / "01_VIDEOS" / "A_CAM", dry_run):
        files_moved += 1
        if event["dst"].lower().endswith(tuple(VIDEO_EXTS)):
            clip_paths["a_cam"].append(event["dst"])
        yield {"type": "progress", **event}

    for event in move_files(buckets["dji"], folder / "01_VIDEOS" / "C_CAM", dry_run):
        files_moved += 1
        if event["dst"].lower().endswith(tuple(VIDEO_EXTS)):
            clip_paths["c_cam"].append(event["dst"])
        yield {"type": "progress", **event}

    for event in move_files(buckets["photos"], folder / "02_PHOTOS", dry_run):
        files_moved += 1
        clip_paths["photos"].append(event["dst"])
        yield {"type": "progress", **event}

    for event in move_files(buckets["thumbnails"], folder / "03_ASSETS" / "Thumbnails", dry_run):
        files_moved += 1
        yield {"type": "progress", **event}

    for event in move_files(buckets["assets"], folder / "03_ASSETS", dry_run):
        files_moved += 1
        yield {"type": "progress", **event}

    # ── Reclassify DJI clips already sitting in A_CAM → C_CAM ────────────────
    # Handles folders that were ingested before C_CAM support was added.
    a_cam_dir = folder / "01_VIDEOS" / "A_CAM"
    c_cam_dir = folder / "01_VIDEOS" / "C_CAM"
    if a_cam_dir.is_dir():
        dji_in_a_cam = [
            f
            for f in a_cam_dir.iterdir()
            if f.is_file() and f.suffix.lower() in VIDEO_EXTS and is_dji_clip(f.name)
        ]
        for event in move_files(dji_in_a_cam, c_cam_dir, dry_run):
            files_moved += 1
            if event["dst"].lower().endswith(tuple(VIDEO_EXTS)):
                clip_paths["c_cam"].append(event["dst"])
            yield {"type": "progress", **event}

    # ── Reclassify XMP sidecars already sitting in 03_ASSETS → 02_PHOTOS ────────
    # Handles folders ingested before .xmp was recognised as a photo companion.
    assets_dir = folder / "03_ASSETS"
    photos_dir = folder / "02_PHOTOS"
    if assets_dir.is_dir():
        xmp_in_assets = [
            f
            for f in assets_dir.iterdir()
            if f.is_file() and (f.suffix.lower() == ".xmp" or f.suffix == "")
        ]
        for event in move_files(xmp_in_assets, photos_dir, dry_run):
            files_moved += 1
            clip_paths["photos"].append(event["dst"])
            yield {"type": "progress", **event}

    # ── Reclassify thumbnail JPEGs in 02_PHOTOS → 03_ASSETS/Thumbnails ───────
    # Handles folders ingested before thumbnail detection was added. Any JPEG in
    # 02_PHOTOS whose stem matches a video file in A_CAM or C_CAM is a proxy,
    # not a real photo.
    thumbnails_dir = folder / "03_ASSETS" / "Thumbnails"
    if photos_dir.is_dir():
        video_stems_in_place: set[str] = set()
        for cam_dir in (
            folder / "01_VIDEOS" / "A_CAM",
            folder / "01_VIDEOS" / "C_CAM",
        ):
            if cam_dir.is_dir():
                for f in cam_dir.iterdir():
                    if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
                        video_stems_in_place.add(f.stem.lower())
        thumb_jpegs_in_photos = [
            f
            for f in photos_dir.iterdir()
            if f.is_file()
            and f.suffix.lower() in {".jpg", ".jpeg"}
            and is_thumbnail_jpeg(f.stem, video_stems_in_place)
        ]
        for event in move_files(thumb_jpegs_in_photos, thumbnails_dir, dry_run):
            files_moved += 1
            yield {"type": "progress", **event}

    # ── Empty shell cleanup ───────────────────────────────────────────────────
    shells_removed = remove_empty_shells(folder, dry_run)
    if shells_removed:
        yield {"event": "shells_removed", "count": shells_removed, "dry_run": dry_run}

    yield {
        "event": "complete",
        "folder": str(folder),
        "files_moved": files_moved,
        "shells_removed": shells_removed,
        "metadata": meta,
        "clip_paths": clip_paths,
        "dry_run": dry_run,
    }


# ── CLI entry (for manual testing) ───────────────────────────────────────────


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Cairn ingest worker")
    parser.add_argument("path", help="Folder or drive to ingest")
    parser.add_argument("--name", help="Shoot name override")
    parser.add_argument("--dry-run", action="store_true", help="Plan only — no moves")
    args = parser.parse_args()

    for event in run(args.path, name_override=args.name, dry_run=args.dry_run):
        print(json.dumps(event), file=sys.stderr)  # stderr for CLI — stdout reserved for bridge


if __name__ == "__main__":
    main()
