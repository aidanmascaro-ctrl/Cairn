"""
Stage 5 — Selects: analysis + re-scoring entry points (specs/selects.md).

    analyse_all(shoot, rate)   → streams progress; writes each clip's `selects`
                                 block to its sidecar (sidecar first, always)
    rescore(shoot, settings)   → scores every analysed clip from stored
                                 measurements; nothing is decoded

Source media is only ever read (ffmpeg → stdout pipe). Nothing is written
next to it.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import classify
import numpy as np
import selects_metrics as metrics
import selects_score as ss
import sidecar

# 2: metrics recalibrated 2026-10-27 + source fingerprint; older blocks are re-analysed.
BLOCK_VERSION = 2
LONG_EDGE = 960
MIN_DECODED_FRACTION = 0.9  # fewer samples than this share of the expected count = damaged file
CACHE_LIMIT = 256  # sidecars held in memory for instant re-scoring
PROGRESS_EVERY = 20  # samples between clip_progress events
DEFAULT_WORKERS = 2  # ffmpeg already multi-threads each decode
SETTINGS_FILE = "selects.json"


# ── Probe ────────────────────────────────────────────────────────────────────


def _parse_rate(raw: str | None) -> float:
    if not raw or raw == "0/0":
        return 0.0
    if "/" in raw:
        num, den = raw.split("/", 1)
        try:
            return float(num) / float(den) if float(den) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def probe(clip_path: str) -> dict[str, Any]:
    """Width, height (after rotation), fps and duration of the first video stream."""
    proc = subprocess.run(
        [
            str(classify.FFPROBE_PATH),
            "-v",
            "error",
            "-protocol_whitelist",
            "file",  # a playlist disguised as .mp4 must not pull in other files
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,avg_frame_rate,duration:stream_side_data=rotation:format=duration",
            "-of",
            "json",
            clip_path,
        ],
        stdin=subprocess.DEVNULL,  # never let a child read the bridge's protocol stream
        capture_output=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.decode(errors='replace')[-200:]}")
    data = json.loads(proc.stdout or b"{}")
    streams = data.get("streams") or []
    if not streams:
        raise RuntimeError("no video stream")
    st = streams[0]
    width, height = int(st.get("width") or 0), int(st.get("height") or 0)
    rotation = 0
    for side in st.get("side_data_list") or []:
        if "rotation" in side:
            rotation = int(side["rotation"]) % 360
    if rotation in (90, 270):
        width, height = height, width
    fps = _parse_rate(st.get("r_frame_rate")) or _parse_rate(st.get("avg_frame_rate"))
    # The container duration is the longest stream; audio running past the picture
    # would put select ends beyond the last video frame. Prefer the video stream.
    container = float((data.get("format") or {}).get("duration") or 0.0)
    stream = float(st.get("duration") or 0.0)
    duration = (
        min(d for d in (container, stream) if d > 0) if (container > 0 or stream > 0) else 0.0
    )
    if width <= 0 or height <= 0 or fps <= 0 or duration <= 0:
        raise RuntimeError(f"unreadable video stream ({width}x{height} @ {fps} fps, {duration}s)")
    return {"width": width, "height": height, "fps": fps, "duration_sec": duration}


def working_size(width: int, height: int) -> tuple[int, int]:
    """Downscale so the long edge is at most LONG_EDGE; both sides even."""
    scale = min(1.0, LONG_EDGE / max(width, height))
    w = max(2, round(width * scale / 2) * 2)
    h = max(2, round(height * scale / 2) * 2)
    return w, h


# ── Sampler ──────────────────────────────────────────────────────────────────


def sample_frames(clip_path: str, rate: float, width: int, height: int) -> Iterator[np.ndarray]:
    """
    Yield grayscale uint8 frames at `rate` samples per second (0 = every frame),
    downscaled to working size. One ffmpeg process, raw frames over a pipe.
    """
    w, h = working_size(width, height)
    vf = f"scale={w}:{h}:flags=area,format=gray"
    if rate > 0:
        vf = f"fps={rate:g}," + vf
    cmd = [
        str(classify.FFMPEG_PATH),
        "-v",
        "error",
        "-nostdin",
        "-protocol_whitelist",
        "file",
        "-i",
        clip_path,
        "-an",
        "-sn",
        "-vf",
        vf,
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "-",
    ]
    frame_bytes = w * h
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err)
        if proc.stdout is None:
            raise RuntimeError("ffmpeg stdout pipe missing")
        completed = False
        try:
            while True:
                buf = proc.stdout.read(frame_bytes)
                if len(buf) < frame_bytes:
                    break
                yield np.frombuffer(buf, dtype=np.uint8).reshape(h, w)
            completed = True
        finally:
            proc.stdout.close()
            if not completed:
                # Consumer stopped early (error or cancel): don't mask its exception.
                proc.kill()
                proc.wait()
        code = proc.wait()
        if code != 0:
            err.seek(0)
            tail = err.read().decode(errors="replace")[-300:]
            raise RuntimeError(f"ffmpeg exited {code}: {tail.strip()}")


# ── Per-clip analysis ────────────────────────────────────────────────────────


class AnalysisCancelledError(Exception):
    """Raised inside a worker when the shoot's analysis is being stopped."""


