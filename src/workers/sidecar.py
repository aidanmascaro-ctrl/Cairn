"""
Sidecar JSON read/write for Cairn.

Each clip gets one JSON file at:
  <shoot_folder>/04_EXPORT/.cairn/<clip-id>.json

clip-id = sha256(absolute_path_at_ingest_time)[:16]

Schema version 1. Bump on breaking change; migrate on load.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1


def clip_id(absolute_path: str | Path) -> str:
    """Stable 16-char hex ID derived from the absolute path at ingest time."""
    return hashlib.sha256(str(Path(absolute_path).resolve()).encode()).hexdigest()[:16]


def sidecar_root(shoot_folder: str | Path) -> Path:
    """Returns the .cairn directory path (does not create it)."""
    return Path(shoot_folder) / "04_EXPORT" / ".cairn"


def _atomic_write(target: Path, record: dict) -> None:
    """Write via a temp file + rename so a crash never leaves a half-written sidecar."""
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record, indent=2))
    os.replace(tmp, target)


def _quarantine(target: Path, exc: Exception) -> None:
    """Keep an unreadable sidecar for recovery instead of silently overwriting it."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    backup = target.with_name(f"{target.name}.corrupt-{stamp}")
    try:
        target.replace(backup)
        print(f"[sidecar] {target} unreadable ({exc}); kept as {backup.name}", file=sys.stderr)
    except OSError as move_exc:
        print(
            f"[sidecar] {target} unreadable ({exc}) and could not be moved: {move_exc}",
            file=sys.stderr,
        )


def is_unreadable(shoot_folder: str | Path, absolute_path: str | Path) -> bool:
    """True when a sidecar exists for the clip but can't be parsed."""
    target = sidecar_root(shoot_folder) / f"{clip_id(absolute_path)}.json"
    if not target.exists():
        return False
    try:
        json.loads(target.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return True
    return False


def write_clip(
    shoot_folder: str | Path,
    absolute_path: str | Path,
    bin_path: str,
    kind: str,
    metadata: dict[str, str],
    extra_stages: list[str] | None = None,
) -> Path:
    """
    Write (or merge) a sidecar record for one clip.

    If a sidecar already exists, stages_complete is merged so re-ingest
    doesn't lose later-stage data.

    Returns the path of the written sidecar file.
    """
    cid = clip_id(absolute_path)
    root = sidecar_root(shoot_folder)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{cid}.json"

    existing_stages: list[str] = []
    if target.exists():
        try:
            existing = json.loads(target.read_text())
            existing_stages = existing.get("stages_complete", [])
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            _quarantine(target, exc)

    stages = list(dict.fromkeys(existing_stages + ["ingest"] + (extra_stages or [])))

    record = {
        "schema_version": SCHEMA_VERSION,
        "clip_id": cid,
        "source_path": str(Path(absolute_path).resolve()),
        "shoot_folder": str(Path(shoot_folder).resolve()),
        "bin_path": bin_path,
        "kind": kind,
        "metadata": metadata,
        "stages_complete": stages,
    }

    _atomic_write(target, record)
    return target


def merge_clip(
    shoot_folder: str | Path,
    absolute_path: str | Path,
    updates: dict,
    new_stage: str | None = None,
) -> Path:
    """
    Merge additional fields into an existing sidecar, preserving all prior data.
    Adds new_stage to stages_complete if provided and not already present.
    Creates a minimal sidecar if none exists.
    """
    cid = clip_id(absolute_path)
    root = sidecar_root(shoot_folder)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{cid}.json"

    record: dict
    if target.exists():
        try:
            record = json.loads(target.read_text())
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            _quarantine(target, exc)
            record = {
                "schema_version": SCHEMA_VERSION,
                "clip_id": cid,
                "source_path": str(Path(absolute_path).resolve()),
                "stages_complete": [],
            }
    else:
        record = {
            "schema_version": SCHEMA_VERSION,
            "clip_id": cid,
            "source_path": str(Path(absolute_path).resolve()),
            "stages_complete": [],
        }

    record.update(updates)

    if new_stage and new_stage not in record.get("stages_complete", []):
        record["stages_complete"] = [*record.get("stages_complete", []), new_stage]

    _atomic_write(target, record)
    return target


def read_clip(shoot_folder: str | Path, absolute_path: str | Path) -> dict | None:
    """
    Read the sidecar for a clip. Returns None if not found or unreadable.
    Migrates schema_version 0→1 transparently (adds missing keys).
    """
    cid = clip_id(absolute_path)
    target = sidecar_root(shoot_folder) / f"{cid}.json"
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    # Forward-compat: add missing keys for older schema versions
    data.setdefault("schema_version", 1)
    data.setdefault("stages_complete", [])
    return data
