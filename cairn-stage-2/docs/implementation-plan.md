# Cairn Stage 2 — Auto-Cut Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a live Claude-Code harness that reads an existing Resolve timeline, transcribes its A-roll, lets the operator approve a soundbite "radio edit", and assembles an A-roll + B-roll rough cut on a new timeline — using local tools + MCP only, no shipped-plugin code.

**Architecture:** Deterministic, testable logic lives in small pure-ish Python helpers under `cairn-stage-2/helpers/` (timecode math, sidecar cache, speech classification, Whisper transcription shaping, frame extraction, assembly planning). The non-deterministic orchestration (reading the timeline, the approval gate, executing the assembly, rendering) is Claude-driven live via the `mcp__davinci-resolve__*` MCP, documented in `cairn-stage-2/docs/runbook.md`. A live MCP spike (Task 1) pins down exactly what the MCP assembly surface can do before any helper is built on top of it.

**Tech Stack:** Python 3 (stdlib + `faster-whisper`), system `ffmpeg`/`ffprobe`, `pytest`, DaVinci Resolve MCP (`mcp__davinci-resolve__*`), Claude in-session reasoning.

**Boundary:** Everything created by this plan lives under `cairn-stage-2/`. No task may edit `../src/`, `../spec.md`, or `../IMPLEMENTATION_PLAN.md`. See `cairn-stage-2/README.md`.

**Branch:** Before Task 1, create a working branch: `git checkout -b cairn-stage-2`.

---

## File Structure

```
cairn-stage-2/
├── README.md                 # boundary declaration (exists)
├── docs/
│   ├── design.md             # approved design (exists)
│   ├── implementation-plan.md# this file
│   └── runbook.md            # live orchestration steps (Task 9)
├── helpers/
│   ├── __init__.py
│   ├── timecode.py           # sec ↔ frame ↔ "HH:MM:SS:FF" (Task 3)
│   ├── cache.py              # sidecar read/write, freshness (Task 4)
│   ├── classify.py           # ffmpeg silencedetect → speech ratio → label (Task 5)
│   ├── transcribe.py         # faster-whisper wrapper + record shaping (Task 6)
│   ├── frames.py             # representative-frame extraction (Task 7)
│   └── assemble.py           # pure assembly planner: soundbites → MCP call sequence (Task 8)
├── tests/
│   ├── test_timecode.py
│   ├── test_cache.py
│   ├── test_classify.py
│   ├── test_transcribe.py
│   ├── test_frames.py
│   └── test_assemble.py
├── requirements.txt          # faster-whisper, pytest
├── pytest.ini                # rootdir config
└── .cache/                   # runtime sidecar cache (git-ignored, exists)
```

Run tests from `cairn-stage-2/`: `python3 -m pytest -q`.

---

## Task 1: Live MCP assembly capability spike

**Why first:** The whole assembly design rests on two assumptions about the *older* `mcp__davinci-resolve__*` server: (a) `media_pool_item.set_mark_in_out` + `media_pool.append_to_timeline` appends only the **marked** sub-range of a clip, and (b) whether B-roll can be placed on a chosen **track (V2) at a chosen record frame**. If (b) is false, Stage 7 must fall back (e.g. B-roll appended sequentially on V2, operator slides into place). Verify before building.

This task is **live MCP exploration**, not code. Use a throwaway timeline; clean up after.

- [ ] **Step 1: Create a throwaway test timeline**

Via MCP: `media_pool.create_timeline(name="_cairn_test")`. Confirm with `timeline.list`.

- [ ] **Step 2: Verify marked-subclip append**

Pick one media pool item id (from `timeline.get_items` on the source timeline). Then:
1. `media_pool_item.set_mark_in_out(clip_id, mark_in=50, mark_out=100, type="all")`
2. `media_pool.append_to_timeline(clip_ids=[clip_id])`
3. `timeline.get_items(track_type="video", index=1)` — confirm the appended item `duration` ≈ 51 frames (the marked range), **not** the full clip length.