def fingerprint(clip_path: str) -> dict[str, int]:
    """Size + mtime of the source, so a replaced file is re-analysed."""
    st = Path(clip_path).stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def analyse_clip(
    clip_path: str,
    rate: float,
    on_progress: Any = None,
    stop: threading.Event | None = None,
) -> dict[str, Any]:
    """Decode, measure and return the sidecar `selects` block for one clip."""
    info = probe(clip_path)
    effective_rate = rate if rate > 0 else info["fps"]
    expected = max(1, int(info["duration_sec"] * effective_rate))

    times: list[float] = []
    statics: list[dict[str, float]] = []
    motions: list[dict[str, float]] = []
    prev: np.ndarray | None = None
    for i, frame in enumerate(sample_frames(clip_path, rate, info["width"], info["height"])):
        if stop is not None and stop.is_set():
            raise AnalysisCancelledError  # closing the generator kills ffmpeg
        times.append(round(i / effective_rate, 4))
        statics.append(metrics.measure_static(frame))
        if prev is not None:
            motions.append(metrics.measure_motion(prev, frame, effective_rate))
        prev = frame
        if on_progress is not None and i % PROGRESS_EVERY == 0:
            on_progress(min(1.0, (i + 1) / expected))

    if not statics:
        raise RuntimeError("no frames decoded")
    if len(statics) < MIN_DECODED_FRACTION * expected:
        # ffmpeg often exits 0 after decode errors; a short read means a damaged file.
        raise RuntimeError(
            f"only {len(statics)} of about {expected} samples decoded — the file may be damaged"
        )
    return {
        "version": BLOCK_VERSION,
        "sampling_rate": effective_rate,
        "requested_rate": rate,
        "fps": info["fps"],
        "duration_sec": round(info["duration_sec"], 4),
        "width": info["width"],
        "height": info["height"],
        "source": fingerprint(clip_path),
        "analysed_at": datetime.now(UTC).isoformat(),
        "columns": list(ss.COLUMNS),
        "frames": metrics.finalise(times, statics, motions),
    }


def _already_analysed(record: dict[str, Any] | None, rate: float, clip_path: str = "") -> bool:
    if not record or "selects" not in record.get("stages_complete", []):
        return False
    block = record.get("selects") or {}
    try:
        same_source = block.get("source") == fingerprint(
            clip_path or str(record.get("source_path", ""))
        )
    except OSError:
        same_source = False
    return (
        block.get("version") == BLOCK_VERSION
        and float(block.get("requested_rate", -1)) == float(rate)
        and bool(block.get("frames"))
        and same_source
    )


