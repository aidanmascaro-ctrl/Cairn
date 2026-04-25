"""
pytest tests for Stage 1 — ingest.py and sidecar.py.

All tests use tmp_path; no side-effects outside tmp.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import ingest
import pytest
import sidecar

# ── Fixtures / helpers ────────────────────────────────────────────────────────


def mediapro_xml(date: str, camera: str) -> str:
    """Minimal MEDIAPRO.XML without namespace (matches what ET.iter parses)."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<MediaProData>
  <Device modelName="{camera}" />
  <Contents ClipCount="1" CreationDate="{date}T12:00:00+00:00" />
</MediaProData>
"""


def clip_xml(lens_make: str, lens_model: str, timecode: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<NonRealTimeMeta>
  <LtcChangeTable>
    <Ltc value="{timecode}" />
  </LtcChangeTable>
  <LensUnitMetadata make="{lens_make}" model="{lens_model}" />
</NonRealTimeMeta>
"""


def dated_folder(tmp_path: Path, name: str = "shoot") -> Path:
    """Return a folder with a 6-digit date prefix so ingest doesn't rename it."""
    folder = tmp_path / f"240101 {name}"
    folder.mkdir()
    return folder


# ── Mode detection ────────────────────────────────────────────────────────────


def test_is_sony_card_dcim(tmp_path: Path) -> None:
    (tmp_path / "DCIM").mkdir()
    assert ingest.is_sony_card(tmp_path) is True


def test_is_sony_card_m4root(tmp_path: Path) -> None:
    (tmp_path / "M4ROOT").mkdir()
    assert ingest.is_sony_card(tmp_path) is True


def test_is_sony_card_false(tmp_path: Path) -> None:
    assert ingest.is_sony_card(tmp_path) is False


def test_already_organised_01_videos(tmp_path: Path) -> None:
    (tmp_path / "01_VIDEOS").mkdir()
    assert ingest.already_organised(tmp_path) is True


def test_already_organised_02_photos(tmp_path: Path) -> None:
    (tmp_path / "02_PHOTOS").mkdir()
    assert ingest.already_organised(tmp_path) is True


def test_already_organised_false(tmp_path: Path) -> None:
    assert ingest.already_organised(tmp_path) is False


def test_has_loose_media_true(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"x")
    assert ingest.has_loose_media(tmp_path) is True


def test_has_loose_media_false(tmp_path: Path) -> None:
    (tmp_path / "readme.txt").write_text("hi")
    assert ingest.has_loose_media(tmp_path) is False


# ── XML metadata extraction ───────────────────────────────────────────────────


def test_extract_metadata_mediapro_date_and_camera(tmp_path: Path) -> None:
    (tmp_path / "MEDIAPRO.XML").write_text(mediapro_xml("2024-03-15", "ILCE-7SM3"))
    meta = ingest.extract_sony_metadata(tmp_path)
    assert meta["shoot_date"] == "240315"
    assert meta["camera"] == "ILCE-7SM3"


def test_extract_metadata_fallback_date_is_today(tmp_path: Path) -> None:
    meta = ingest.extract_sony_metadata(tmp_path)
    assert len(meta["shoot_date"]) == 6
    assert meta["shoot_date"].isdigit()


def test_extract_metadata_defaults_when_no_xml(tmp_path: Path) -> None:
    meta = ingest.extract_sony_metadata(tmp_path)
    assert meta["camera"] == ""
    assert meta["lens"] == ""
    assert meta["timecode_start"] == ""


def test_extract_metadata_per_clip_xml_lens_and_tc(tmp_path: Path) -> None:
    clip_dir = tmp_path / "M4ROOT" / "CLIP"
    clip_dir.mkdir(parents=True)
    (clip_dir / "C0001M01.XML").write_text(clip_xml("Sony", "FE 24mm", "01:00:00:00"))
    meta = ingest.extract_sony_metadata(tmp_path)
    assert meta["lens"] == "Sony FE 24mm"
    assert meta["timecode_start"] == "01:00:00:00"