Record the observed duration in `runbook.md`. If append ignores marks → note the fallback (use `timeline.import_into_timeline` or operator-side trimming).

- [ ] **Step 3: Probe track/record-frame control for B-roll**

Inspect the live schema of `media_pool.append_to_timeline` and scan `timeline` actions for any insert-at-track/frame capability (e.g. `insert_*`, `import_into_timeline` options). Attempt to append a second clip and check which track it lands on (`timeline.get_track_count` before/after, `get_items` on track 2). Document: **can B-roll be targeted to V2 at a specific record frame, yes/no?**

- [ ] **Step 4: Clean up**

`timeline.list` → find `_cairn_test` id → `media_pool.delete_timelines([id])`. Confirm it's gone. **Never leave test artifacts in the project.**

- [ ] **Step 5: Write findings**

Create `cairn-stage-2/docs/runbook.md` with a `## MCP assembly findings (Task 1)` section recording: marked-append behaviour, V2/record-frame capability, and the chosen B-roll placement strategy. This drives Task 8 and Task 9.

- [ ] **Step 6: Commit**

```bash
git add cairn-stage-2/docs/runbook.md
git commit -m "docs(stage2): MCP assembly capability spike findings"
```

---

## Task 2: Helpers scaffold

**Files:**
- Create: `cairn-stage-2/helpers/__init__.py`
- Create: `cairn-stage-2/requirements.txt`
- Create: `cairn-stage-2/pytest.ini`

- [ ] **Step 1: Create the package init**

`cairn-stage-2/helpers/__init__.py`:
```python
"""Cairn Stage 2 harness helpers (experimental). Walled off from the shipped plugin."""
```

- [ ] **Step 2: Create requirements.txt**

`cairn-stage-2/requirements.txt`:
```
faster-whisper>=1.2.0
pytest>=8.0
```

- [ ] **Step 3: Create pytest.ini**

`cairn-stage-2/pytest.ini`:
```ini
[pytest]
testpaths = tests
python_files = test_*.py
pythonpath = .
addopts = -q
```

(`pythonpath = .` puts `cairn-stage-2/` on `sys.path` so `from helpers import ...` resolves when running pytest from that directory. Requires pytest >= 7.)

- [ ] **Step 4: Verify pytest collects (zero tests yet is OK)**

Run: `cd cairn-stage-2 && python3 -m pytest`
Expected: "no tests ran" (exit code 5) — confirms config is valid.

- [ ] **Step 5: Commit**

```bash
git add cairn-stage-2/helpers/__init__.py cairn-stage-2/requirements.txt cairn-stage-2/pytest.ini
git commit -m "chore(stage2): helpers scaffold + pytest config"
```

---

## Task 3: `timecode.py` — frame/seconds/timecode conversion

**Files:**
- Create: `cairn-stage-2/helpers/timecode.py`
- Test: `cairn-stage-2/tests/test_timecode.py`

Non-drop-frame only (v1 footage is 25fps, `Drop frame=0`). Drop-frame rates raise.

- [ ] **Step 1: Write the failing tests**

`cairn-stage-2/tests/test_timecode.py`:
```python
import pytest
from helpers import timecode as tc


def test_seconds_to_frames_rounds_to_nearest():
    assert tc.seconds_to_frames(2.0, 25) == 50
    assert tc.seconds_to_frames(2.02, 25) == 51   # 50.5 -> 51 (round half up via round())
    assert tc.seconds_to_frames(0.0, 25) == 0


def test_frames_to_seconds():
    assert tc.frames_to_seconds(50, 25) == pytest.approx(2.0)


def test_timecode_to_frames_non_drop():
    assert tc.timecode_to_frames("00:00:02:00", 25) == 50
    assert tc.timecode_to_frames("00:00:00:24", 25) == 24
    assert tc.timecode_to_frames("01:00:00:00", 25) == 25 * 60 * 60


def test_frames_to_timecode_roundtrip():
    for frames in (0, 24, 25, 50, 9000, 90000):
        s = tc.frames_to_timecode(frames, 25)
        assert tc.timecode_to_frames(s, 25) == frames


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        tc.seconds_to_frames(-1, 25)
    with pytest.raises(ValueError):
        tc.timecode_to_frames("00:00:25", 25)      # wrong field count
    with pytest.raises(ValueError):
        tc.timecode_to_frames("00:00:00:25", 25)   # frame out of range
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd cairn-stage-2 && python3 -m pytest tests/test_timecode.py -v`
Expected: FAIL — `ModuleNotFoundError: helpers.timecode`.

