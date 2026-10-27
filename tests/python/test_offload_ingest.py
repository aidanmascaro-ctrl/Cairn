"""
pytest tests for ingesting an Offload shoot in place (Footage/<Camera> layout).

All tests use tmp_path; no side-effects outside tmp.
"""

from __future__ import annotations

import json
from pathlib import Path

import classify
import ingest
import pytest
import sidecar


def make_offload_shoot(tmp_path: Path) -> Path:
    shoot = tmp_path / "Harbour Coffee Co." / "22.10.26 Brand film"
    (shoot / "Footage" / "FX3").mkdir(parents=True)
    (shoot / "Footage" / "A7S3").mkdir(parents=True)
    (shoot / "Photos").mkdir()
    (shoot / "Project").mkdir()
    (shoot / "Footage" / "FX3" / "C0001.MP4").write_bytes(b"a")
    (shoot / "Footage" / "FX3" / "C0002.MP4").write_bytes(b"b")
    (shoot / "Footage" / "A7S3" / "C0101.MP4").write_bytes(b"c")
    (shoot / "Photos" / "DSC0001.ARW").write_bytes(b"d")
    (shoot / ".cairn").mkdir()
    manifest = {
        "schema_version": 1,
        "client": "Harbour Coffee Co.",
        "shoot_name": "Brand film",
        "date": "2026-10-22",
        "camera": "FX3",
        "camera_model": "ILME-FX3",
        "shoot_folder": str(shoot),
        "footage_folder": str(shoot / "Footage" / "FX3"),
        "files": [],
    }
    (shoot / ".cairn" / "offload.json").write_text(json.dumps(manifest))
    return shoot


def complete_event(shoot: Path) -> dict:
    events = list(ingest.run(shoot))
    return next(e for e in events if e.get("event") == "complete")


def test_offload_shoot_is_ingested_in_place(tmp_path: Path) -> None:
    shoot = make_offload_shoot(tmp_path)
    before = sorted(p.relative_to(shoot) for p in shoot.rglob("*"))

    result = complete_event(shoot)

    assert result["layout"] == "offload"
    assert result["folder"] == str(shoot.resolve())
    assert result["files_moved"] == 0
    assert sorted(p.relative_to(shoot) for p in shoot.rglob("*")) == before  # nothing moved/renamed
    assert not (shoot / "01_VIDEOS").exists()


def test_each_camera_folder_becomes_a_bin(tmp_path: Path) -> None:
    shoot = make_offload_shoot(tmp_path).resolve()

    result = complete_event(shoot)

    assert set(result["camera_clips"]) == {"FX3", "A7S3"}
    assert [Path(p).name for p in result["camera_clips"]["FX3"]] == ["C0001.MP4", "C0002.MP4"]
    assert [Path(p).name for p in result["clip_paths"]["photos"]] == ["DSC0001.ARW"]
    assert result["metadata"]["shoot_date"] == "261022"
    assert result["metadata"]["camera"] == "ILME-FX3"


def test_choose_folder_copy_falls_back_to_manifest_camera(tmp_path: Path) -> None:
    shoot = tmp_path / "Somewhere"
    shoot.mkdir()
    (shoot / "C0001.MP4").write_bytes(b"a")
    (shoot / ".cairn").mkdir()
    (shoot / ".cairn" / "offload.json").write_text(
        json.dumps({"camera": "FX3", "footage_folder": str(shoot), "date": "2026-10-22"})
    )

    assert list(ingest.offload_camera_dirs(shoot)) == ["FX3"]
    assert len(complete_event(shoot)["camera_clips"]["FX3"]) == 1


def test_broken_manifest_raises(tmp_path: Path) -> None:
    shoot = make_offload_shoot(tmp_path)
    (shoot / ".cairn" / "offload.json").write_text("{not json")

    with pytest.raises(ingest.IngestError):
        list(ingest.run(shoot))


def test_offload_sidecars_live_next_to_manifest(tmp_path: Path) -> None:
    shoot = make_offload_shoot(tmp_path)
    assert sidecar.sidecar_root(shoot) == shoot / ".cairn"
    assert sidecar.sidecar_root(tmp_path / "classic") == tmp_path / "classic" / "04_EXPORT" / ".cairn"


def test_classify_finds_offload_footage(tmp_path: Path) -> None:
    shoot = make_offload_shoot(tmp_path)
    names = sorted(Path(p).name for p in classify.scan_video_clips(shoot))
    assert names == ["C0001.MP4", "C0002.MP4", "C0101.MP4"]
