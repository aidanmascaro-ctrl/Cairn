"""
Stage 4 — Index B-roll visually.

For every B-ROLL clip whose sidecar does not yet contain "vision" in stages_complete:
  1. Extract 5 frames via ffmpeg (0%, 25%, 50%, 75%, 100% of duration).
  2. Encode frames as base64 JPEG and call Claude with the tag_broll tool.
  3. Run an LLM-as-judge pass on a 10% random sample to score tag quality.
  4. Write structured tags to sidecar; flatten to Resolve Keywords string.
  5. Clean up frames unless CAIRN_KEEP_FRAMES env var is set.

cloud_allowed=False: local vision fallback is deferred to v2 (see fix_plan.md).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import math
import os
import random
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import sidecar as sid

# ── Constants ──────────────────────────────────────────────────────────────────

PROVIDER_ANTHROPIC = "anthropic"
ANTHROPIC_MODEL = "claude-sonnet-4-6"
FRAMES_PER_CLIP = 5
SHORT_CLIP_THRESHOLD_SEC = 2.0
JUDGE_SAMPLE_RATE = 0.10
JUDGE_LOW_SCORE_THRESHOLD = 0.5
# Conservative cost estimate: 5 frames x ~1500 tokens + prompt + output ~= $0.03
ANTHROPIC_VISION_COST_PER_CLIP_USD: float = 0.03

_VIDEO_EXTS: set[str] = {".mp4", ".mov", ".mxf", ".avi", ".m2ts"}

# ── ffmpeg / ffprobe paths ─────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_FFMPEG_PATH: Path = _PROJECT_ROOT / "assets" / "bin" / "ffmpeg"
if not _FFMPEG_PATH.exists():
    _FFMPEG_PATH = Path("ffmpeg")

_FFPROBE_PATH: Path = _PROJECT_ROOT / "assets" / "bin" / "ffprobe"
if not _FFPROBE_PATH.exists():
    _FFPROBE_PATH = Path("ffprobe")

# ── Tool definitions ───────────────────────────────────────────────────────────

_TAG_BROLL_TOOL: dict[str, Any] = {
    "name": "tag_broll",
    "description": "Record structured visual tags for a B-roll clip.",
    "input_schema": {
        "type": "object",
        "properties": {
            "subjects": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concrete nouns visible (prefer specific over generic)",
            },
            "actions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Verbs/gerunds for motion visible between frames",
            },
            "shot_type": {
                "type": "string",
                "enum": ["wide", "medium", "close-up", "macro", "aerial", "handheld-POV"],
            },
            "mood": {
                "type": "string",
                "description": "1-2 words for emotional register",
            },
            "environment": {
                "type": "string",
                "description": "Where this takes place",
            },
            "colour_palette": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-4 dominant colours, evocative names",
            },
            "time_of_day": {
                "type": "string",
                "enum": [
                    "morning",
                    "midday",
                    "afternoon",
                    "golden-hour",
                    "evening",
                    "night",
                    "indoor",
                    "unclear",
                ],
            },
            "lighting_style": {
                "type": "string",
                "description": "Short phrase describing lighting",
            },
            "camera_motion": {
                "type": "string",
                "enum": [
                    "static tripod",
                    "handheld",
                    "slider",
                    "gimbal",
                    "whip-pan",
                    "unclear",
                ],
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Overall tagging confidence 0-1",
            },
        },
        "required": [
            "subjects",
            "actions",
            "shot_type",
            "mood",
            "environment",
            "colour_palette",
            "time_of_day",
            "lighting_style",
            "camera_motion",
            "confidence",
        ],
    },
}

_JUDGE_TAGS_TOOL: dict[str, Any] = {
    "name": "judge_tags",
    "description": "Score the accuracy of previously assigned visual tags by re-examining the frames.",
    "input_schema": {
        "type": "object",
        "properties": {
            "subjects_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "actions_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "shot_type_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "mood_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "overall_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "low_confidence_fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Field names scored below 0.5",
            },
            "notes": {
                "type": "string",
                "description": "Brief explanation of any low scores",
            },
        },
        "required": [
            "subjects_score",
            "actions_score",
            "shot_type_score",
            "mood_score",
            "overall_score",
            "low_confidence_fields",
        ],
    },
}

# ── Errors ─────────────────────────────────────────────────────────────────────


class VisionTaggingError(Exception):
    """Raised when Claude does not return a valid tag_broll tool call."""


class FrameExtractionError(Exception):
    """Raised when ffmpeg cannot extract frames from a clip."""


# ── Duration probe ─────────────────────────────────────────────────────────────


def get_clip_duration_sec(clip_path: str) -> float:
    """Return clip duration in seconds, or 0.0 on failure."""
    try:
        result = subprocess.run(
            [
                str(_FFPROBE_PATH),
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                clip_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            dur = float(data.get("format", {}).get("duration", 0))
            if dur > 0:
                return dur
    except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, OSError):
        pass

    # Fallback: parse stderr from ffmpeg
    try:
        result = subprocess.run(
            [str(_FFMPEG_PATH), "-i", clip_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        for line in result.stderr.splitlines():
            if "Duration:" in line:
                dur_str = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = dur_str.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass

    return 0.0


# ── Frame extraction ──────────────────────────────────────────────────────────


def _frame_timestamps(duration: float) -> list[float]:
    """Return frame timestamps in seconds for a clip of the given duration."""
    if duration < SHORT_CLIP_THRESHOLD_SEC:
        # 3 frames for very short clips
        return [0.0, duration / 2, max(0.0, duration - 0.1)]
    pcts = [0.0, 0.25, 0.5, 0.75, 1.0]
    return [pct * duration if pct < 1.0 else max(0.0, duration - 0.1) for pct in pcts]


def extract_frames(clip_path: str, frames_dir: Path) -> list[str]:
    """
    Extract representative frames from clip_path into frames_dir.
    Returns list of absolute frame file paths.
    Raises FrameExtractionError on ffmpeg failure.
    """
    frames_dir.mkdir(parents=True, exist_ok=True)
    duration = get_clip_duration_sec(clip_path)
    if duration <= 0:
        raise FrameExtractionError(f"Could not determine duration of {clip_path}")

    timestamps = _frame_timestamps(duration)
    frame_paths: list[str] = []

    for i, ts in enumerate(timestamps):
        out_path = frames_dir / f"{i + 1:02d}.jpg"
        cmd = [
            str(_FFMPEG_PATH),
            "-y",
            "-ss",
            f"{ts:.3f}",
            "-i",
            clip_path,
            "-frames:v",
            "1",
            "-q:v",
            "3",
            "-update",
            "1",
            str(out_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired as exc:
            raise FrameExtractionError(f"ffmpeg timed out extracting frame at {ts:.1f}s") from exc

        # If end-frame extraction failed, step back 0.1 s and retry once
        if result.returncode != 0 and i == len(timestamps) - 1:
            fallback_ts = max(0.0, ts - 0.1)
            cmd[4] = f"{fallback_ts:.3f}"
            result = subprocess.run(cmd, capture_output=True, timeout=60)

        if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            raise FrameExtractionError(
                f"ffmpeg failed for {clip_path} at {ts:.1f}s: "
                f"{result.stderr.decode(errors='replace')[-200:]}"
            )
        frame_paths.append(str(out_path))

    return frame_paths


def _read_prompt() -> str:
    """Load the vision_tag.md prompt from the prompts directory."""
    prompt_path = (
        Path(__file__).resolve().parent.parent / "orchestrator" / "prompts" / "vision_tag.md"
    )
    if prompt_path.exists():
        return prompt_path.read_text()
    # Fallback inline (should not happen in normal use)
    return (
        "Tag this B-roll clip. Call the tag_broll tool with structured visual tags "
        "grounded in the provided frames."
    )


# ── Claude API calls ───────────────────────────────────────────────────────────


def _frame_to_base64(frame_path: str) -> str:
    return base64.standard_b64encode(Path(frame_path).read_bytes()).decode()


def tag_broll_with_claude(frame_paths: list[str], api_key: str) -> dict[str, Any]:
    """
    Send frames to Claude and return the tag_broll tool input dict.
    Raises VisionTaggingError if Claude does not call the tool.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    prompt_text = _read_prompt()

    content: list[dict[str, Any]] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": _frame_to_base64(fp),
            },
        }
        for fp in frame_paths
    ]
    content.append({"type": "text", "text": prompt_text})

    response = client.messages.create(  # type: ignore[no-matching-overload]
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        tools=[_TAG_BROLL_TOOL],
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": content}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "tag_broll":
            return dict(block.input)  # type: ignore[return-value]

    raise VisionTaggingError("Claude did not call the tag_broll tool")


def judge_tags_with_claude(
    frame_paths: list[str],
    tags: dict[str, Any],
    api_key: str,
) -> dict[str, Any]:
    """
    LLM-as-judge: re-examine frames and score the previously assigned tags.
    Returns the judge_tags tool input dict.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    tags_summary = json.dumps(tags, indent=2)
    judge_prompt = (
        "You previously tagged this B-roll clip. Here are the frames again and the tags assigned.\n"
        f"Tags:\n{tags_summary}\n\n"
        "Call the judge_tags tool to score how accurately each tag field describes "
        "the visible content. Score 0.0-1.0 per field. Flag fields below 0.5 in "
        "low_confidence_fields."
    )

    content: list[dict[str, Any]] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": _frame_to_base64(fp),
            },
        }
        for fp in frame_paths
    ]
    content.append({"type": "text", "text": judge_prompt})

    response = client.messages.create(  # type: ignore[no-matching-overload]
        model=ANTHROPIC_MODEL,
        max_tokens=512,
        tools=[_JUDGE_TAGS_TOOL],
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": content}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "judge_tags":
            return dict(block.input)  # type: ignore[return-value]

    # Judge didn't call the tool — return a neutral result rather than crashing
    return {
        "subjects_score": 1.0,
        "actions_score": 1.0,
        "shot_type_score": 1.0,
        "mood_score": 1.0,
        "overall_score": 1.0,
        "low_confidence_fields": [],
    }


# ── Keywords formatter ─────────────────────────────────────────────────────────


def keywords_from_tags(tags: dict[str, Any]) -> str:
    """Flatten structured tags to a comma-joined Keywords string for Resolve."""
    subjects = list(tags.get("subjects", []))
    actions = list(tags.get("actions", []))[:2]
    shot_type = tags.get("shot_type", "")
    palette = list(tags.get("colour_palette", []))[:2]
    lighting = tags.get("lighting_style", "")
    lighting_word = lighting.split()[0] if lighting else ""

    parts = subjects + actions + [shot_type] + palette + [lighting_word]
    return ", ".join(p for p in parts if p)


# ── Clip scanning ──────────────────────────────────────────────────────────────


def scan_broll_clips(shoot_folder: str | Path) -> list[str]:
    """
    Return absolute paths of B-ROLL video clips that have not yet been vision-tagged.
    A clip qualifies if its sidecar has classification=B-ROLL and "vision" is NOT
    in stages_complete.
    """
    folder = Path(shoot_folder)
    clips: list[str] = []

    for video_dir in [
        folder / "01_VIDEOS" / "A_CAM",
        folder / "01_VIDEOS" / "B_CAM",
        folder / "01_VIDEOS" / "C_CAM",
    ]:
        if not video_dir.is_dir():
            continue
        for f in video_dir.iterdir():
            if not f.is_file() or f.suffix.lower() not in _VIDEO_EXTS:
                continue
            sc = sid.read_clip(shoot_folder, str(f))
            if sc is None:
                continue
            if sc.get("classification") != "B-ROLL":
                continue
            if "vision" in sc.get("stages_complete", []):
                continue
            clips.append(str(f))

    return sorted(clips)


# ── Cost estimate ──────────────────────────────────────────────────────────────


def estimate_vision(clip_paths: list[str]) -> dict[str, Any]:
    """Return clip count + estimated cost for cloud tagging."""
    count = len(clip_paths)
    cost = round(count * ANTHROPIC_VISION_COST_PER_CLIP_USD, 4)
    return {
        "clip_count": count,
        "estimated_cost_usd": cost,
    }


# ── Main tagging pipeline ──────────────────────────────────────────────────────


def tag_all(
    clip_paths: list[str],
    shoot_folder: str | Path,
    api_key: str,
    *,
    cloud_allowed: bool = True,
) -> Iterator[dict[str, Any]]:
    """
    Tag every B-roll clip. Yields progress events per clip, then vision_complete.

    Event types yielded:
      - vision_started       {total_clips}
      - clip_tagged          {file, tags, keywords, resolve_ok=None}
      - clip_skipped         {file, reason}
      - clip_error           {file, reason}
      - judge_flagged        {file, judge, low_fields}
      - vision_complete      {total, tagged, skipped, errors, judge_samples}
    """
    if not cloud_allowed:
        # Local vision fallback not yet implemented in v1
        yield {
            "event": "vision_complete",
            "total": len(clip_paths),
            "tagged": 0,
            "skipped": len(clip_paths),
            "errors": 0,
            "judge_samples": 0,
            "reason": "Local vision not available in v1 — enable cloud vision or see fix_plan.md",
        }
        return

    total = len(clip_paths)
    yield {"event": "vision_started", "total_clips": total}

    tagged: list[str] = []
    skipped = 0
    errors = 0
    tagged_frames: dict[str, tuple[list[str], dict[str, Any]]] = {}  # clip_path → (frames, tags)
    keep_frames = bool(os.environ.get("CAIRN_KEEP_FRAMES"))

    frames_base = sid.sidecar_root(shoot_folder) / "frames"

    for clip_path in clip_paths:
        clip_id = sid.clip_id(clip_path)
        frames_dir = frames_base / clip_id

        # Idempotency: skip if already tagged with same model
        sc = sid.read_clip(shoot_folder, clip_path)
        if sc is not None:
            vision = sc.get("vision", {})
            if (
                "vision" in sc.get("stages_complete", [])
                and vision.get("provider_model") == ANTHROPIC_MODEL
            ):
                yield {"event": "clip_skipped", "file": clip_path, "reason": "already tagged"}
                skipped += 1
                continue

        frame_paths: list[str] = []
        try:
            frame_paths = extract_frames(clip_path, frames_dir)
        except FrameExtractionError as exc:
            yield {"event": "clip_error", "file": clip_path, "reason": str(exc)}
            errors += 1
            continue

        try:
            tags = tag_broll_with_claude(frame_paths, api_key)
        except Exception as exc:
            yield {"event": "clip_error", "file": clip_path, "reason": str(exc)}
            errors += 1
            if not keep_frames:
                _cleanup_frames(frame_paths)
            continue

        # Build sidecar vision block
        raw_sha = hashlib.sha256(json.dumps(tags, sort_keys=True).encode()).hexdigest()
        duration = get_clip_duration_sec(clip_path)
        timestamps = _frame_timestamps(duration)
        vision_block: dict[str, Any] = {
            "provider": PROVIDER_ANTHROPIC,
            "provider_model": ANTHROPIC_MODEL,
            "frame_times": [round(t, 3) for t in timestamps],
            "tags": tags,
            "raw_response_sha": raw_sha,
        }
        if duration > 600:
            vision_block["sampling_note"] = "long-clip-sparse"

        sid.merge_clip(
            shoot_folder,
            clip_path,
            updates={"vision": vision_block},
            new_stage="vision",
        )

        keywords = keywords_from_tags(tags)
        tagged.append(clip_path)
        tagged_frames[clip_path] = (frame_paths, tags)

        yield {
            "event": "clip_tagged",
            "file": clip_path,
            "tags": tags,
            "keywords": keywords,
        }

        if not keep_frames:
            _cleanup_frames(frame_paths)

    # ── LLM-as-judge: sample 10% of tagged clips ──────────────────────────────
    judge_samples = 0
    if tagged and api_key:
        sample_size = max(1, math.ceil(len(tagged) * JUDGE_SAMPLE_RATE))
        sample = random.sample(tagged, min(sample_size, len(tagged)))

        for clip_path in sample:
            judge_samples += 1
            frame_paths, tags = tagged_frames.get(clip_path, ([], {}))

            # Re-extract frames if they were cleaned up
            if not frame_paths or not all(Path(fp).exists() for fp in frame_paths):
                try:
                    clip_id = sid.clip_id(clip_path)
                    frames_dir = frames_base / clip_id
                    frame_paths = extract_frames(clip_path, frames_dir)
                    cleanup_after_judge = not keep_frames
                except FrameExtractionError:
                    continue
            else:
                cleanup_after_judge = False

            try:
                judge = judge_tags_with_claude(frame_paths, tags, api_key)
            except Exception:
                if cleanup_after_judge:
                    _cleanup_frames(frame_paths)
                continue

            if cleanup_after_judge:
                _cleanup_frames(frame_paths)

            low_fields = judge.get("low_confidence_fields", [])
            overall = judge.get("overall_score", 1.0)
            if low_fields or overall < JUDGE_LOW_SCORE_THRESHOLD:
                yield {
                    "event": "judge_flagged",
                    "file": clip_path,
                    "judge": judge,
                    "low_fields": low_fields,
                }

    yield {
        "event": "vision_complete",
        "total": total,
        "tagged": len(tagged),
        "skipped": skipped,
        "errors": errors,
        "judge_samples": judge_samples,
    }


def _cleanup_frames(frame_paths: list[str]) -> None:
    """Delete frame files. Non-fatal on error."""
    for fp in frame_paths:
        with contextlib.suppress(OSError):
            Path(fp).unlink(missing_ok=True)
    if frame_paths:
        parent = Path(frame_paths[0]).parent
        with contextlib.suppress(OSError):
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