- [ ] **Step 3: Write the implementation**

`cairn-stage-2/helpers/timecode.py`:
```python
"""Frame / seconds / timecode conversion. Non-drop-frame only (v1 = 25fps)."""
from __future__ import annotations


def _ifps(fps: float) -> int:
    if fps <= 0:
        raise ValueError("fps must be > 0")
    ifps = round(fps)
    if abs(fps - ifps) > 1e-6:
        raise ValueError(f"non-integer fps {fps} (drop-frame) not supported in v1")
    return ifps


def seconds_to_frames(seconds: float, fps: float) -> int:
    if seconds < 0:
        raise ValueError("seconds must be >= 0")
    if fps <= 0:
        raise ValueError("fps must be > 0")
    # round half up (Python's round() is banker's rounding: round(50.5) == 50)
    return int(seconds * fps + 0.5)


def frames_to_seconds(frames: int, fps: float) -> float:
    if fps <= 0:
        raise ValueError("fps must be > 0")
    return frames / fps


def timecode_to_frames(tc: str, fps: float) -> int:
    parts = tc.split(":")
    if len(parts) != 4:
        raise ValueError(f"bad timecode {tc!r}; expected HH:MM:SS:FF")
    hh, mm, ss, ff = (int(p) for p in parts)
    ifps = _ifps(fps)
    if not (0 <= ff < ifps):
        raise ValueError(f"frame {ff} out of range for {ifps}fps")
    return ((hh * 60 + mm) * 60 + ss) * ifps + ff


def frames_to_timecode(frames: int, fps: float) -> str:
    if frames < 0:
        raise ValueError("frames must be >= 0")
    ifps = _ifps(fps)
    ff = frames % ifps
    total = frames // ifps
    ss, mm, hh = total % 60, (total // 60) % 60, total // 3600
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd cairn-stage-2 && python3 -m pytest tests/test_timecode.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add cairn-stage-2/helpers/timecode.py cairn-stage-2/tests/test_timecode.py
git commit -m "feat(stage2): timecode/frame conversion helper"
```

---

## Task 4: `cache.py` — sidecar cache with freshness

**Files:**
- Create: `cairn-stage-2/helpers/cache.py`
- Test: `cairn-stage-2/tests/test_cache.py`

- [ ] **Step 1: Write the failing tests**

`cairn-stage-2/tests/test_cache.py`:
```python
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


def test_freshness_tracks_source_signature(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"data")
    rec = {"source_sig": cache.source_signature(src)}
    assert cache.is_fresh(rec, src) is True
    src.write_bytes(b"changed-bigger")
    assert cache.is_fresh(rec, src) is False
    assert cache.is_fresh(None, src) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd cairn-stage-2 && python3 -m pytest tests/test_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: helpers.cache`.

- [ ] **Step 3: Write the implementation**

`cairn-stage-2/helpers/cache.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd cairn-stage-2 && python3 -m pytest tests/test_cache.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add cairn-stage-2/helpers/cache.py cairn-stage-2/tests/test_cache.py
git commit -m "feat(stage2): sidecar cache with freshness check"
```

---

## Task 5: `classify.py` — speech ratio via ffmpeg silencedetect

