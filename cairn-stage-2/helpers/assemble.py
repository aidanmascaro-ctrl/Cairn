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
    clip_id: str  # MCP media pool item id
    src_in_sec: float  # in-point within the source clip (seconds)
    src_out_sec: float  # out-point within the source clip (seconds)
    fps: float


@dataclass(frozen=True)
class MarkInOut:
    clip_id: str
    mark_in: int  # inclusive source frame
    mark_out: int  # inclusive source frame


def soundbite_to_mark(sb: Soundbite) -> MarkInOut:
    if sb.src_out_sec <= sb.src_in_sec:
        raise ValueError("src_out_sec must be greater than src_in_sec")
    mark_in = seconds_to_frames(sb.src_in_sec, sb.fps)
    mark_out = seconds_to_frames(sb.src_out_sec, sb.fps) - 1
    if mark_out < mark_in:
        # Sub-frame soundbite: in/out round to the same (or adjacent) frame, so
        # the inclusive mark_out lands before mark_in. Too short to place.
        raise ValueError(
            f"soundbite spans less than one frame at {sb.fps}fps "
            f"({sb.src_in_sec}s -> {sb.src_out_sec}s)"
        )
    return MarkInOut(clip_id=sb.clip_id, mark_in=mark_in, mark_out=mark_out)


def assembly_plan(soundbites: list[Soundbite]) -> list[dict]:
    plan: list[dict] = []
    for sb in soundbites:
        m = soundbite_to_mark(sb)
        plan.append(
            {
                "set_mark": {
                    "clip_id": m.clip_id,
                    "mark_in": m.mark_in,
                    "mark_out": m.mark_out,
                    "type": "all",
                },
                "append": {"clip_id": m.clip_id},
            }
        )
    return plan
