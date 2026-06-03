import os

import pytest
from helpers import cache


def test_clip_key_is_stable_16_hex():
    k = cache.clip_key("/Volumes/x/C3108.MP4")
    assert len(k) == 16 and all(c in "0123456789abcdef" for c in k)
    assert k == cache.clip_key("/Volumes/x/C3108.MP4")


def test_write_then_read_roundtrip(tmp_path):
    cache.write(tmp_path, "abc", {"label": "A-ROLL"})
    assert cache.read(tmp_path, "abc") == {"label": "A-ROLL"}


def test_write_merges_not_overwrites(tmp_path):
    cache.write(tmp_path, "abc", {"label": "A-ROLL"})
    cache.write(tmp_path, "abc", {"words": [1, 2]})
    rec = cache.read(tmp_path, "abc")
    assert rec["label"] == "A-ROLL" and rec["words"] == [1, 2]


def test_read_missing_returns_none(tmp_path):
    assert cache.read(tmp_path, "nope") is None


def test_write_leaves_no_tmp_file_when_replace_fails(tmp_path, monkeypatch):
    # If os.replace raises mid-write, the partial .tmp must be cleaned up and the
    # existing record left intact — never accumulate stale temp files.
    cache.write(tmp_path, "abc", {"label": "A-ROLL"})

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(cache.os, "replace", boom)
    with pytest.raises(OSError):
        cache.write(tmp_path, "abc", {"words": [1, 2]})

    leftover = [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]
    assert leftover == []
    # Prior record survived untouched.
    assert cache.read(tmp_path, "abc") == {"label": "A-ROLL"}


def test_freshness_tracks_source_signature(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"data")
    rec = {"source_sig": cache.source_signature(src)}
    assert cache.is_fresh(rec, src) is True
    src.write_bytes(b"changed-bigger")
    assert cache.is_fresh(rec, src) is False
    assert cache.is_fresh(None, src) is False