**Files:**
- Create: `cairn-stage-2/helpers/classify.py`
- Test: `cairn-stage-2/tests/test_classify.py`

Uses ffmpeg's built-in `silencedetect` (no extra dep). Pure parser is tested directly; the ffmpeg call is isolated for mocking.

- [ ] **Step 1: Write the failing tests**

`cairn-stage-2/tests/test_classify.py`:
```python
import pytest
from helpers import classify


SILENCE_STDERR = """\
[silencedetect @ 0x1] silence_start: 0.0
[silencedetect @ 0x1] silence_end: 1.0 | silence_duration: 1.0
[silencedetect @ 0x1] silence_start: 8.0
[silencedetect @ 0x1] silence_end: 9.0 | silence_duration: 1.0
"""


def test_parse_silence_ratio_subtracts_silent_time():
    # 10s clip, 2s silent -> 0.8 speech ratio
    assert classify.parse_silence_ratio(SILENCE_STDERR, 10.0) == pytest.approx(0.8)


def test_parse_handles_trailing_unclosed_silence():
    stderr = "silence_start: 9.0\n"     # runs to end
    assert classify.parse_silence_ratio(stderr, 10.0) == pytest.approx(0.9)


def test_parse_no_silence_is_full_speech():
    assert classify.parse_silence_ratio("", 10.0) == pytest.approx(1.0)


@pytest.mark.parametrize("ratio,label", [
    (0.9, "A-ROLL"),
    (0.45, "A-ROLL"),
    (0.44, "AMBIGUOUS"),
    (0.11, "AMBIGUOUS"),
    (0.10, "B-ROLL"),
    (0.0, "B-ROLL"),
])
def test_classify_thresholds(ratio, label):
    assert classify.classify(ratio) == label


def test_parse_rejects_bad_duration():
    with pytest.raises(ValueError):
        classify.parse_silence_ratio("", 0.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd cairn-stage-2 && python3 -m pytest tests/test_classify.py -v`
Expected: FAIL — `ModuleNotFoundError: helpers.classify`.

- [ ] **Step 3: Write the implementation**

`cairn-stage-2/helpers/classify.py`:
```python
"""A-roll vs B-roll via ffmpeg silencedetect speech ratio."""
from __future__ import annotations

import re
import subprocess

AROLL_THRESHOLD = 0.45   # speech_ratio >= this -> A-ROLL
BROLL_THRESHOLD = 0.10   # speech_ratio <= this -> B-ROLL

_START = re.compile(r"silence_start:\s*([0-9.]+)")
_END = re.compile(r"silence_end:\s*([0-9.]+)")


def parse_silence_ratio(stderr: str, duration: float) -> float:
    """speech_ratio = 1 - silent_time/duration, parsed from silencedetect stderr."""
    if duration <= 0:
        raise ValueError("duration must be > 0")
    starts = [float(x) for x in _START.findall(stderr)]
    ends = [float(x) for x in _END.findall(stderr)]
    silent = 0.0
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration
        silent += max(0.0, min(e, duration) - s)
    return max(0.0, min(1.0, 1.0 - silent / duration))


def classify(speech_ratio: float) -> str:
    if speech_ratio >= AROLL_THRESHOLD:
        return "A-ROLL"
    if speech_ratio <= BROLL_THRESHOLD:
        return "B-ROLL"
    return "AMBIGUOUS"


def probe_duration(file_path, ffprobe: str = "ffprobe") -> float:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", str(file_path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def run_silencedetect(file_path, ffmpeg: str = "ffmpeg",
                      noise: str = "-30dB", min_silence: float = 0.5) -> str:
    proc = subprocess.run(
        [ffmpeg, "-i", str(file_path), "-af",
         f"silencedetect=noise={noise}:d={min_silence}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return proc.stderr


def analyze_clip(file_path) -> dict:
    """Side-effectful convenience: probe + silencedetect + classify."""
    duration = probe_duration(file_path)
    ratio = parse_silence_ratio(run_silencedetect(file_path), duration)
    return {"duration": duration, "speech_ratio": ratio, "label": classify(ratio)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd cairn-stage-2 && python3 -m pytest tests/test_classify.py -v`
