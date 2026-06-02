"""Harness-local sidecar cache. Keyed by sha256(path)[:16]; merge-on-write."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def clip_key(file_path: str) -> str:
    return hashlib.sha256(str(file_path).encode("utf-8")).hexdigest()[:16]


def cache_file(root, clip_id: str) -> Path:
    return Path(root) / f"{clip_id}.json"


def read(root, clip_id: str):
    p = cache_file(root, clip_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def write(root, clip_id: str, record: dict) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    merged = {**(read(root, clip_id) or {}), **record}
    fd, tmp = tempfile.mkstemp(dir=root, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, cache_file(root, clip_id))


def source_signature(file_path) -> str:
    st = os.stat(file_path)
    return f"{int(st.st_mtime)}:{st.st_size}"


def is_fresh(record, file_path) -> bool:
    if not record:
        return False
    return record.get("source_sig") == source_signature(file_path)
