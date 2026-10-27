"""
pytest tests for resolve_api.ingest_into_resolve — the single-worker Resolve
import path that main.js drives via bridge.py (resolve=True).

These tests fake the Resolve MediaPool object graph so they run with no DaVinci
Resolve present. They lock down the behaviour the plugin relies on: the Cairn
bin tree is built, clips are routed to the correct bins, re-ingest dedupes, and
a missing project raises ResolveUnavailableError instead of importing blind.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import resolve_api

# ── Fakes for the Resolve MediaPool object graph ─────────────────────────────


# Field names real Resolve (Studio 21.1) accepts in SetMetadata — a sample of the
# Metadata panel. It refuses anything else ("Camera", "Shoot Date", "Cairn …"),
# by returning False, so the fake does too.
RESOLVE_FIELDS = {
    "Camera Type",
    "Date Recorded",
    "Lens Type",
    "Keywords",
    "Comments",
    "Description",
    "Shoot Day",
}


class FakeClip:
    """Stands in for a Resolve MediaPoolItem."""

    def __init__(self, file_path: str) -> None:
        self._file_path = file_path
        self.metadata: dict[str, str] = {}
        self.third_party: dict[str, str] = {}

    def GetClipProperty(self, key: str) -> str:  # noqa: N802 (Resolve API name)
        return self._file_path if key == "File Path" else ""

    def SetMetadata(self, key: str, value: str) -> bool:  # noqa: N802
        if key not in RESOLVE_FIELDS:
            return False
        self.metadata[key] = value
        return True

    def SetThirdPartyMetadata(self, key: str, value: str) -> bool:  # noqa: N802
        self.third_party[key] = value
        return True

    def GetMetadata(self, key: str) -> str:  # noqa: N802
        return self.metadata.get(key, "")

    def GetThirdPartyMetadata(self, key: str) -> str:  # noqa: N802
        return self.third_party.get(key, "")


class FakeFolder:
    """Stands in for a Resolve MediaPoolFolder (a bin)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.subs: list[FakeFolder] = []
        self.clips: list[FakeClip] = []

    def GetName(self) -> str:  # noqa: N802
        return self.name

    def GetSubFolderList(self) -> list[FakeFolder]:  # noqa: N802
        return list(self.subs)

    def GetClipList(self) -> list[FakeClip]:  # noqa: N802
        return list(self.clips)

    def child(self, name: str) -> FakeFolder:
        """Test helper: return the sub-bin named `name` (KeyError-ish if absent)."""
        for sub in self.subs:
            if sub.name == name:
                return sub
        raise AssertionError(f"bin {name!r} not found under {self.name!r}")


class FakeMediaPool:
    """Records AddSubFolder / SetCurrentFolder / ImportMedia like the real API."""

    def __init__(self) -> None:
        self.root = FakeFolder("Master")
        self.current: FakeFolder | None = None
        self.import_calls: list[tuple[str, list[str]]] = []

    def GetRootFolder(self) -> FakeFolder:  # noqa: N802
        return self.root

    def AddSubFolder(self, parent: FakeFolder, name: str) -> FakeFolder:  # noqa: N802
        created = FakeFolder(name)
        parent.subs.append(created)
        return created

    def SetCurrentFolder(self, folder: FakeFolder) -> bool:  # noqa: N802
        self.current = folder
        return True

    def ImportMedia(self, paths: list[str]) -> list[FakeClip]:  # noqa: N802
        assert self.current is not None, "ImportMedia called before SetCurrentFolder"
        clips = [FakeClip(p) for p in paths]
        self.current.clips.extend(clips)
        self.import_calls.append((self.current.name, list(paths)))
        return clips


class _FakeProject:
    def __init__(self, mp: FakeMediaPool | None) -> None:
        self._mp = mp

    def GetMediaPool(self) -> FakeMediaPool | None:  # noqa: N802
        return self._mp


class _FakeProjectManager:
    def __init__(self, project: _FakeProject | None) -> None:
        self._project = project

    def GetCurrentProject(self) -> _FakeProject | None:  # noqa: N802
        return self._project


class _FakeResolve:
    def __init__(self, project: _FakeProject | None) -> None:
        self._pm = _FakeProjectManager(project)

    def GetProjectManager(self) -> _FakeProjectManager:  # noqa: N802
        return self._pm


@pytest.fixture
def fake_resolve(monkeypatch: pytest.MonkeyPatch) -> FakeMediaPool:
    """Patch resolve_api.get_resolve to return a fake with an open project."""
    mp = FakeMediaPool()
    monkeypatch.setattr(resolve_api, "get_resolve", lambda: _FakeResolve(_FakeProject(mp)))
    return mp