Expected: PASS (parser + 6 parametrized threshold cases).

- [ ] **Step 5: Commit**

```bash
git add cairn-stage-2/helpers/classify.py cairn-stage-2/tests/test_classify.py
git commit -m "feat(stage2): A/B-roll speech-ratio classifier (silencedetect)"
```

---

## Task 6: `transcribe.py` — faster-whisper wrapper + record shaping

**Files:**
- Create: `cairn-stage-2/helpers/transcribe.py`
- Test: `cairn-stage-2/tests/test_transcribe.py`

The pure shaping function (`segments_to_record`) is fully tested with fake segment objects. The model load/call is isolated so unit tests need no model download.

- [ ] **Step 1: Write the failing tests**

`cairn-stage-2/tests/test_transcribe.py`:
```python
from dataclasses import dataclass
from helpers import transcribe


@dataclass
class FakeWord:
    word: str
    start: float
    end: float


@dataclass
class FakeSegment:
    start: float
    end: float
    text: str
    words: list


@dataclass
class FakeInfo:
    language: str
    duration: float


class FakeModel:
    def __init__(self, segments, info):
        self._segments, self._info = segments, info
        self.called_with = None

    def transcribe(self, path, **kw):
        self.called_with = (path, kw)
        return iter(self._segments), self._info


def test_segments_to_record_flattens_words():
    segs = [
        FakeSegment(0.0, 1.0, " Hello ", [FakeWord(" Hello", 0.0, 0.5)]),
        FakeSegment(1.0, 2.0, " world ", [FakeWord(" world", 1.0, 1.5)]),
    ]
    rec = transcribe.segments_to_record(segs, FakeInfo("en", 2.0))
    assert rec["language"] == "en"
    assert rec["duration"] == 2.0
    assert [w["text"] for w in rec["words"]] == ["Hello", "world"]
    assert rec["words"][0] == {"text": "Hello", "start": 0.0, "end": 0.5}
    assert [s["text"] for s in rec["segments"]] == ["Hello", "world"]


def test_transcribe_file_requests_word_timestamps():
    model = FakeModel([FakeSegment(0.0, 1.0, "hi", [FakeWord("hi", 0.0, 1.0)])],
                      FakeInfo("en", 1.0))
    rec = transcribe.transcribe_file("/x/clip.mp4", model)
    assert model.called_with[0] == "/x/clip.mp4"
    assert model.called_with[1]["word_timestamps"] is True
    assert rec["words"][0]["text"] == "hi"


def test_segments_with_no_words_still_records_segment():
    segs = [FakeSegment(0.0, 1.0, "ambient", None)]
    rec = transcribe.segments_to_record(segs, FakeInfo("en", 1.0))
    assert rec["words"] == []
    assert rec["segments"][0]["text"] == "ambient"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd cairn-stage-2 && python3 -m pytest tests/test_transcribe.py -v`
Expected: FAIL — `ModuleNotFoundError: helpers.transcribe`.

- [ ] **Step 3: Write the implementation**