def analyse_all(
    shoot_folder: str | Path,
    rate: float = 4.0,
    force: bool = False,
    workers: int = DEFAULT_WORKERS,
) -> Iterator[dict[str, Any]]:
    """
    Analyse every video clip in the shoot. Yields:
      analyse_started, clip_started, clip_progress, clip_analysed,
      clip_skipped, clip_error, analyse_complete
    The sidecar is written before clip_analysed is yielded.
    """
    clips = classify.scan_video_clips(shoot_folder)
    yield {"event": "analyse_started", "total_clips": len(clips), "sampling_rate": rate}

    todo: list[str] = []
    skipped = 0
    for path in clips:
        if not force and _already_analysed(sidecar.read_clip(shoot_folder, path), rate, path):
            skipped += 1
            yield {"event": "clip_skipped", "file": path}
        else:
            todo.append(path)

    events: queue.Queue[dict[str, Any]] = queue.Queue()
    counts = {"analysed": 0, "failed": 0}
    lock = threading.Lock()

    stop = threading.Event()

    def run(path: str) -> None:
        if stop.is_set():
            return
        events.put({"event": "clip_started", "file": path})
        try:
            block = analyse_clip(
                path,
                rate,
                stop=stop,
                on_progress=lambda f: events.put(
                    {"event": "clip_progress", "file": path, "fraction": round(f, 3)}
                ),
            )
            sidecar.merge_clip(shoot_folder, path, updates={"selects": block}, new_stage="selects")
            with lock:
                counts["analysed"] += 1
            events.put(
                {
                    "event": "clip_analysed",
                    "file": path,
                    "samples": len(block["frames"]),
                    "duration_sec": block["duration_sec"],
                }
            )
        except AnalysisCancelledError:
            return
        except Exception as exc:  # one bad clip must not stop the shoot
            print(f"[selects.py] analysis failed for {path}: {exc}", file=sys.stderr)
            with lock:
                counts["failed"] += 1
            events.put({"event": "clip_error", "file": path, "reason": str(exc)[:300]})

    pool_size = max(1, min(workers, len(todo) or 1, os.cpu_count() or 1))
    pool = ThreadPoolExecutor(max_workers=pool_size)
    futures = [pool.submit(run, p) for p in todo]
    try:
        while not (all(f.done() for f in futures) and events.empty()):
            try:
                yield events.get(timeout=0.2)
            except queue.Empty:
                continue
    finally:
        # Normal end, or the consumer went away (bridge error, cancel): stop every
        # worker so no ffmpeg keeps decoding in the background.
        stop.set()
        pool.shutdown(wait=True, cancel_futures=True)

    _invalidate_cache(shoot_folder)
    yield {
        "event": "analyse_complete",
        "analysed": counts["analysed"],
        "skipped": skipped,
        "failed": counts["failed"],
        "total": len(clips),
    }


# ── Settings persistence ─────────────────────────────────────────────────────


def settings_path(shoot_folder: str | Path) -> Path:
    return sidecar.sidecar_root(shoot_folder) / SETTINGS_FILE


def load_settings(shoot_folder: str | Path) -> ss.SelectsSettings:
    return load_settings_with_warning(shoot_folder)[0]


def load_settings_with_warning(shoot_folder: str | Path) -> tuple[ss.SelectsSettings, str | None]:
    """Saved settings for the shoot. An unreadable file is kept aside, never silently replaced."""
    path = settings_path(shoot_folder)
    if not path.is_file():
        return ss.default_settings(), None
    try:
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict):
            raise ValueError("not a JSON object")
        return ss.settings_from_dict(raw), None
    except (OSError, ValueError, TypeError, AttributeError, OverflowError) as exc:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        backup = path.with_name(f"{path.name}.corrupt-{stamp}")
        try:
            path.replace(backup)
            kept = f" It was kept as {backup.name}."
        except OSError:
            kept = ""
        print(f"[selects.py] unreadable {path}: {exc}", file=sys.stderr)
        return (
            ss.default_settings(),
            f"Saved tuning for this shoot couldn't be read, so defaults are in use.{kept}",
        )


