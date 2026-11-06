"""Path validation shared by the ingest, classify and build-timeline entry points."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import bridge
import ingest
import pytest
import timeline


def _drain(events: Iterator[dict]) -> list[dict]:
    return list(events)


@pytest.mark.parametrize(
    "path",
    [
        "/System",
        "/System/Library",
        "/Windows",
        "C:\\Windows\\System32",
        "c:\\windows",
        "D:\\Program Files\\Blackmagic Design",
        "C:\\Program Files (x86)\\Common Files",
        "\\\\?\\C:\\Windows\\System32",
    ],
)
def test_is_system_path_recognises_system_locations(path: str) -> None:
    assert ingest.is_system_path(path)


@pytest.mark.parametrize(
    "path",
    ["/Volumes/Footage/Systematic", "/Users/me/Windows", "D:\\Shoots\\Windows", "/SystemShoots"],
)
def test_is_system_path_allows_ordinary_folders(path: str) -> None:
    assert not ingest.is_system_path(path)


def test_preflight_can_skip_the_writable_check(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o500)
    try:
        with pytest.raises(ingest.IngestError, match="not writable"):
            ingest.preflight(tmp_path)
        ingest.preflight(tmp_path, require_writable=False)
    finally:
        os.chmod(tmp_path, 0o700)


def test_classify_refuses_a_missing_folder_before_scanning(tmp_path: Path) -> None:
    with pytest.raises(ingest.IngestError, match="does not exist"):
        _drain(bridge.handle_classify({"id": "1", "payload": {"path": str(tmp_path / "ghost")}}))


def test_classify_refuses_a_system_path() -> None:
    with pytest.raises(ingest.IngestError, match="system path"):
        _drain(bridge.handle_classify({"id": "1", "payload": {"path": "/System/Library"}}))


def test_classify_refuses_a_symlink_into_a_system_path(tmp_path: Path) -> None:
    link = tmp_path / "innocent"
    link.symlink_to("/System/Library")
    with pytest.raises(ingest.IngestError, match="system path"):
        _drain(bridge.handle_classify({"id": "1", "payload": {"path": str(link)}}))


def test_classify_dry_run_accepts_a_read_only_folder(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o500)
    try:
        events = _drain(
            bridge.handle_classify({"id": "1", "payload": {"path": str(tmp_path), "dry_run": True}})
        )
    finally:
        os.chmod(tmp_path, 0o700)
    assert events[-1]["type"] == "classify_complete"


def test_classify_real_run_refuses_a_read_only_folder(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o500)
    try:
        with pytest.raises(ingest.IngestError, match="not writable"):
            _drain(bridge.handle_classify({"id": "1", "payload": {"path": str(tmp_path)}}))
    finally:
        os.chmod(tmp_path, 0o700)


def test_classify_confirm_refuses_a_system_path_without_writing() -> None:
    msg = {
        "id": "1",
        "payload": {"clip_path": "/x/a.mp4", "label": "A-ROLL", "shoot_folder": "/System/Library"},
    }
    with pytest.raises(ingest.IngestError, match="system path"):
        bridge.handle_classify_confirm(msg)


def test_build_timeline_reports_a_system_path_as_not_ok() -> None:
    result = timeline.build_timeline("/System/Library")
    assert result["ok"] is False
    assert "system path" in result["error"]


def test_build_timeline_reports_a_file_as_not_ok(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"")
    result = timeline.build_timeline(clip)
    assert result["ok"] is False
    assert "not a directory" in result["error"]


def test_build_timeline_accepts_a_read_only_shoot(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o500)
    try:
        result = timeline.build_timeline(tmp_path)
    finally:
        os.chmod(tmp_path, 0o700)
    assert "No classified clips" in result["error"]