`cairn-stage-2/helpers/transcribe.py`:
```python
"""faster-whisper transcription → word-timestamped record. Single-speaker v1."""
from __future__ import annotations

from typing import Any, Iterable


def segments_to_record(segments: Iterable[Any], info: Any) -> dict:
    words: list[dict] = []
    seg_list: list[dict] = []
    for seg in segments:
        seg_list.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
        for w in (seg.words or []):
            words.append({"text": w.word.strip(), "start": w.start, "end": w.end})
    return {
        "language": getattr(info, "language", None),
        "duration": getattr(info, "duration", None),
        "segments": seg_list,
        "words": words,
    }


def transcribe_file(file_path, model, **kw) -> dict:
    segments, info = model.transcribe(str(file_path), word_timestamps=True, **kw)
    return segments_to_record(segments, info)


def load_model(size: str = "base", device: str = "auto", compute_type: str = "int8"):
    from faster_whisper import WhisperModel  # imported lazily so unit tests need no model
    return WhisperModel(size, device=device, compute_type=compute_type)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd cairn-stage-2 && python3 -m pytest tests/test_transcribe.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add cairn-stage-2/helpers/transcribe.py cairn-stage-2/tests/test_transcribe.py
git commit -m "feat(stage2): faster-whisper wrapper + word-record shaping"
```

---

## Task 7: `frames.py` — representative-frame extraction

**Files:**
- Create: `cairn-stage-2/helpers/frames.py`
- Test: `cairn-stage-2/tests/test_frames.py`

`frame_timestamps` is pure and fully tested. `extract_frames` gets one real integration test using an ffmpeg-generated test clip (skipped if ffmpeg is absent).

- [ ] **Step 1: Write the failing tests**

`cairn-stage-2/tests/test_frames.py`:
```python
import shutil
import subprocess
import pytest
from helpers import frames


def test_frame_timestamps_count_and_order():
    ts = frames.frame_timestamps(100.0, count=5)
    assert len(ts) == 5
    assert ts == sorted(ts)
    assert ts[0] >= 0.0 and ts[-1] <= 100.0


def test_frame_timestamps_single():
    assert frames.frame_timestamps(10.0, count=1) == [5.0]


def test_frame_timestamps_invalid():
    with pytest.raises(ValueError):
        frames.frame_timestamps(0.0)
    with pytest.raises(ValueError):
        frames.frame_timestamps(10.0, count=0)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_extract_frames_writes_jpgs(tmp_path):
    clip = tmp_path / "test.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=25",
         str(clip)], capture_output=True, check=True,
    )
    out = frames.extract_frames(clip, [0.5, 1.5, 2.5], tmp_path / "frames")
    assert len(out) == 3
    assert all(p.exists() and p.stat().st_size > 0 for p in out)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd cairn-stage-2 && python3 -m pytest tests/test_frames.py -v`
Expected: FAIL — `ModuleNotFoundError: helpers.frames`.

- [ ] **Step 3: Write the implementation**

`cairn-stage-2/helpers/frames.py`:
```python
"""Extract representative frames from a clip for Claude to view (B-roll tagging)."""
from __future__ import annotations

import subprocess
from pathlib import Path


def frame_timestamps(duration: float, count: int = 5) -> list[float]:
    if duration <= 0:
        raise ValueError("duration must be > 0")
    if count < 1:
        raise ValueError("count must be >= 1")
    if count == 1:
        return [duration / 2]
    inset = min(0.1, duration * 0.02)
    span = duration - 2 * inset
    return [round(inset + span * i / (count - 1), 3) for i in range(count)]


def extract_frames(file_path, timestamps, out_dir, ffmpeg: str = "ffmpeg") -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, ts in enumerate(timestamps):
        out = out_dir / f"frame_{i:02d}.jpg"
        subprocess.run(
            [ffmpeg, "-y", "-ss", str(ts), "-i", str(file_path),
             "-frames:v", "1", "-q:v", "4", str(out)],
            capture_output=True, text=True, check=True,
        )
        paths.append(out)
    return paths
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd cairn-stage-2 && python3 -m pytest tests/test_frames.py -v`
Expected: PASS (3 pure tests pass; the ffmpeg integration test passes if ffmpeg present, else skips).

- [ ] **Step 5: Commit**

```bash
git add cairn-stage-2/helpers/frames.py cairn-stage-2/tests/test_frames.py
git commit -m "feat(stage2): representative-frame extraction helper"
```

---

## Task 8: `assemble.py` — pure assembly planner