def test_extract_metadata_malformed_xml_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "MEDIAPRO.XML").write_text("<broken>not closed")
    meta = ingest.extract_sony_metadata(tmp_path)
    assert isinstance(meta, dict)
    assert "shoot_date" in meta


# ── File collection ───────────────────────────────────────────────────────────


def test_collect_files_video_to_videos_bucket(tmp_path: Path) -> None:
    (tmp_path / "clip.mxf").write_bytes(b"x")
    buckets = ingest.collect_files(tmp_path)
    assert any(p.name == "clip.mxf" for p in buckets["videos"])


def test_collect_files_photo_to_photos_bucket(tmp_path: Path) -> None:
    (tmp_path / "IMG_0001.ARW").write_bytes(b"x")
    buckets = ingest.collect_files(tmp_path)
    assert any(p.name == "IMG_0001.ARW" for p in buckets["photos"])


def test_collect_files_skips_already_organised(tmp_path: Path) -> None:
    (tmp_path / "01_VIDEOS").mkdir()
    (tmp_path / "01_VIDEOS" / "clip.mp4").write_bytes(b"x")
    buckets = ingest.collect_files(tmp_path)
    assert not buckets["videos"]


def test_collect_files_thumbnail_to_thumbnails_bucket(tmp_path: Path) -> None:
    thmbnl = tmp_path / "M4ROOT" / "THMBNL"
    thmbnl.mkdir(parents=True)
    (thmbnl / "C0001T01.JPG").write_bytes(b"x")
    buckets = ingest.collect_files(tmp_path)
    assert any(p.name == "C0001T01.JPG" for p in buckets["thumbnails"])


# ── move_files ────────────────────────────────────────────────────────────────