# ── Tests ────────────────────────────────────────────────────────────────────

_META = {
    "camera": "ILCE-7M4",
    "lens": "FE 24-70",
    "timecode_start": "01:00:00:00",
    "shoot_date": "240101",
}


def test_builds_cairn_bin_tree_and_routes_clips(
    fake_resolve: FakeMediaPool, tmp_path: Path
) -> None:
    clip_paths: dict[str, list[str]] = {
        "a_cam": ["/m/A001.mp4", "/m/A002.mp4"],
        "c_cam": ["/m/DJI_0001.mp4"],
        "photos": ["/m/DSC1.dng"],
        "assets": [],
        "thumbnails": [],
    }

    counts = resolve_api.ingest_into_resolve(tmp_path, clip_paths, _META).counts

    # Bin tree: Master / Cairn / 01_VIDEOS / {A_CAM,B_CAM,C_CAM}; Cairn/02_PHOTOS;
    # Cairn/03_ASSETS/Thumbnails.
    cairn = fake_resolve.root.child("Cairn")
    videos = cairn.child("01_VIDEOS")
    assert {s.name for s in videos.subs} == {"A_CAM", "B_CAM", "C_CAM"}
    cairn.child("02_PHOTOS")
    cairn.child("03_ASSETS").child("Thumbnails")

    # Clips routed to the right bins.
    assert {c.GetClipProperty("File Path") for c in videos.child("A_CAM").clips} == {
        "/m/A001.mp4",
        "/m/A002.mp4",
    }
    assert {c.GetClipProperty("File Path") for c in videos.child("C_CAM").clips} == {
        "/m/DJI_0001.mp4"
    }

    assert counts["a_cam"] == 2
    assert counts["c_cam"] == 1
    assert counts["photos"] == 1
    assert counts["total"] == 4


def test_writes_clip_metadata_to_fields_resolve_accepts(
    fake_resolve: FakeMediaPool, tmp_path: Path
) -> None:
    result = resolve_api.ingest_into_resolve(tmp_path, {"a_cam": ["/m/A001.mp4"]}, _META)

    a_cam = fake_resolve.root.child("Cairn").child("01_VIDEOS").child("A_CAM")
    (clip,) = a_cam.clips
    assert clip.metadata == {
        "Camera Type": "ILCE-7M4",
        "Lens Type": "FE 24-70",
        "Date Recorded": "240101",
    }
    assert clip.third_party == {"Cairn Stage": "ingested"}
    assert result.refused_fields == []


def test_offload_bins_get_their_camera_folder_name(
    fake_resolve: FakeMediaPool, tmp_path: Path
) -> None:
    camera_clips = {"FX30": ["/m/C0001.MP4"], "vid": ["/m/clip.mov"]}
    result = resolve_api.ingest_offload_into_resolve(
        tmp_path, camera_clips, [], {"camera": "ILME-FX30", "shoot_date": "261015"}
    )

    footage = fake_resolve.root.child("Cairn").child("Footage")
    (fx30,) = footage.child("FX30").clips
    (vid,) = footage.child("vid").clips
    assert fx30.metadata == {"Camera Type": "FX30", "Date Recorded": "261015"}
    assert vid.metadata["Camera Type"] == "vid"
    assert fx30.third_party == {"Cairn Stage": "ingested"}
    assert result.counts == {"FX30": 1, "vid": 1, "total": 2}
    assert result.refused_fields == []


def test_reingest_fills_blank_metadata_on_clips_already_imported(
    fake_resolve: FakeMediaPool, tmp_path: Path
) -> None:
    # Imported before metadata worked: in the bin, but blank.
    bin_folder = fake_resolve.root
    for name in ("Cairn", "Footage", "FX30"):
        bin_folder = fake_resolve.AddSubFolder(bin_folder, name)
    old = FakeClip("/m/C0001.MP4")
    edited = FakeClip("/m/C0002.MP4")
    edited.metadata["Camera Type"] = "B-cam"  # the editor's own
    edited.third_party["Cairn Stage"] = "transcribed"  # a later stage
    bin_folder.clips.extend([old, edited])

    result = resolve_api.ingest_offload_into_resolve(
        tmp_path, {"FX30": ["/m/C0001.MP4", "/m/C0002.MP4"]}, [], {"shoot_date": "261015"}
    )

    assert result.counts["FX30"] == 0  # nothing re-imported
    assert len(bin_folder.clips) == 2
    assert old.metadata == {"Camera Type": "FX30", "Date Recorded": "261015"}
    assert old.third_party == {"Cairn Stage": "ingested"}
    assert edited.metadata == {
        "Camera Type": "B-cam",
        "Date Recorded": "261015",
    }  # blank filled, edit kept
    assert edited.third_party == {"Cairn Stage": "transcribed"}  # not reset