**Files:**
- Create: `cairn-stage-2/helpers/assemble.py`
- Test: `cairn-stage-2/tests/test_assemble.py`

Isolates the frame math + MCP call sequence from the live calls. The runbook (Task 9) executes this plan. **If Task 1 found that marked-append uses inclusive out-frames differently, adjust `soundbite_to_mark` here and update the test — Task 1 findings are authoritative over the default below.**

- [ ] **Step 1: Write the failing tests**

`cairn-stage-2/tests/test_assemble.py`:
```python
import pytest
from helpers import assemble


def test_soundbite_to_mark_converts_seconds_to_frames():
    sb = assemble.Soundbite(clip_id="abc", src_in_sec=2.0, src_out_sec=4.0, fps=25)
    m = assemble.soundbite_to_mark(sb)
    assert m.clip_id == "abc"
    assert m.mark_in == 50
    assert m.mark_out == 99   # 100 (out) - 1 inclusive last frame


def test_soundbite_rejects_zero_or_negative_span():
    with pytest.raises(ValueError):
        assemble.soundbite_to_mark(
            assemble.Soundbite("abc", src_in_sec=4.0, src_out_sec=4.0, fps=25))


def test_assembly_plan_emits_set_mark_then_append_in_order():
    sbs = [
        assemble.Soundbite("a", 1.0, 2.0, 25),
        assemble.Soundbite("b", 0.0, 1.0, 25),
    ]
    plan = assemble.assembly_plan(sbs)
    assert [p["set_mark"]["clip_id"] for p in plan] == ["a", "b"]
    assert plan[0]["set_mark"]["mark_in"] == 25
    assert plan[0]["set_mark"]["mark_out"] == 49
    assert plan[0]["append"]["clip_id"] == "a"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd cairn-stage-2 && python3 -m pytest tests/test_assemble.py -v`
Expected: FAIL — `ModuleNotFoundError: helpers.assemble`.

- [ ] **Step 3: Write the implementation**