def test_move_files_dry_run_yields_event_without_moving(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    f = src_dir / "clip.mxf"
    f.write_bytes(b"x")
    dest = tmp_path / "dest"

    events = list(ingest.move_files([f], dest, dry_run=True))
    assert len(events) == 1
    assert events[0]["event"] == "file_moved"
    assert events[0]["dry_run"] is True
    assert not dest.exists()  # dest dir must not be created


def test_move_files_actually_moves(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    f = src_dir / "clip.mxf"
    f.write_bytes(b"x")
    dest = tmp_path / "dest"

    list(ingest.move_files([f], dest, dry_run=False))
    assert not f.exists()
    assert (dest / "clip.mxf").exists()


def test_move_files_collision_safe_rename(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "clip.mxf").write_bytes(b"existing")
    f = src_dir / "clip.mxf"
    f.write_bytes(b"new")

    list(ingest.move_files([f], dest, dry_run=False))
    assert (dest / "clip.mxf").read_bytes() == b"existing"
    assert (dest / "clip_1.mxf").exists()


def test_move_files_empty_list_yields_nothing(tmp_path: Path) -> None:
    events = list(ingest.move_files([], tmp_path / "dest", dry_run=False))
    assert events == []


# ── is_dji_clip ───────────────────────────────────────────────────────────────


def test_is_dji_clip_true() -> None:
    assert ingest.is_dji_clip("DJI_0001.MP4") is True
    assert ingest.is_dji_clip("dji_0001.mov") is True


def test_is_dji_clip_false() -> None:
    assert ingest.is_dji_clip("C0001.MXF") is False
    assert ingest.is_dji_clip("IMG_0001.ARW") is False


def test_collect_files_dji_to_dji_bucket(tmp_path: Path) -> None:
    (tmp_path / "DJI_0001.MP4").write_bytes(b"x")
    (tmp_path / "DJI_0002.MOV").write_bytes(b"x")
    buckets = ingest.collect_files(tmp_path)
    assert len(buckets["dji"]) == 2
    assert len(buckets["videos"]) == 0


def test_collect_files_photomator_no_ext_to_photos_bucket(tmp_path: Path) -> None:
    """Extension-less Photomator edit files must land in the photos bucket."""
    (tmp_path / "DSC00816").write_bytes(b"x")  # no extension
    buckets = ingest.collect_files(tmp_path)
    assert any(p.name == "DSC00816" for p in buckets["photos"])
    assert not any(p.name == "DSC00816" for p in buckets["assets"])


def test_collect_files_xmp_to_photos_bucket(tmp_path: Path) -> None:
    (tmp_path / "IMG_0001.ARW").write_bytes(b"x")
    (tmp_path / "IMG_0001.xmp").write_bytes(b"x")
    buckets = ingest.collect_files(tmp_path)
    assert any(p.name == "IMG_0001.xmp" for p in buckets["photos"])


def test_run_dji_clips_go_to_c_cam(tmp_path: Path) -> None:
    folder = dated_folder(tmp_path)
    (folder / "DJI_0001.MP4").write_bytes(b"x")
    (folder / "DJI_0002.MOV").write_bytes(b"x")

    events = list(ingest.run(str(folder), dry_run=False))
    complete = next(e for e in events if e.get("event") == "complete")
    assert len(complete["clip_paths"]["c_cam"]) == 2
    assert len(complete["clip_paths"]["a_cam"]) == 0
    assert (folder / "01_VIDEOS" / "C_CAM" / "DJI_0001.MP4").exists()
    assert (folder / "01_VIDEOS" / "C_CAM" / "DJI_0002.MOV").exists()


def test_run_non_dji_clips_not_in_c_cam(tmp_path: Path) -> None:
    folder = dated_folder(tmp_path)
    (folder / "C0001.MXF").write_bytes(b"x")

    events = list(ingest.run(str(folder), dry_run=False))
    complete = next(e for e in events if e.get("event") == "complete")
    assert len(complete["clip_paths"]["a_cam"]) == 1
    assert len(complete["clip_paths"]["c_cam"]) == 0


def test_run_xmp_companion_moves_with_photos(tmp_path: Path) -> None:
    folder = dated_folder(tmp_path)
    (folder / "IMG_0001.ARW").write_bytes(b"x")
    (folder / "IMG_0001.xmp").write_bytes(b"x")

    list(ingest.run(str(folder), dry_run=False))
    assert (folder / "02_PHOTOS" / "IMG_0001.ARW").exists()
    assert (folder / "02_PHOTOS" / "IMG_0001.xmp").exists()


# ── remove_empty_shells ───────────────────────────────────────────────────────


def test_remove_empty_shells_removes_empty_dirs(tmp_path: Path) -> None:
    (tmp_path / "DCIM").mkdir()
    (tmp_path / "M4ROOT").mkdir()
    count = ingest.remove_empty_shells(tmp_path, dry_run=False)
    assert count == 2
    assert not (tmp_path / "DCIM").exists()
    assert not (tmp_path / "M4ROOT").exists()


def test_remove_empty_shells_dry_run_counts_without_deleting(tmp_path: Path) -> None:
    (tmp_path / "CLIP").mkdir()
    count = ingest.remove_empty_shells(tmp_path, dry_run=True)
    assert count == 1
    assert (tmp_path / "CLIP").exists()


def test_remove_empty_shells_poisoned_dir_is_skipped(tmp_path: Path) -> None:
    """Shell dir containing a real file must not be removed."""
    shell = tmp_path / "PRIVATE"
    shell.mkdir()
    (shell / "leftover.txt").write_text("user file")
    count = ingest.remove_empty_shells(tmp_path, dry_run=False)
    assert count == 0
    assert shell.exists()


def test_remove_empty_shells_ds_store_not_a_blocker(tmp_path: Path) -> None:
    """A directory containing only .DS_Store should still be removed."""
    shell = tmp_path / "DCIM"
    shell.mkdir()
    (shell / ".DS_Store").write_bytes(b"\x00")
    count = ingest.remove_empty_shells(tmp_path, dry_run=False)
    assert count == 1
    assert not shell.exists()


def test_remove_empty_shells_nested_empty_dirs_removed(tmp_path: Path) -> None:
    """Parent is cleaned after its empty child is removed (bottom-up)."""
    dcim = tmp_path / "DCIM"
    inner = dcim / "100MSDCF"
    inner.mkdir(parents=True)
    count = ingest.remove_empty_shells(tmp_path, dry_run=False)
    assert count == 2
    assert not dcim.exists()


def test_remove_empty_shells_non_sony_user_folder_removed_when_empty(tmp_path: Path) -> None:
    """Any empty directory (e.g. drone/ after DJI files moved) is cleaned up."""
    (tmp_path / "drone").mkdir()
    count = ingest.remove_empty_shells(tmp_path, dry_run=False)
    assert count == 1


def test_remove_empty_shells_template_dirs_never_touched(tmp_path: Path) -> None:
    """Template directories we created must never be removed even if empty."""
    for rel in ingest.TEMPLATE_DIRS:
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    count = ingest.remove_empty_shells(tmp_path, dry_run=False)
    assert count == 0
    assert (tmp_path / "01_VIDEOS").exists()


# ── preflight ─────────────────────────────────────────────────────────────────


def test_preflight_raises_for_nonexistent_path(tmp_path: Path) -> None:
    with pytest.raises(ingest.IngestError, match="does not exist"):
        ingest.preflight(tmp_path / "ghost")


def test_preflight_raises_for_file(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(ingest.IngestError, match="not a directory"):
        ingest.preflight(f)


@pytest.mark.skipif(sys.platform != "darwin", reason="System path check uses macOS paths")
def test_preflight_raises_for_system_path() -> None:
    with pytest.raises(ingest.IngestError, match="system path"):
        ingest.preflight(Path("/System/Library"))


# ── Full pipeline ─────────────────────────────────────────────────────────────


def test_run_dry_run_returns_complete_event(tmp_path: Path) -> None:
    folder = dated_folder(tmp_path)
    (folder / "clip.mxf").write_bytes(b"x")

    events = list(ingest.run(str(folder), dry_run=True))
    complete = next(e for e in events if e.get("event") == "complete")
    assert complete["dry_run"] is True
    assert complete["files_moved"] == 1
    assert "metadata" in complete
    assert "clip_paths" in complete


def test_run_dry_run_does_not_move_files(tmp_path: Path) -> None:
    folder = dated_folder(tmp_path)
    (folder / "clip.mxf").write_bytes(b"x")

    list(ingest.run(str(folder), dry_run=True))
    assert (folder / "clip.mxf").exists()


def test_run_renames_folder_without_date_prefix(tmp_path: Path) -> None:
    folder = tmp_path / "beach_shoot"
    folder.mkdir()
    (folder / "clip.mxf").write_bytes(b"x")
    (folder / "MEDIAPRO.XML").write_text(mediapro_xml("2024-06-21", "FX3"))

    events = list(ingest.run(str(folder), dry_run=False))
    renamed = next((e for e in events if e.get("event") == "renamed"), None)
    assert renamed is not None
    assert renamed["to"].startswith("240621")


def test_run_name_override_applied(tmp_path: Path) -> None:
    folder = tmp_path / "beach_shoot"
    folder.mkdir()
    (folder / "clip.mxf").write_bytes(b"x")
    (folder / "MEDIAPRO.XML").write_text(mediapro_xml("2024-07-04", "ILCE-7SM3"))

    events = list(ingest.run(str(folder), name_override="wedding", dry_run=False))
    complete = next(e for e in events if e.get("event") == "complete")
    assert "wedding" in complete["folder"]


def test_run_creates_template_dirs(tmp_path: Path) -> None:
    folder = dated_folder(tmp_path)

    list(ingest.run(str(folder), dry_run=False))
    for rel in ingest.TEMPLATE_DIRS:
        assert (folder / rel).is_dir(), f"Missing template dir: {rel}"


def test_run_clip_paths_populated_for_video(tmp_path: Path) -> None:
    folder = dated_folder(tmp_path)
    (folder / "clip.mxf").write_bytes(b"x")

    events = list(ingest.run(str(folder), dry_run=False))
    complete = next(e for e in events if e.get("event") == "complete")
    assert len(complete["clip_paths"]["a_cam"]) == 1


def test_run_reclassifies_xmp_sidecars_from_assets_to_photos(tmp_path: Path) -> None:
    """XMP files previously moved to 03_ASSETS must be relocated to 02_PHOTOS on re-ingest."""
    folder = dated_folder(tmp_path)
    for rel in ingest.TEMPLATE_DIRS:
        (folder / rel).mkdir(parents=True, exist_ok=True)
    # Simulate old ingest putting XMP in 03_ASSETS
    (folder / "03_ASSETS" / "IMG_0001.xmp").write_bytes(b"x")
    (folder / "03_ASSETS" / "IMG_0002.XMP").write_bytes(b"x")
    (folder / "03_ASSETS" / "something.lut").write_bytes(b"x")  # should stay

    list(ingest.run(str(folder), dry_run=False))
    assert (folder / "02_PHOTOS" / "IMG_0001.xmp").exists()
    assert (folder / "02_PHOTOS" / "IMG_0002.XMP").exists()
    assert (folder / "03_ASSETS" / "something.lut").exists()  # non-XMP unchanged


def test_run_reclassifies_photomator_edits_from_assets_to_photos(tmp_path: Path) -> None:
    """Extension-less Photomator edit files in 03_ASSETS must move to 02_PHOTOS."""
    folder = dated_folder(tmp_path)
    for rel in ingest.TEMPLATE_DIRS:
        (folder / rel).mkdir(parents=True, exist_ok=True)
    # Simulate old ingest dumping Photomator files into 03_ASSETS
    (folder / "03_ASSETS" / "DSC00816").write_bytes(b"x")
    (folder / "03_ASSETS" / "DSC00818").write_bytes(b"x")
    (folder / "03_ASSETS" / "some.lut").write_bytes(b"x")  # should stay

    list(ingest.run(str(folder), dry_run=False))
    assert (folder / "02_PHOTOS" / "DSC00816").exists()
    assert (folder / "02_PHOTOS" / "DSC00818").exists()
    assert (folder / "03_ASSETS" / "some.lut").exists()


def test_run_photomator_edit_moves_with_photos_on_fresh_ingest(tmp_path: Path) -> None:
    """On a fresh ingest, extension-less Photomator edits move alongside their RAW."""
    folder = dated_folder(tmp_path)
    (folder / "DSC00816.ARW").write_bytes(b"raw")
    (folder / "DSC00816").write_bytes(b"edit")  # Photomator, no extension

    list(ingest.run(str(folder), dry_run=False))
    assert (folder / "02_PHOTOS" / "DSC00816.ARW").exists()
    assert (folder / "02_PHOTOS" / "DSC00816").exists()


def test_run_reclassifies_dji_clips_already_in_a_cam(tmp_path: Path) -> None:
    """DJI files previously moved to A_CAM must be relocated to C_CAM on re-ingest."""
    folder = dated_folder(tmp_path)
    for rel in ingest.TEMPLATE_DIRS:
        (folder / rel).mkdir(parents=True, exist_ok=True)
    # Simulate old ingest putting DJI files in A_CAM
    (folder / "01_VIDEOS" / "A_CAM" / "DJI_0001.MP4").write_bytes(b"x")
    (folder / "01_VIDEOS" / "A_CAM" / "DJI_0002.MOV").write_bytes(b"x")
    (folder / "01_VIDEOS" / "A_CAM" / "C0001.MXF").write_bytes(b"x")  # should stay

    events = list(ingest.run(str(folder), dry_run=False))
    complete = next(e for e in events if e.get("event") == "complete")
    assert len(complete["clip_paths"]["c_cam"]) == 2
    assert (folder / "01_VIDEOS" / "C_CAM" / "DJI_0001.MP4").exists()
    assert (folder / "01_VIDEOS" / "C_CAM" / "DJI_0002.MOV").exists()
    assert (folder / "01_VIDEOS" / "A_CAM" / "C0001.MXF").exists()  # non-DJI unchanged


def test_run_idempotent_on_already_organised_folder(tmp_path: Path) -> None:
    """Re-ingesting an already-organised folder moves nothing."""
    folder = dated_folder(tmp_path)
    for rel in ingest.TEMPLATE_DIRS:
        (folder / rel).mkdir(parents=True, exist_ok=True)
    (folder / "01_VIDEOS" / "A_CAM" / "clip.mxf").write_bytes(b"x")

    events = list(ingest.run(str(folder), dry_run=False))
    complete = next(e for e in events if e.get("event") == "complete")
    assert complete["files_moved"] == 0


# ── sidecar.clip_id ───────────────────────────────────────────────────────────


def test_clip_id_is_16_hex_chars(tmp_path: Path) -> None:
    cid = sidecar.clip_id(str(tmp_path / "clip.mxf"))
    assert len(cid) == 16
    assert all(c in "0123456789abcdef" for c in cid)


def test_clip_id_is_stable(tmp_path: Path) -> None:
    path = str(tmp_path / "clip.mxf")
    assert sidecar.clip_id(path) == sidecar.clip_id(path)


def test_clip_id_differs_for_different_paths(tmp_path: Path) -> None:
    a = sidecar.clip_id(str(tmp_path / "a.mxf"))
    b = sidecar.clip_id(str(tmp_path / "b.mxf"))
    assert a != b


# ── sidecar.write_clip / read_clip ────────────────────────────────────────────


def test_write_clip_creates_json(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mxf"
    clip.write_bytes(b"x")
    path = sidecar.write_clip(
        shoot_folder=tmp_path,
        absolute_path=str(clip),
        bin_path="01_VIDEOS/A_CAM",
        kind="video",
        metadata={"shoot_date": "240101", "camera": "FX3", "lens": "", "timecode_start": ""},
    )
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["schema_version"] == 1
    assert data["kind"] == "video"
    assert "ingest" in data["stages_complete"]


def test_write_clip_merges_stages_on_reingest(tmp_path: Path) -> None:
    """A later-stage marker written before re-ingest must survive."""
    clip = tmp_path / "clip.mxf"
    clip.write_bytes(b"x")
    sidecar.write_clip(
        shoot_folder=tmp_path,
        absolute_path=str(clip),
        bin_path="01_VIDEOS/A_CAM",
        kind="video",
        metadata={},
        extra_stages=["classify"],
    )
    # Re-ingest without classify
    sidecar.write_clip(
        shoot_folder=tmp_path,
        absolute_path=str(clip),
        bin_path="01_VIDEOS/A_CAM",
        kind="video",
        metadata={},
    )
    cid = sidecar.clip_id(str(clip))
    data = json.loads((sidecar.sidecar_root(tmp_path) / f"{cid}.json").read_text())
    assert "classify" in data["stages_complete"]
    assert "ingest" in data["stages_complete"]


def test_read_clip_returns_none_for_missing(tmp_path: Path) -> None:
    result = sidecar.read_clip(tmp_path, str(tmp_path / "ghost.mxf"))
    assert result is None


def test_read_clip_round_trips(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mxf"
    clip.write_bytes(b"x")
    sidecar.write_clip(
        shoot_folder=tmp_path,
        absolute_path=str(clip),
        bin_path="01_VIDEOS/A_CAM",
        kind="video",
        metadata={"shoot_date": "240315", "camera": "ILCE-7SM3", "lens": "", "timecode_start": ""},
    )
    data = sidecar.read_clip(tmp_path, str(clip))
    assert data is not None
    assert data["kind"] == "video"
    assert data["metadata"]["camera"] == "ILCE-7SM3"


def test_read_clip_adds_missing_keys_for_old_schema(tmp_path: Path) -> None:
    """read_clip must not crash on a sidecar missing optional keys."""
    clip = tmp_path / "clip.mxf"
    clip.write_bytes(b"x")
    cid = sidecar.clip_id(str(clip))
    root = sidecar.sidecar_root(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    # Write a minimal v0-style record without stages_complete
    (root / f"{cid}.json").write_text(json.dumps({"schema_version": 0, "kind": "video"}))
    data = sidecar.read_clip(tmp_path, str(clip))
    assert data is not None
    assert "stages_complete" in data
