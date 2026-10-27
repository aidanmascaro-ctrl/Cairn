"""
Tests for src/workers/timeline.py — Stage 4 (build assembly timeline).

Covers the pure-Python paths: sidecar reading, resolution parsing,
modal computation, name uniqueness, and the no-clips error path.
The Resolve API integration is exercised via the live plugin in dev.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import timeline

# ── _classified_clips ─────────────────────────────────────────────────────────


def _write_sidecar(
    cairn_dir: Path,
    clip_id: str,
    *,
    classification: str,
    source_path: str,
    stages: list[str] | None = None,
) -> None:
    cairn_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1,
        "clip_id": clip_id,
        "source_path": source_path,
        "classification": classification,
        "stages_complete": stages or ["ingest", "classify"],
    }
    (cairn_dir / f"{clip_id}.json").write_text(json.dumps(record))


def test_classified_clips_groups_by_label(tmp_path: Path) -> None:
    cairn = tmp_path / "04_EXPORT" / ".cairn"
    _write_sidecar(cairn, "aaa", classification="A-ROLL", source_path="/foo/c01.mp4")
    _write_sidecar(cairn, "bbb", classification="B-ROLL", source_path="/foo/c02.mp4")
    _write_sidecar(cairn, "ccc", classification="A-ROLL", source_path="/foo/c00.mp4")
    _write_sidecar(cairn, "ddd", classification="AMBIGUOUS", source_path="/foo/c03.mp4")

    bins = timeline._classified_clips(tmp_path)

    assert bins["A-ROLL"] == ["/foo/c00.mp4", "/foo/c01.mp4"]
    assert bins["B-ROLL"] == ["/foo/c02.mp4"]
    assert bins["AMBIGUOUS"] == ["/foo/c03.mp4"]


def test_classified_clips_reads_offload_shoot_sidecars(tmp_path: Path) -> None:
    # Offload shoots keep clip sidecars beside offload.json, not in 04_EXPORT.
    cairn = tmp_path / ".cairn"
    cairn.mkdir()
    (cairn / "offload.json").write_text(json.dumps({"camera": "FX30"}))
    _write_sidecar(cairn, "aaa", classification="B-ROLL", source_path="/s/Footage/FX30/C0001.MP4")

    bins = timeline._classified_clips(tmp_path)

    assert bins["B-ROLL"] == ["/s/Footage/FX30/C0001.MP4"]


def test_classified_clips_skips_unclassified_sidecars(tmp_path: Path) -> None:
    cairn = tmp_path / "04_EXPORT" / ".cairn"
    _write_sidecar(
        cairn, "aaa", classification="A-ROLL", source_path="/foo/c01.mp4", stages=["ingest"]
    )
    bins = timeline._classified_clips(tmp_path)
    assert bins == {"A-ROLL": [], "B-ROLL": [], "AMBIGUOUS": []}


def test_classified_clips_handles_missing_dir(tmp_path: Path) -> None:
    bins = timeline._classified_clips(tmp_path)
    assert bins == {"A-ROLL": [], "B-ROLL": [], "AMBIGUOUS": []}


def test_classified_clips_skips_unparseable_sidecar(tmp_path: Path) -> None:
    cairn = tmp_path / "04_EXPORT" / ".cairn"
    cairn.mkdir(parents=True)
    (cairn / "broken.json").write_text("{ this is not json")
    _write_sidecar(cairn, "aaa", classification="A-ROLL", source_path="/foo/x.mp4")
    bins = timeline._classified_clips(tmp_path)
    assert bins["A-ROLL"] == ["/foo/x.mp4"]


# ── _parse_resolution ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("3840x2160", (3840, 2160)),
        ("1920x1080", (1920, 1080)),
        ({"width": 1920, "height": 1080}, (1920, 1080)),
        ("invalid", None),
        ("3840", None),  # no 'x' separator
        (None, None),
        (1080, None),
        ({}, None),
        ({"width": "abc", "height": "def"}, None),
    ],
)
def test_parse_resolution(value: Any, expected: tuple[int, int] | None) -> None:
    assert timeline._parse_resolution(value) == expected


# ── _modal_settings ───────────────────────────────────────────────────────────


class _FakeClip:
    def __init__(self, fps: str | None = "23.976", resolution: str | None = "3840x2160") -> None:
        self._props = {"FPS": fps, "Resolution": resolution}

    def GetClipProperty(self, key: str) -> Any:  # noqa: N802 — Resolve API name
        return self._props.get(key)


class _FakeMediaPool:
    def __init__(self, mapping: dict[str, _FakeClip]) -> None:
        self._mapping = mapping

    # _modal_settings calls resolve_api.get_clip_by_path which expects a
    # MediaPool object — but in the test we monkeypatch get_clip_by_path
    # to read from this mapping directly.


def test_modal_settings_picks_most_common(monkeypatch: pytest.MonkeyPatch) -> None:
    clips = {
        "/a.mp4": _FakeClip(fps="23.976", resolution="3840x2160"),
        "/b.mp4": _FakeClip(fps="23.976", resolution="3840x2160"),
        "/c.mp4": _FakeClip(fps="25.0", resolution="1920x1080"),
    }
    monkeypatch.setattr("timeline.resolve_api.get_clip_by_path", lambda _mp, p: clips.get(p))
    fps, res = timeline._modal_settings(_FakeMediaPool(clips), list(clips.keys()))
    assert fps == 23.976
    assert res == (3840, 2160)


def test_modal_settings_falls_back_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("timeline.resolve_api.get_clip_by_path", lambda _mp, _p: None)
    fps, res = timeline._modal_settings(_FakeMediaPool({}), [])
    assert fps == 24.0
    assert res == (1920, 1080)


def test_modal_settings_skips_unreadable_props(monkeypatch: pytest.MonkeyPatch) -> None:
    clips = {
        "/a.mp4": _FakeClip(fps=None, resolution=None),
        "/b.mp4": _FakeClip(fps="invalid", resolution="bad"),
        "/c.mp4": _FakeClip(fps="29.97", resolution="1920x1080"),
    }
    monkeypatch.setattr("timeline.resolve_api.get_clip_by_path", lambda _mp, p: clips.get(p))
    fps, res = timeline._modal_settings(_FakeMediaPool(clips), list(clips.keys()))
    assert fps == 29.97
    assert res == (1920, 1080)


# ── _unique_timeline_name ─────────────────────────────────────────────────────


class _FakeTimeline:
    def __init__(self, name: str) -> None:
        self._name = name

    def GetName(self) -> str:  # noqa: N802 — Resolve API name
        return self._name


class _FakeProject:
    def __init__(self, names: list[str]) -> None:
        self._timelines = [_FakeTimeline(n) for n in names]

    def GetTimelineCount(self) -> int:  # noqa: N802
        return len(self._timelines)

    def GetTimelineByIndex(self, idx: int) -> _FakeTimeline:  # noqa: N802
        # Resolve uses 1-based indexing
        return self._timelines[idx - 1]


def test_unique_timeline_name_returns_base_when_free() -> None:
    project = _FakeProject(["Other Timeline"])
    assert (
        timeline._unique_timeline_name(project, "shoot — Cairn Assembly")
        == "shoot — Cairn Assembly"
    )


def test_unique_timeline_name_suffixes_when_taken() -> None:
    project = _FakeProject(["shoot — Cairn Assembly"])
    assert (
        timeline._unique_timeline_name(project, "shoot — Cairn Assembly")
        == "shoot — Cairn Assembly (2)"
    )


def test_unique_timeline_name_walks_to_next_free_suffix() -> None:
    project = _FakeProject(
        ["shoot — Cairn Assembly", "shoot — Cairn Assembly (2)", "shoot — Cairn Assembly (3)"]
    )
    assert (
        timeline._unique_timeline_name(project, "shoot — Cairn Assembly")
        == "shoot — Cairn Assembly (4)"
    )


# ── build_timeline error paths ────────────────────────────────────────────────


def test_build_timeline_errors_on_missing_folder(tmp_path: Path) -> None:
    result = timeline.build_timeline(tmp_path / "does_not_exist")
    assert result["ok"] is False
    assert result["error"] is not None
    assert "does not exist" in result["error"]


def test_build_timeline_errors_when_no_classified_clips(tmp_path: Path) -> None:
    # Folder exists but no sidecars — should fail before touching Resolve
    result = timeline.build_timeline(tmp_path)
    assert result["ok"] is False
    assert result["a_count"] == 0
    assert result["b_count"] == 0
    assert "Run Classify first" in (result["error"] or "")