`cairn-stage-2/helpers/assemble.py`:
```python
"""Pure assembly planner: ordered soundbites -> deterministic MCP call sequence.

Executed live by the runbook: for each entry, call
  media_pool_item.set_mark_in_out(clip_id, mark_in, mark_out, type="all")
  media_pool.append_to_timeline(clip_ids=[clip_id])
"""
from __future__ import annotations

from dataclasses import dataclass

from .timecode import seconds_to_frames


@dataclass(frozen=True)
class Soundbite:
    clip_id: str          # MCP media pool item id
    src_in_sec: float     # in-point within the source clip (seconds)
    src_out_sec: float    # out-point within the source clip (seconds)
    fps: float


@dataclass(frozen=True)
class MarkInOut:
    clip_id: str
    mark_in: int          # inclusive source frame
    mark_out: int         # inclusive source frame


def soundbite_to_mark(sb: Soundbite) -> MarkInOut:
    if sb.src_out_sec <= sb.src_in_sec:
        raise ValueError("src_out_sec must be greater than src_in_sec")
    return MarkInOut(
        clip_id=sb.clip_id,
        mark_in=seconds_to_frames(sb.src_in_sec, sb.fps),
        mark_out=seconds_to_frames(sb.src_out_sec, sb.fps) - 1,
    )


def assembly_plan(soundbites: list[Soundbite]) -> list[dict]:
    plan: list[dict] = []
    for sb in soundbites:
        m = soundbite_to_mark(sb)
        plan.append({
            "set_mark": {"clip_id": m.clip_id, "mark_in": m.mark_in,
                         "mark_out": m.mark_out, "type": "all"},
            "append": {"clip_id": m.clip_id},
        })
    return plan
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd cairn-stage-2 && python3 -m pytest tests/test_assemble.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full suite**

Run: `cd cairn-stage-2 && python3 -m pytest -q`
Expected: all tests across all helpers PASS.

- [ ] **Step 6: Commit**

```bash
git add cairn-stage-2/helpers/assemble.py cairn-stage-2/tests/test_assemble.py
git commit -m "feat(stage2): pure assembly planner (soundbites -> MCP sequence)"
```

---

## Task 9: `runbook.md` — live orchestration

**Files:**
- Modify: `cairn-stage-2/docs/runbook.md` (created in Task 1; append the orchestration sections)

This documents the end-to-end live procedure Claude follows in a session, tying the helpers + MCP together. No code to test — it is the operator/agent script. It must reflect Task 1's findings.

- [ ] **Step 1: Write the orchestration runbook**

Append to `cairn-stage-2/docs/runbook.md` these sections, each as concrete numbered steps:

1. **Preconditions** — Resolve open, target timeline current, footage drive mounted; `python3 -c "import faster_whisper"` and `ffmpeg -version` succeed.
2. **Read the timeline** — `timeline.get_current`; `timeline.get_track_count(video)`; for each video track `timeline.get_items`; for each unique clip `timeline_item.get_media_pool_item` then `media_pool_item.get_clip_property` → build the manifest (`clip_id`, `File Path`, `FPS`, `Resolution`, `Duration`). Cache via `helpers.cache`.
3. **Classify** — for each clip: `helpers.classify.analyze_clip(path)` → label; write to cache; present the A/B/AMBIGUOUS summary; **operator confirms ambiguous clips** before continuing.
4. **Transcribe A-roll** — `model = helpers.transcribe.load_model("base")`; for each A-roll clip not already fresh in cache: `helpers.transcribe.transcribe_file(path, model)`; write to cache. Report cost = time only.
5. **Collect the brief** — record platform/aspect, target duration, angle, tone into `cairn-stage-2/.cache/brief.json`.
6. **Select soundbites (★ APPROVAL GATE)** — Claude reads cached transcripts against the brief, proposes an ordered radio edit: for each pick show clip name, `in`/`out` timecode, transcript text, beat, and why. **Present this list and STOP. Do not proceed to assembly until the operator approves it.**
7. **Tag + match B-roll** — for each B-roll clip: `helpers.frames.extract_frames` then Claude views the JPGs via Read and assigns tags; match each soundbite to literal/thematic B-roll; write to cache.
8. **Assemble** — build `Soundbite` objects (clip_id = media pool item id, in/out seconds from the approved picks, fps from manifest); `plan = helpers.assemble.assembly_plan(soundbites)`. Create a new timeline via `media_pool.create_timeline(name="CAIRN_AUTOCUT_<brief>_<n>")`, set it current, then execute each plan entry's `set_mark` + `append`. Place B-roll on V2 using the strategy Task 1 established (targeted record-frame if supported, else sequential-append + operator slide). **Never modify the source timeline.**
9. **Verify** — `timeline.get_items` on the new timeline; confirm clip count and per-clip durations match the plan; report any mismatch.
10. **Refine / render** — operator trims; optionally `render.add_job` + `render.start` for a draft via the MCP render pipeline.

Each step must name the exact MCP action and helper function used.

- [ ] **Step 2: Self-check the runbook against the design**

Confirm every pipeline stage in `docs/design.md` §3 maps to a runbook section, and the Stage-5 approval gate is explicit and blocking.

- [ ] **Step 3: Commit**

```bash
git add cairn-stage-2/docs/runbook.md
git commit -m "docs(stage2): end-to-end live orchestration runbook"
```

---

## Done criteria

- All helper unit tests pass: `cd cairn-stage-2 && python3 -m pytest -q` green.
- Task 1 MCP findings recorded; assembly approach (esp. V2 B-roll) is decided, not assumed.
- `runbook.md` covers all 8 design stages with exact MCP actions + helper calls, and a blocking soundbite-approval gate.
- Nothing outside `cairn-stage-2/` was modified.
- A live dry run on the Melville timeline can be driven end-to-end from the runbook (separate from this build).
