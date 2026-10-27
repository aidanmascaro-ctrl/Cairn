"""
Tests for src/workers/selects_timeline.py — planning (pure) and the Resolve
build driven against an in-memory fake of the Scripting API.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest
import resolve_api
import selects
import selects_timeline as st


def seg(start: float, end: float, score: float = 0.9, colour: str = "Green") -> dict[str, Any]:
    return {
        "start_sec": start,
        "end_sec": end,
        "duration_sec": end - start,
        "mean_score": score,
        "peak_score": score,
        "group_scores": {"focus": 0.9, "exposure": 1.0, "motion": None},
        "flags": [],
        "camera": {"class": "static"},
        "forced": False,
        "marker_color": colour,
    }


def clip(
    name: str, duration: float, segments: list[dict[str, Any]], fps: float = 25.0
) -> dict[str, Any]:
    return {
        "file": f"/f/{name}",
        "name": name,
        "analysed": True,
        "duration_sec": duration,
        "fps": fps,
        "segments": segments,
    }


# ── Planning ─────────────────────────────────────────────────────────────────


class TestPlan:
    def test_context_layout_tiles_each_clip_exactly(self) -> None:
        clips = [clip("a.mp4", 10.0, [seg(2.0, 4.0), seg(6.0, 8.0)]), clip("b.mp4", 4.0, [])]
        pieces = st.plan_timeline(clips, 25.0, "context")
        a = [p for p in pieces if p.file == "/f/a.mp4"]
        assert [(p.track, p.src_in, p.src_out) for p in a] == [
            (1, 0, 50),
            (2, 50, 100),
            (1, 100, 150),
            (2, 150, 200),
            (1, 200, 250),
        ]
        assert [p.record for p in a] == [0, 50, 100, 150, 200]
        b = [p for p in pieces if p.file == "/f/b.mp4"]
        assert [(p.track, p.record, p.tl_frames) for p in b] == [(1, 250, 100)]

    def test_select_touching_clip_edges_leaves_no_empty_pieces(self) -> None:
        pieces = st.plan_timeline([clip("a.mp4", 4.0, [seg(0.0, 4.0)])], 25.0, "context")
        assert [(p.track, p.src_in, p.src_out) for p in pieces] == [(2, 0, 100)]

    def test_overlapping_selects_are_clamped(self) -> None:
        pieces = st.plan_timeline(
            [clip("a.mp4", 10.0, [seg(1.0, 5.0), seg(4.0, 6.0)])], 25.0, "context"
        )
        selects_only = [p for p in pieces if p.is_select]
        assert [(p.src_in, p.src_out) for p in selects_only] == [(25, 125), (125, 150)]

    def test_fps_conversion_keeps_pieces_abutting(self) -> None:
        clips = [clip("a.mp4", 10.0, [seg(3.3, 7.1)], fps=29.97)]
        pieces = st.plan_timeline(clips, 25.0, "context")
        for prev, nxt in itertools.pairwise(pieces):
            assert prev.record + prev.tl_frames == nxt.record

    def test_selects_only_packs_in_order(self) -> None:
        clips = [
            clip("a.mp4", 10.0, [seg(0.0, 2.0, 0.81), seg(5.0, 9.0, 0.95)]),
            clip("b.mp4", 10.0, [seg(1.0, 2.0, 0.9)]),
        ]
        by_score = st.plan_timeline(clips, 25.0, "selects_only", "score_desc")
        assert [p.segment["mean_score"] for p in by_score] == [0.95, 0.9, 0.81]
        assert [p.record for p in by_score] == [0, 100, 125]
        assert all(p.track == 1 for p in by_score)
        by_length = st.plan_timeline(clips, 25.0, "selects_only", "duration_asc")
        assert [p.segment["duration_sec"] for p in by_length] == [1.0, 2.0, 4.0]

    def test_unanalysed_clips_are_left_out(self) -> None:
        clips = [clip("a.mp4", 4.0, []), {"file": "/f/x.mp4", "analysed": False, "segments": []}]
        assert {p.file for p in st.plan_timeline(clips, 25.0)} == {"/f/a.mp4"}

    def test_bad_arguments_raise(self) -> None:
        with pytest.raises(ValueError):
            st.plan_timeline([], 25.0, "sideways")
        with pytest.raises(ValueError):
            st.plan_timeline([], 25.0, "context", "random")
        with pytest.raises(ValueError):
            st.plan_timeline([], 0.0)

    def test_marker_note(self) -> None:
        name, note = st.marker_note(seg(0, 2, 0.87), "C0340.MP4")
        assert name == "Select 0.87 · C0340"
        assert "focus 0.90" in note and "motion" not in note
        assert "camera static" in note


# ── Fake Resolve ─────────────────────────────────────────────────────────────


class FakeItem:
    def __init__(self, mpi: FakeMPI, kind: str, track: int, start: int, duration: int) -> None:
        self.mpi, self.kind, self.track, self.start, self.duration = (
            mpi,
            kind,
            track,
            start,
            duration,
        )
        self.linked_to: list[FakeItem] = []

    def GetMediaPoolItem(self) -> FakeMPI:  # noqa: N802
        return self.mpi

    def GetName(self) -> str:  # noqa: N802
        return self.mpi.name

    def GetStart(self) -> int:  # noqa: N802
        return self.start

    def GetDuration(self) -> int:  # noqa: N802
        return self.duration

    def GetTrackTypeAndIndex(self) -> list[Any]:  # noqa: N802
        return [self.kind, self.track]

    def GetLinkedItems(self) -> list[FakeItem]:  # noqa: N802
        return [i for i in self.linked_to if i is not self]


class Markable:
    def __init__(self) -> None:
        self.markers: dict[int, dict[str, Any]] = {}

    def GetMarkers(self) -> dict[int, dict[str, Any]]:  # noqa: N802
        return dict(self.markers)

    def DeleteMarkerAtFrame(self, frame: int) -> bool:  # noqa: N802
        return self.markers.pop(frame, None) is not None

    def AddMarker(  # noqa: N802
        self, frame: int, color: str, name: str, note: str, duration: int, custom: str = ""
    ) -> bool:
        if frame in self.markers:
            return False
        self.markers[frame] = {
            "color": color,
            "name": name,
            "note": note,
            "duration": duration,
            "customData": custom,
        }
        return True


class FakeMPI(Markable):
    def __init__(self, path: str, fps: str = "25.0", res: str = "3840x2160") -> None:
        super().__init__()
        self.path, self.fps, self.res, self.name = path, fps, res, Path(path).name

    def GetClipProperty(self, key: str) -> str:  # noqa: N802
        return {"File Path": self.path, "FPS": self.fps, "Resolution": self.res}[key]

    def GetName(self) -> str:  # noqa: N802
        return self.name


class FakeTimeline(Markable):
    def __init__(self, name: str, project: FakeProject) -> None:
        super().__init__()
        self.name, self.project = name, project
        self.settings: dict[str, str] = {}
        self.tracks = {"video": 1, "audio": 1}
        self.items: list[FakeItem] = []
        self.linked: list[list[FakeItem]] = []

    def GetName(self) -> str:  # noqa: N802
        return self.name

    def GetSetting(self, key: str) -> str:  # noqa: N802
        if self.settings.get("useCustomSettings") == "1" and key in self.settings:
            return self.settings[key]
        return self.project.settings.get(key, "")

    def SetSetting(self, key: str, value: str) -> bool:  # noqa: N802
        self.settings[key] = value
        return True

    def GetStartFrame(self) -> int:  # noqa: N802
        return 90000

    def AddTrack(self, kind: str, sub: str = "") -> bool:  # noqa: N802
        self.tracks[kind] += 1
        return True

    def GetTrackCount(self, kind: str) -> int:  # noqa: N802
        return self.tracks[kind]

    def GetItemListInTrack(self, kind: str, index: int) -> list[FakeItem]:  # noqa: N802
        return [i for i in self.items if i.kind == kind and i.track == index]

    def SetClipsLinked(self, items: list[FakeItem], linked: bool) -> bool:  # noqa: N802
        if all(i.linked_to for i in items):
            return False  # Resolve refuses to re-link an already linked group
        self.linked.append(items)
        for i in items:
            i.linked_to = list(items)
        return True


class FakeFolder:
    def __init__(self, clips: list[FakeMPI]) -> None:
        self.clips = clips

    def GetClipList(self) -> list[FakeMPI]:  # noqa: N802
        return self.clips

    def GetSubFolderList(self) -> list[FakeFolder]:  # noqa: N802
        return []


class FakeMediaPool:
    def __init__(self, project: FakeProject, clips: list[FakeMPI]) -> None:
        self.project, self.root = project, FakeFolder(clips)
        self.appends: list[list[dict[str, Any]]] = []
        self.auto_link = False  # Resolve 21 links video+audio of a clipInfo append itself
        self.drop_last = False  # simulate Resolve placing fewer pieces than asked
        self.explode = False  # simulate a Resolve call blowing up mid-build

    def GetRootFolder(self) -> FakeFolder:  # noqa: N802
        return self.root

    def CreateEmptyTimeline(self, name: str) -> FakeTimeline:  # noqa: N802
        tl = FakeTimeline(name, self.project)
        self.project.timelines.append(tl)
        return tl

    def DeleteTimelines(self, timelines: list[FakeTimeline]) -> bool:  # noqa: N802
        self.project.timelines = [t for t in self.project.timelines if t not in timelines]
        return True

    def AppendToTimeline(self, infos: list[dict[str, Any]]) -> list[FakeItem]:  # noqa: N802
        self.appends.append(infos)
        if self.explode:
            raise RuntimeError("Resolve went away")
        if self.drop_last:
            infos = infos[:-1]
        tl = self.project.current or self.project.timelines[-1]
        out: list[FakeItem] = []
        for info in infos:
            dur = info["endFrame"] - info["startFrame"]
            rec = info["recordFrame"] - tl.GetStartFrame()
            pair: list[FakeItem] = []
            for kind in ("video", "audio"):
                item = FakeItem(info["mediaPoolItem"], kind, info["trackIndex"], rec, dur)
                tl.items.append(item)
                if kind == "video":
                    out.append(item)
                pair.append(item)
            if self.auto_link:
                for i in pair:
                    i.linked_to = list(pair)
        return out


class FakeProject:
    def __init__(self, clips: list[FakeMPI], res: tuple[int, int] = (3840, 2160)) -> None:
        self.settings = {
            "timelineResolutionWidth": str(res[0]),
            "timelineResolutionHeight": str(res[1]),
            "timelineFrameRate": "25",
        }
        self.timelines: list[FakeTimeline] = []
        self.current: FakeTimeline | None = None
        self.refuse_switch = False
        self.mp = FakeMediaPool(self, clips)

    def GetMediaPool(self) -> FakeMediaPool:  # noqa: N802
        return self.mp

    def GetTimelineCount(self) -> int:  # noqa: N802
        return len(self.timelines)

    def GetTimelineByIndex(self, i: int) -> FakeTimeline:  # noqa: N802
        return self.timelines[i - 1]

    def GetSetting(self, key: str) -> str:  # noqa: N802
        return self.settings.get(key, "")

    def SetSetting(self, key: str, value: str) -> bool:  # noqa: N802
        self.settings[key] = value
        return True

    def SetCurrentTimeline(self, tl: FakeTimeline) -> bool:  # noqa: N802
        if self.refuse_switch:
            return False
        self.current = tl
        return True

    def GetCurrentTimeline(self) -> FakeTimeline | None:  # noqa: N802
        return self.current


class FakeResolve:
    def __init__(self, project: FakeProject) -> None:
        self.project = project

    def GetProjectManager(self) -> FakeResolve:  # noqa: N802
        return self

    def GetCurrentProject(self) -> FakeProject:  # noqa: N802
        return self.project


@pytest.fixture
def scored(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    clips = [
        clip("A.MP4", 10.0, [{**seg(2.0, 4.0, 0.95)}, {**seg(6.0, 8.0, 0.85)}]),
        clip("B.MP4", 6.0, [{**seg(1.0, 3.0, 0.9)}]),
        clip("C.MP4", 4.0, []),
    ]
    for c in clips:
        c["segments"] = [{k: v for k, v in s.items() if k != "marker_color"} for s in c["segments"]]
    monkeypatch.setattr(
        selects,
        "rescore",
        lambda folder, settings=None, persist=True: {"clips": clips, "summary": {}, "settings": {}},
    )
    return clips


def _install(monkeypatch: pytest.MonkeyPatch, project: FakeProject) -> None:
    monkeypatch.setattr(resolve_api, "get_resolve", lambda: FakeResolve(project))


class TestBuild:
    def test_context_build_places_links_and_marks(
        self, monkeypatch: pytest.MonkeyPatch, scored: Any, tmp_path: Path
    ) -> None:
        pool = [FakeMPI(c["file"]) for c in scored]
        project = FakeProject(pool)
        _install(monkeypatch, project)

        out = st.build_selects_timeline(tmp_path / "Shoot", name="Test Selects")

        assert out["ok"] is True, out
        assert out["name"] == "Test Selects"
        assert out["select_count"] == 3
        tl = project.timelines[0]
        assert out["tracks"] == {"V1": 6, "V2": 3, "A1": 6, "A2": 3}
        assert out["warnings"] == []
        # every append is anchored at the timeline's start frame
        assert min(i["recordFrame"] for batch in project.mp.appends for i in batch) == 90000
        # each piece's video is linked to its own audio
        assert out["linked_count"] == 9
        assert all({i.kind for i in group} == {"video", "audio"} for group in tl.linked)
        # timeline markers: one per select, banded by score
        colours = {m["name"]: m["color"] for m in tl.markers.values()}
        assert colours == {
            "Select 0.95 · A": "Green",
            "Select 0.90 · B": "Yellow",
            "Select 0.85 · A": "Red",
        }
        assert all(m["customData"] == st.MARKER_TAG for m in tl.markers.values())
        # clip markers at source frames
        assert sorted(pool[0].markers) == [50, 150]
        assert out["clip_markers_added"] == 3
        # matching project resolution: timeline keeps inheriting project settings
        assert "useCustomSettings" not in tl.settings

    def test_already_linked_appends_are_counted_not_relinked(
        self, monkeypatch: pytest.MonkeyPatch, scored: Any, tmp_path: Path
    ) -> None:
        project = FakeProject([FakeMPI(c["file"]) for c in scored])
        project.mp.auto_link = True
        _install(monkeypatch, project)
        out = st.build_selects_timeline(tmp_path / "Shoot")
        assert out["linked_count"] == 9
        assert project.timelines[0].linked == []  # nothing needed re-linking

    def test_rebuild_replaces_clip_markers_instead_of_stacking(
        self, monkeypatch: pytest.MonkeyPatch, scored: Any, tmp_path: Path
    ) -> None:
        pool = [FakeMPI(c["file"]) for c in scored]
        project = FakeProject(pool)
        _install(monkeypatch, project)
        pool[0].AddMarker(5, "Blue", "user marker", "", 1, "")
        st.build_selects_timeline(tmp_path / "Shoot")
        second = st.build_selects_timeline(tmp_path / "Shoot")
        assert second["name"] == "Shoot — Cairn Selects (2)"
        assert sorted(pool[0].markers) == [5, 50, 150]

    def test_vertical_footage_gets_custom_resolution_and_colour_standard(
        self, monkeypatch: pytest.MonkeyPatch, scored: Any, tmp_path: Path
    ) -> None:
        pool = [FakeMPI(c["file"], res="2160x3840") for c in scored]
        project = FakeProject(pool, res=(3840, 2160))
        _install(monkeypatch, project)
        out = st.build_selects_timeline(tmp_path / "Shoot")
        tl = project.timelines[0]
        assert out["resolution"] == "2160x3840"
        assert tl.settings["useCustomSettings"] == "1"
        for key, value in st.COLOUR_STANDARD.items():
            assert tl.GetSetting(key) == value

    def test_selects_only_uses_one_track(
        self, monkeypatch: pytest.MonkeyPatch, scored: Any, tmp_path: Path
    ) -> None:
        project = FakeProject([FakeMPI(c["file"]) for c in scored])
        _install(monkeypatch, project)
        out = st.build_selects_timeline(
            tmp_path / "Shoot", layout="selects_only", order="score_desc"
        )
        assert out["tracks"] == {"V1": 3, "A1": 3}

    def test_missing_clips_are_reported_not_fatal(
        self, monkeypatch: pytest.MonkeyPatch, scored: Any, tmp_path: Path
    ) -> None:
        project = FakeProject([FakeMPI(scored[0]["file"])])
        _install(monkeypatch, project)
        out = st.build_selects_timeline(tmp_path / "Shoot")
        assert out["ok"] is True
        assert sorted(out["missing_paths"]) == ["/f/B.MP4", "/f/C.MP4"]

    def test_nothing_in_pool_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch, scored: Any, tmp_path: Path
    ) -> None:
        _install(monkeypatch, FakeProject([]))
        out = st.build_selects_timeline(tmp_path / "Shoot")
        assert out["ok"] is False
        assert "media pool" in out["error"]

    def test_no_selects_is_an_error_before_touching_resolve(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(selects, "rescore", lambda *a, **k: {"clips": [clip("A.MP4", 4.0, [])]})
        monkeypatch.setattr(resolve_api, "get_resolve", lambda: pytest.fail("Resolve touched"))
        out = st.build_selects_timeline(tmp_path)
        assert out["ok"] is False
        assert "No selects" in out["error"]

    def test_resolve_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, scored: Any, tmp_path: Path
    ) -> None:
        def boom() -> None:
            raise resolve_api.ResolveUnavailableError("Resolve is not running")

        monkeypatch.setattr(resolve_api, "get_resolve", boom)
        out = st.build_selects_timeline(tmp_path)
        assert out == {**out, "ok": False, "error": "Resolve is not running"}
        # lock released even on early return
        assert resolve_api._lock.acquire(blocking=False)
        resolve_api._release_lock()


class TestBuildFailures:
    def test_refused_timeline_switch_adds_nothing_and_cleans_up(
        self, monkeypatch: pytest.MonkeyPatch, scored: Any, tmp_path: Path
    ) -> None:
        project = FakeProject([FakeMPI(c["file"]) for c in scored])
        project.refuse_switch = True
        _install(monkeypatch, project)
        out = st.build_selects_timeline(tmp_path / "Shoot")
        assert out["ok"] is False
        assert "wouldn't switch" in out["error"]
        assert project.mp.appends == []  # never wrote into whatever timeline was open
        assert project.timelines == []  # half-built timeline removed

    def test_exception_mid_build_removes_the_timeline(
        self, monkeypatch: pytest.MonkeyPatch, scored: Any, tmp_path: Path
    ) -> None:
        project = FakeProject([FakeMPI(c["file"]) for c in scored])
        project.mp.explode = True
        _install(monkeypatch, project)
        out = st.build_selects_timeline(tmp_path / "Shoot")
        assert out["ok"] is False
        assert "Resolve went away" in out["error"]
        assert "removed" in out["error"]
        assert project.timelines == []

    def test_short_append_is_reported(
        self, monkeypatch: pytest.MonkeyPatch, scored: Any, tmp_path: Path
    ) -> None:
        project = FakeProject([FakeMPI(c["file"]) for c in scored])
        project.mp.drop_last = True
        _install(monkeypatch, project)
        out = st.build_selects_timeline(tmp_path / "Shoot")
        assert out["ok"] is True
        assert any("placed" in w for w in out["warnings"])

    def test_clip_that_lost_its_selects_loses_old_markers(
        self, monkeypatch: pytest.MonkeyPatch, scored: Any, tmp_path: Path
    ) -> None:
        pool = [FakeMPI(c["file"]) for c in scored]
        project = FakeProject(pool)
        _install(monkeypatch, project)
        pool[2].AddMarker(10, "Green", "old select", "", 5, st.MARKER_TAG)  # C has no selects now
        pool[2].AddMarker(20, "Blue", "editor's own", "", 1, "")
        st.build_selects_timeline(tmp_path / "Shoot")
        assert sorted(pool[2].markers) == [20]


def test_norm_path_follows_symlinks(tmp_path: Path) -> None:
    real = tmp_path / "real.mp4"
    real.write_bytes(b"")
    link = tmp_path / "link.mp4"
    link.symlink_to(real)
    assert st.norm_path(str(link)) == st.norm_path(str(real))