def test_set_clip_metadata_reports_refused_fields() -> None:
    clip = FakeClip("/m/A001.mp4")

    refused = resolve_api.set_clip_metadata(
        clip, {"Camera": "FX3", "Camera Type": "FX3", "Lens Type": "", "Cairn Stage": "x"}
    )

    assert refused == ["Camera"]  # not a Resolve field
    assert clip.metadata == {"Camera Type": "FX3"}  # empty values are skipped, not written
    assert clip.third_party == {"Cairn Stage": "x"}


def test_bridge_reports_refused_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import bridge

    done = {"event": "complete", "folder": str(tmp_path), "clip_paths": {}, "metadata": {}}
    monkeypatch.setattr(bridge.ingest_worker, "run", lambda *_, **__: iter([done]))
    monkeypatch.setattr(
        bridge.resolve_api,
        "ingest_into_resolve",
        lambda **_: resolve_api.ResolveImport({"total": 0}, ["Camera"]),
    )

    events = list(
        bridge.handle_ingest(
            {"id": "1", "payload": {"path": str(tmp_path), "write_sidecar": False}}
        )
    )

    refused = [e for e in events if e.get("event") == "metadata_refused"]
    assert refused and refused[0]["fields"] == ["Camera"]
    assert events[-1]["type"] == "ingest_complete"
    assert events[-1]["metadata_refused"] == ["Camera"]


def test_reingest_dedupes(fake_resolve: FakeMediaPool, tmp_path: Path) -> None:
    clip_paths = {"a_cam": ["/m/A001.mp4", "/m/A002.mp4"]}

    first = resolve_api.ingest_into_resolve(tmp_path, clip_paths, _META).counts
    second = resolve_api.ingest_into_resolve(tmp_path, clip_paths, _META).counts

    assert first["a_cam"] == 2
    assert second["a_cam"] == 0  # already imported — not duplicated
    a_cam = fake_resolve.root.child("Cairn").child("01_VIDEOS").child("A_CAM")
    assert len(a_cam.clips) == 2


def test_jpeg_stills_imported_individually(fake_resolve: FakeMediaPool, tmp_path: Path) -> None:
    # Sequential JPEG filenames must each be a discrete ImportMedia call so
    # Resolve does not collapse them into one image-sequence clip.
    photos = ["/m/DSC00001.jpg", "/m/DSC00002.jpg", "/m/DSC00003.jpg"]
    counts = resolve_api.ingest_into_resolve(tmp_path, {"photos": photos}, _META).counts

    assert counts["photos"] == 3
    photo_import_calls = [
        paths for (binname, paths) in fake_resolve.import_calls if binname == "02_PHOTOS"
    ]
    assert all(len(paths) == 1 for paths in photo_import_calls)
    assert len(photo_import_calls) == 3


def test_raises_when_no_project_open(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(resolve_api, "get_resolve", lambda: _FakeResolve(None))

    with pytest.raises(resolve_api.ResolveUnavailableError):
        resolve_api.ingest_into_resolve(tmp_path, {"a_cam": ["/m/A001.mp4"]}, _META)


def test_bridge_keeps_ingest_result_when_resolve_import_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import bridge

    done = {
        "event": "complete",
        "folder": str(tmp_path),
        "clip_paths": {},
        "metadata": {},
        "files_moved": 3,
    }
    monkeypatch.setattr(bridge.ingest_worker, "run", lambda *_, **__: iter([done]))

    def boom(**_: object) -> None:
        raise RuntimeError("SetCurrentFolder failed")

    monkeypatch.setattr(bridge.resolve_api, "ingest_into_resolve", boom)

    events = list(
        bridge.handle_ingest(
            {"id": "1", "payload": {"path": str(tmp_path), "write_sidecar": False}}
        )
    )

    skipped = [e for e in events if e.get("event") == "resolve_skipped"]
    assert skipped and "SetCurrentFolder failed" in skipped[0]["reason"]
    assert events[-1]["type"] == "ingest_complete"  # the organise work isn't thrown away
    assert events[-1]["files_moved"] == 3
    assert events[-1]["resolve_counts"] == {}