def save_settings(shoot_folder: str | Path, settings: ss.SelectsSettings) -> None:
    path = settings_path(shoot_folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ss.settings_to_dict(settings), indent=2))
    tmp.replace(path)


# ── Re-scoring (with an in-process measurement cache) ────────────────────────

_cache: OrderedDict[str, tuple[tuple[int, int], dict[str, Any] | None]] = OrderedDict()
_cache_lock = threading.Lock()


def _invalidate_cache(shoot_folder: str | Path) -> None:
    root = sidecar.sidecar_root(shoot_folder)
    with _cache_lock:
        for key in [k for k in _cache if Path(k).is_relative_to(root)]:
            del _cache[key]


def read_record(shoot_folder: str | Path, clip_path: str) -> dict[str, Any] | None:
    """Sidecar for a clip, cached by (mtime, size) so slider drags don't re-read JSON."""
    target = sidecar.sidecar_root(shoot_folder) / f"{sidecar.clip_id(clip_path)}.json"
    key = str(target)
    try:
        st = target.stat()
    except OSError:
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] == stamp:
            _cache.move_to_end(key)
            return hit[1]
    record = sidecar.read_clip(shoot_folder, clip_path)
    with _cache_lock:
        _cache[key] = (stamp, record)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_LIMIT:
            _cache.popitem(last=False)
    return record


def rescore(
    shoot_folder: str | Path,
    settings: ss.SelectsSettings | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Score every clip from stored measurements. Returns clips + summary + settings used."""
    warnings: list[str] = []
    if settings is None:
        settings, warning = load_settings_with_warning(shoot_folder)
        if warning:
            warnings.append(warning)
    if persist:
        try:
            save_settings(shoot_folder, settings)
        except OSError as exc:
            warnings.append(f"Couldn't save the tuning for this shoot: {exc}")

    clips_out: list[dict[str, Any]] = []
    unreadable: list[str] = []
    for path in classify.scan_video_clips(shoot_folder):
        record = read_record(shoot_folder, path)
        if record is None and sidecar.is_unreadable(shoot_folder, path):
            unreadable.append(Path(path).name)
        record = record or {}
        block = record.get("selects") if "selects" in record.get("stages_complete", []) else None
        base = {
            "file": path,
            "name": Path(path).name,
            "classification": record.get("classification"),
            "ratings": record.get("select_ratings", {}),
        }
        if not block:
            clips_out.append(
                {
                    **base,
                    "analysed": False,
                    "duration_sec": 0.0,
                    "segments": [],
                    "near_misses": [],
                    "scores": [],
                }
            )
            continue
        scored = ss.score_clip(block, settings)
        clips_out.append(
            {
                **base,
                "analysed": True,
                "duration_sec": block.get("duration_sec", 0.0),
                "fps": block.get("fps"),
                "sampling_rate": block.get("sampling_rate"),
                "scores": scored["scores"],
                "segments": scored["segments"],
                "near_misses": ss.near_misses(block, settings, scored["segments"]),
                "sample_count": scored["sample_count"],
                "qualifying_count": scored["qualifying_count"],
            }
        )
    if unreadable:
        shown = ", ".join(unreadable[:5]) + ("…" if len(unreadable) > 5 else "")
        warnings.append(
            f"{len(unreadable)} clip record{'s' if len(unreadable) != 1 else ''} couldn't be read "
            f"({shown}); those clips show as not analysed. Analysing again rebuilds them and keeps "
            "the damaged file as .corrupt."
        )
    return {
        "settings": ss.settings_to_dict(settings),
        "clips": clips_out,
        "summary": ss.summarise(clips_out),
        "warnings": warnings,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: selects.py <shoot_folder> [rate]", file=sys.stderr)
        sys.exit(1)
    for ev in analyse_all(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 4.0):
        print(json.dumps(ev))
