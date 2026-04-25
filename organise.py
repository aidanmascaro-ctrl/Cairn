#!/usr/bin/env python3
"""
File Organiser — Aidan Mascaro
Usage:
  python3 organise.py <folder-or-drive> [--name "shoot name"]

Organises Sony card dumps into the TEMPLATE layout, fully automatic — no prompts.
  01_VIDEOS/A_CAM/   ← MP4 + XML
  01_VIDEOS/B_CAM/   ← empty, reserved for second camera
  02_PHOTOS/         ← ARW + JPG (RAW and JPEG together)
  03_ASSETS/         ← thumbnails, LUT, metadata, misc
  04_EXPORT/         ← empty, ready for renders

Never deletes files — only moves. Removes empty Sony shell folders when done.
If folder already has date prefix (YYMMDD) it keeps that name; otherwise prepends
the date read from Sony metadata (or today's date as fallback).
Pass --name to override the shoot name portion.
"""

import os
import sys
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime

# ── File type buckets ─────────────────────────────────────────────────────────
VIDEO_EXTS  = {".mp4", ".mov", ".mxf", ".avi", ".m2ts"}
PHOTO_EXTS  = {".arw", ".jpg", ".jpeg", ".dng", ".cr2", ".cr3", ".nef", ".raf"}
ASSET_EXTS  = {".lut", ".cube", ".bin"}
ASSET_NAMES = {"MEDIAPRO.XML", "DATABASE.BIN"}

SONY_SHELLS = {
    "DCIM", "M4ROOT", "PRIVATE", "GENERAL", "CLIP", "THMBNL",
    "SUB", "TAKE", "DATABASE",
    "100MSDCF", "101MSDCF", "102MSDCF", "103MSDCF", "104MSDCF",
}

TEMPLATE_DIRS = [
    "01_VIDEOS/A_CAM", "01_VIDEOS/B_CAM",
    "02_PHOTOS", "03_ASSETS", "04_EXPORT",
]

SKIP_NAMES = {
    "TEMPLATE", "template one",
    ".Spotlight-V100", ".fseventsd", ".Trashes", "System Volume Information",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def read_sony_date(folder: Path) -> str | None:
    for xml_path in folder.rglob("MEDIAPRO.XML"):
        try:
            root = ET.parse(xml_path).getroot()
            for node in root.iter("Contents"):
                d = node.get("CreationDate", "")
                if d:
                    return datetime.strptime(d[:10], "%Y-%m-%d").strftime("%y%m%d")
        except Exception:
            pass
    for xml_path in folder.rglob("*.XML"):
        if xml_path.name == "MEDIAPRO.XML":
            continue
        try:
            root = ET.parse(xml_path).getroot()
            for node in root.iter():
                for attr in ("startDate", "CreationDate"):
                    d = node.get(attr, "")
                    if d and len(d) >= 10:
                        return datetime.strptime(d[:10], "%Y-%m-%d").strftime("%y%m%d")
        except Exception:
            pass
    return None


def collect_files(folder: Path) -> dict:
    buckets = {"videos": [], "photos": [], "thumbnails": [], "assets": []}
    thmbnl = folder / "M4ROOT" / "THMBNL"
    clip   = folder / "M4ROOT" / "CLIP"

    for path in folder.rglob("*"):
        if not path.is_file() or path.name == ".DS_Store":
            continue
        # Skip files already inside template dirs
        rel = path.relative_to(folder).parts
        if rel and rel[0] in {"01_VIDEOS", "02_PHOTOS", "03_ASSETS", "04_EXPORT"}:
            continue

        ext = path.suffix.lower()

        if thmbnl.exists() and thmbnl in path.parents:
            buckets["thumbnails"].append(path)
        elif ext in VIDEO_EXTS:
            buckets["videos"].append(path)
        elif ext == ".xml":
            # XML in CLIP folder stays paired with videos
            buckets["videos"].append(path) if (clip.exists() and clip in path.parents) \
                else buckets["assets"].append(path)
        elif ext in PHOTO_EXTS:
            buckets["photos"].append(path)
        elif ext in ASSET_EXTS or path.name in ASSET_NAMES:
            buckets["assets"].append(path)
        else:
            buckets["assets"].append(path)

    return buckets


def move_files(files: list, dest: Path) -> int:
    if not files:
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    moved = 0
    for src in files:
        dst = dest / src.name
        if dst.exists() and dst != src:
            stem, suffix, counter = src.stem, src.suffix, 1
            while dst.exists():
                dst = dest / f"{stem}_{counter}{suffix}"
                counter += 1
        if src != dst:
            shutil.move(str(src), str(dst))
            moved += 1
    return moved


def remove_empty_shells(folder: Path) -> int:
    count = 0
    for dirpath, _, _ in os.walk(folder, topdown=False):
        dp = Path(dirpath)
        if dp == folder:
            continue
        if dp.name.upper() in {s.upper() for s in SONY_SHELLS}:
            try:
                dp.rmdir()
                count += 1
            except OSError:
                pass
    return count


def is_sony_card(folder: Path) -> bool:
    return (folder / "DCIM").is_dir() or (folder / "M4ROOT").is_dir()


def already_organised(folder: Path) -> bool:
    return any((folder / d).is_dir() for d in ["01_VIDEOS", "02_PHOTOS"])


# ── Core organise ─────────────────────────────────────────────────────────────

def organise_shoot(folder: Path, name_override: str = None):
    print(f"\n→ {folder.name}")

    # Rename if needed
    current = folder.name
    if not current[:6].isdigit():
        date = read_sony_date(folder) or datetime.now().strftime("%y%m%d")
        shoot_name = name_override or current
        new_name = f"{date} {shoot_name}"
        dest = folder.parent / new_name
        if not dest.exists():
            folder.rename(dest)
            folder = dest
            print(f"  Renamed → {folder.name}")
        else:
            print(f"  ⚠ {dest.name} already exists, organising in place")
    elif name_override:
        # Has date prefix but user wants a different name
        date = current[:6]
        new_name = f"{date} {name_override}"
        dest = folder.parent / new_name
        if not dest.exists():
            folder.rename(dest)
            folder = dest
            print(f"  Renamed → {folder.name}")

    # Create template dirs
    for rel in TEMPLATE_DIRS:
        (folder / rel).mkdir(parents=True, exist_ok=True)

    # Collect and move
    b = collect_files(folder)
    total = sum(len(v) for v in b.values())
    if total == 0:
        print("  Nothing to move.")
        return

    n_v = move_files(b["videos"],     folder / "01_VIDEOS/A_CAM")
    n_p = move_files(b["photos"],     folder / "02_PHOTOS")
    n_t = move_files(b["thumbnails"], folder / "03_ASSETS")
    n_a = move_files(b["assets"],     folder / "03_ASSETS")

    if n_v: print(f"  01_VIDEOS/A_CAM  {n_v:4d} files")
    if n_p: print(f"  02_PHOTOS        {n_p:4d} files")
    if n_t + n_a: print(f"  03_ASSETS        {n_t + n_a:4d} files")

    removed = remove_empty_shells(folder)
    if removed:
        print(f"  Removed {removed} empty shell folder(s)")

    print(f"  Done.")


# ── Drive scan ────────────────────────────────────────────────────────────────

def scan_drive(drive: Path) -> list:
    candidates = []
    for item in sorted(drive.iterdir()):
        if not item.is_dir() or item.name in SKIP_NAMES or item.name.startswith("."):
            continue
        if already_organised(item):
            continue
        if is_sony_card(item):
            candidates.append(item)
            continue
        loose = [f for f in item.iterdir()
                 if f.is_file() and f.suffix.lower() in VIDEO_EXTS | PHOTO_EXTS]
        if loose:
            candidates.append(item)
            continue
        # One level deeper
        try:
            for sub in item.iterdir():
                if sub.is_dir() and is_sony_card(sub) and not already_organised(sub):
                    candidates.append(sub)
        except PermissionError:
            pass
    return candidates


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if not args:
        print("Usage: python3 organise.py <folder-or-drive> [--name 'shoot name']")
        print("")
        print("Examples:")
        print('  python3 organise.py "/Volumes/Samsung Evo 4TB/sample"')
        print('  python3 organise.py "/Volumes/Samsung Evo 4TB/sample" --name "Sample session"')
        print('  python3 organise.py "/Volumes/Samsung Evo 4TB"          # scan whole drive')
        sys.exit(0)

    # Parse --name flag
    name_override = None
    if "--name" in args:
        idx = args.index("--name")
        name_override = args[idx + 1]
        args = [a for i, a in enumerate(args) if i != idx and i != idx + 1]

    target = Path(args[0])
    if not target.exists():
        print(f"Error: {target} does not exist or is not mounted.")
        sys.exit(1)

    # Single shoot folder
    if is_sony_card(target) or already_organised(target):
        organise_shoot(target, name_override)
        print("\nDone.")
        return

    # Drive root — auto-organise all found folders
    candidates = scan_drive(target)
    if not candidates:
        print("No unorganised folders found.")
        return

    print(f"Found {len(candidates)} folder(s) to organise:")
    for p in candidates:
        print(f"  {p.name}")

    for folder in candidates:
        organise_shoot(folder)

    print("\nAll done.")


if __name__ == "__main__":
    main()
