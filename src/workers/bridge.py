#!/usr/bin/env python3
"""
Cairn Python worker — JSON-per-line bridge.

Reads one JSON object per line from stdin, dispatches to a handler by the
'type' field, writes one or more JSON responses per line to stdout. All other
logging goes to stderr so it doesn't pollute the protocol stream.

Two handler types:
  HANDLERS           — return a single dict (one response)
  STREAMING_HANDLERS — are generators that yield dicts (multiple responses)
                       The final yielded dict must have event == "complete".

The Node side (src/orchestrator/python-bridge.ts) is the sole caller.
"""

from __future__ import annotations

import json
import platform
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import brief as brief_worker
import classify as classify_worker
import ingest as ingest_worker
import matching as matching_worker
import resolve_api
import sidecar
import soundbites as soundbites_worker
import transcribe as transcribe_worker
import vision as vision_worker

# ── Single-response handlers ──────────────────────────────────────────────────


def handle_ping(msg: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": msg["id"],
        "type": "pong",
        "python_version": platform.python_version(),
        "timestamp_iso": _now(),
    }


def handle_echo(msg: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": msg["id"],
        "type": "echo_response",
        "payload": msg["payload"],
        "timestamp_iso": _now(),
    }


def handle_classify_confirm(msg: dict[str, Any]) -> dict[str, Any]:
    """
    Persist a user-confirmed classification override for one clip.
    Updates the sidecar JSON, then writes Keywords + clip colour to Resolve.
    """
    payload = msg.get("payload", {})
    clip_path: str = payload["clip_path"]
    label: str = payload["label"]
    shoot_folder: str = payload["shoot_folder"]

    import sidecar as sid

    sid.merge_clip(
        shoot_folder,
        clip_path,
        updates={"classification": label, "user_confirmed": True},
        new_stage=None,
    )

    resolve_ok = False
    try:
        resolve_ok = resolve_api.classify_write_resolve(clip_path, label)
    except Exception as exc:
        print(
            f"[bridge.py] classify_confirm resolve write failed: {exc}",
            file=sys.stderr,
        )

    return {
        "id": msg["id"],
        "type": "classify_confirm_ok",
        "clip_path": clip_path,
        "label": label,
        "resolve_ok": resolve_ok,
    }


def handle_transcribe_estimate(msg: dict[str, Any]) -> dict[str, Any]:
    """
    Return clip count + estimated audio minutes for the shoot folder.
    Used by the UI to show cost estimate before starting transcription.
    """
    payload = msg.get("payload", {})
    path: str = payload["path"]

    clips = transcribe_worker.scan_aroll_clips(path)
    minutes = transcribe_worker.estimate_minutes(clips)

    return {
        "id": msg["id"],
        "type": "transcribe_estimate_result",
        "clip_count": len(clips),
        "estimated_minutes": minutes,
        "estimated_cost_usd": round(minutes * transcribe_worker.ASSEMBLYAI_COST_PER_MIN_USD, 4),
    }


def handle_update_speaker_labels(msg: dict[str, Any]) -> dict[str, Any]:
    """
    Persist user-assigned speaker names into all transcribed clip sidecars.
    payload: { shoot_folder: str, speaker_labels: {id: name, ...} }
    """
    payload = msg.get("payload", {})
    shoot_folder: str = payload["shoot_folder"]
    speaker_labels: dict[str, str] = payload.get("speaker_labels", {})

    updated = transcribe_worker.update_speaker_labels(shoot_folder, speaker_labels)

    return {
        "id": msg["id"],
        "type": "speaker_labels_updated",
        "updated_count": updated,
    }


def handle_brief_get(msg: dict[str, Any]) -> dict[str, Any]:
    """
    Read the existing brief.json for a shoot folder.
    payload: { path: str }
    Returns: { brief: dict | null, fields: dict }
    """
    payload = msg.get("payload", {})
    path: str = payload["path"]

    data = brief_worker.read_brief(path)
    fields = (
        brief_worker.count_fields(data)
        if data is not None
        else {"filled": 0, "total": 8, "required_filled": 0, "required_total": 5}
    )

    return {"id": msg["id"], "type": "brief_get_result", "brief": data, "fields": fields}


def handle_brief_message(msg: dict[str, Any]) -> dict[str, Any]:
    """
    Send a user message to Claude and get back the assistant reply + updated draft.
    payload: { path: str, message: str, api_key: str }
    """
    payload = msg.get("payload", {})
    path: str = payload["path"]
    message: str = payload["message"]
    api_key: str = payload.get("api_key", "")

    result = brief_worker.send_message(path, message, api_key)

    return {"id": msg["id"], "type": "brief_message_result", **result}


def handle_brief_save(msg: dict[str, Any]) -> dict[str, Any]:
    """
    Persist manual edits from the UI form into brief.json.
    payload: { path: str, updates: dict }
    """
    payload = msg.get("payload", {})
    path: str = payload["path"]
    updates: dict[str, Any] = payload.get("updates", {})

    updated = brief_worker.save_brief(path, updates)
    fields = brief_worker.count_fields(updated)

    return {"id": msg["id"], "type": "brief_save_result", "brief": updated, "fields": fields}


def handle_brief_reset(msg: dict[str, Any]) -> dict[str, Any]:
    """
    Delete brief.json for a shoot folder (start over).
    payload: { path: str }
    """
    payload = msg.get("payload", {})
    path: str = payload["path"]

    brief_worker.delete_brief(path)

    return {"id": msg["id"], "type": "brief_reset_result", "ok": True}


def handle_vision_estimate(msg: dict[str, Any]) -> dict[str, Any]:
    """
    Return B-ROLL clip count + estimated Claude vision cost for the shoot folder.
    payload: { path: str }
    """
    payload = msg.get("payload", {})
    path: str = payload["path"]

    clips = vision_worker.scan_broll_clips(path)
    estimate = vision_worker.estimate_vision(clips)

    return {
        "id": msg["id"],
        "type": "vision_estimate_result",
        **estimate,
    }


def handle_soundbites_estimate(msg: dict[str, Any]) -> dict[str, Any]:
    """
    Return A-roll clip count + estimated Claude cost for soundbite selection.
    payload: { path: str }
    """
    payload = msg.get("payload", {})
    path: str = payload["path"]

    clips = soundbites_worker.scan_aroll_clips(path)
    estimate = soundbites_worker.estimate_soundbites(clips)

    return {
        "id": msg["id"],
        "type": "soundbites_estimate_result",
        **estimate,
    }


def handle_matching_estimate(msg: dict[str, Any]) -> dict[str, Any]:
    """
    Return soundbite count + B-roll count + estimated Claude cost for matching.
    payload: { path: str }
    """
    payload = msg.get("payload", {})
    path: str = payload["path"]

    library = matching_worker.build_broll_library(path)
    aroll_clips = matching_worker.scan_aroll_for_matching(path)
    estimate = matching_worker.estimate_matching(aroll_clips, library)

    return {
        "id": msg["id"],
        "type": "matching_estimate_result",
        **estimate,
    }


HANDLERS: dict[str, Any] = {
    "ping": handle_ping,
    "echo": handle_echo,
    "classify_confirm": handle_classify_confirm,
    "transcribe_estimate": handle_transcribe_estimate,
    "update_speaker_labels": handle_update_speaker_labels,
    "vision_estimate": handle_vision_estimate,
    "soundbites_estimate": handle_soundbites_estimate,
    "matching_estimate": handle_matching_estimate,
    "brief_get": handle_brief_get,
    "brief_message": handle_brief_message,
    "brief_save": handle_brief_save,
    "brief_reset": handle_brief_reset,
}


# ── Streaming handlers ────────────────────────────────────────────────────────


def handle_ingest(msg: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """
    Runs the full ingest pipeline for one shoot folder.

    Expected payload keys:
      path          — absolute path to shoot folder (required)
      name          — shoot name override (optional)
      dry_run       — bool, default false
      write_sidecar — bool, default true (skipped in dry_run)
      resolve       — bool, default true (skipped in dry_run)

    Yields progress events, then a final ingest_complete message.
    """
    payload = msg.get("payload", {})
    msg_id: str = msg["id"]

    path: str = payload["path"]
    name_override: str | None = payload.get("name")
    dry_run: bool = bool(payload.get("dry_run", False))
    do_sidecar: bool = bool(payload.get("write_sidecar", True)) and not dry_run
    do_resolve: bool = bool(payload.get("resolve", True)) and not dry_run

    result: dict[str, Any] = {}

    for event in ingest_worker.run(path, name_override=name_override, dry_run=dry_run):
        if event.get("event") == "complete":
            result = event
        else:
            yield {"id": msg_id, "type": "ingest_progress", **event}

    if not result:
        yield {
            "id": msg_id,
            "type": "error",
            "message": "ingest.run() completed without a 'complete' event",
        }
        return

    # ── Sidecar write (before Resolve) ────────────────────────────────────────
    if do_sidecar:
        shoot_folder = result["folder"]
        clip_paths = result.get("clip_paths", {})
        metadata = result.get("metadata", {})

        for clip_path in clip_paths.get("a_cam", []):
            sidecar.write_clip(
                shoot_folder=shoot_folder,
                absolute_path=clip_path,
                bin_path="01_VIDEOS/A_CAM",
                kind="video",
                metadata=metadata,
            )
        for clip_path in clip_paths.get("c_cam", []):
            sidecar.write_clip(
                shoot_folder=shoot_folder,
                absolute_path=clip_path,
                bin_path="01_VIDEOS/C_CAM",
                kind="video",
                metadata=metadata,
            )
        for clip_path in clip_paths.get("photos", []):
            sidecar.write_clip(
                shoot_folder=shoot_folder,
                absolute_path=clip_path,
                bin_path="02_PHOTOS",
                kind="photo",
                metadata=metadata,
            )

        sidecar_count = (
            len(clip_paths.get("a_cam", []))
            + len(clip_paths.get("c_cam", []))
            + len(clip_paths.get("photos", []))
        )
        yield {
            "id": msg_id,
            "type": "ingest_progress",
            "event": "sidecars_written",
            "count": sidecar_count,
        }

    # ── Resolve import ────────────────────────────────────────────────────────
    resolve_counts: dict[str, int] = {}
    if do_resolve:
        try:
            resolve_counts = resolve_api.ingest_into_resolve(
                shoot_folder=result["folder"],
                clip_paths=result.get("clip_paths", {}),
                metadata=result.get("metadata", {}),
            )
            yield {
                "id": msg_id,
                "type": "ingest_progress",
                "event": "resolve_imported",
                **resolve_counts,
            }
        except resolve_api.ResolveUnavailableError as exc:
            yield {
                "id": msg_id,
                "type": "ingest_progress",
                "event": "resolve_skipped",
                "reason": str(exc),
            }

    yield {
        "id": msg_id,
        "type": "ingest_complete",
        "folder": result.get("folder", path),
        "files_moved": result.get("files_moved", 0),
        "shells_removed": result.get("shells_removed", 0),
        "metadata": result.get("metadata", {}),
        "clip_paths": result.get("clip_paths", {}),
        "resolve_counts": resolve_counts,
        "dry_run": dry_run,
        "timestamp_iso": _now(),
    }


def handle_classify(msg: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """
    Run VAD classification on every video clip in a shoot folder.

    Expected payload keys:
      path    -- absolute path to the organised shoot folder (required)
      dry_run -- bool, default false (skip Resolve writes when true)

    Yields classify_progress events per clip, then classify_complete.
    """
    payload = msg.get("payload", {})
    msg_id: str = msg["id"]

    path: str = payload["path"]
    dry_run: bool = bool(payload.get("dry_run", False))

    clip_paths = classify_worker.scan_video_clips(path)
    total = len(clip_paths)

    yield {
        "id": msg_id,
        "type": "classify_progress",
        "event": "classify_started",
        "total_clips": total,
    }

    for event in classify_worker.classify_all(clip_paths, path):
        if event.get("event") == "classify_complete":
            yield {"id": msg_id, "type": "classify_complete", **event}
            return

        resolve_ok: bool | None = None
        if not dry_run and event.get("event") == "clip_classified":
            try:
                resolve_ok = resolve_api.classify_write_resolve(event["file"], event["label"])
            except Exception as exc:
                resolve_ok = False
                print(
                    f"[bridge.py] Resolve write failed for {event['file']}: {exc}",
                    file=sys.stderr,
                )

        yield {"id": msg_id, "type": "classify_progress", "resolve_ok": resolve_ok, **event}


def handle_transcribe(msg: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """
    Transcribe all A-ROLL clips in a shoot folder.

    Expected payload keys:
      path          -- absolute path to the organised shoot folder (required)
      api_key       -- AssemblyAI API key (required when cloud_allowed=True)
      cloud_allowed -- bool, default True (False → faster-whisper local)
      word_boost    -- list[str], optional project-specific vocabulary hints
      dry_run       -- bool, default False (skip Resolve subtitle attach when True)

    Yields transcribe_progress events per clip, then transcribe_complete.
    """
    payload = msg.get("payload", {})
    msg_id: str = msg["id"]

    path: str = payload["path"]
    api_key: str = payload.get("api_key", "")
    cloud_allowed: bool = bool(payload.get("cloud_allowed", True))
    word_boost: list[str] | None = payload.get("word_boost")
    dry_run: bool = bool(payload.get("dry_run", False))

    clip_paths = transcribe_worker.scan_aroll_clips(path)

    for event in transcribe_worker.transcribe_all(
        clip_paths, path, api_key, cloud_allowed, word_boost
    ):
        if event.get("event") == "transcribe_complete":
            yield {"id": msg_id, "type": "transcribe_complete", **event}
            return

        resolve_ok: bool | None = None
        if not dry_run and event.get("event") == "clip_transcribed":
            srt_path = event.get("srt_path", "")
            if srt_path:
                try:
                    resolve_ok = resolve_api.add_subtitle_track(event["file"], srt_path)
                except Exception as exc:
                    resolve_ok = False
                    print(
                        f"[bridge.py] subtitle attach failed for {event['file']}: {exc}",
                        file=sys.stderr,
                    )

        yield {"id": msg_id, "type": "transcribe_progress", "resolve_ok": resolve_ok, **event}


def handle_vision(msg: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """
    Tag all B-ROLL clips in a shoot folder with Claude vision.

    Expected payload keys:
      path          -- absolute path to organised shoot folder (required)
      api_key       -- Anthropic API key (required when cloud_allowed=True)
      cloud_allowed -- bool, default True (False → local fallback, not yet in v1)
      dry_run       -- bool, default False (skip Resolve Keywords write when True)

    Yields vision_progress events per clip, then vision_complete.
    """
    payload = msg.get("payload", {})
    msg_id: str = msg["id"]

    path: str = payload["path"]
    api_key: str = payload.get("api_key", "")
    cloud_allowed: bool = bool(payload.get("cloud_allowed", True))
    dry_run: bool = bool(payload.get("dry_run", False))

    clip_paths = vision_worker.scan_broll_clips(path)

    for event in vision_worker.tag_all(clip_paths, path, api_key, cloud_allowed=cloud_allowed):
        if event.get("event") == "vision_complete":
            yield {"id": msg_id, "type": "vision_complete", **event}
            return

        resolve_ok: bool | None = None
        if not dry_run and event.get("event") == "clip_tagged":
            keywords = event.get("keywords", "")
            if keywords:
                try:
                    resolve_ok = resolve_api.set_keywords(event["file"], keywords)
                except Exception as exc:
                    resolve_ok = False
                    print(
                        f"[bridge.py] Keywords write failed for {event['file']}: {exc}",
                        file=sys.stderr,
                    )

        yield {"id": msg_id, "type": "vision_progress", "resolve_ok": resolve_ok, **event}


def handle_soundbites(msg: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """
    Identify soundbites for all A-roll clips in a shoot folder.

    Expected payload keys:
      path      -- absolute path to organised shoot folder (required)
      api_key   -- Anthropic API key (required)
      dry_run   -- bool, default False (skip Resolve marker writes when True)

    Yields soundbites_progress events per clip, then soundbites_complete.
    """
    payload = msg.get("payload", {})
    msg_id: str = msg["id"]

    path: str = payload["path"]
    api_key: str = payload.get("api_key", "")
    dry_run: bool = bool(payload.get("dry_run", False))

    clip_paths = soundbites_worker.scan_aroll_clips(path)

    for event in soundbites_worker.process_all(clip_paths, path, api_key):
        if event.get("event") == "soundbites_complete":
            yield {"id": msg_id, "type": "soundbites_complete", **event}
            return

        if event.get("event") == "error":
            yield {"id": msg_id, "type": "error", "message": event.get("reason", "Unknown error")}
            return

        resolve_ok: bool | None = None
        if not dry_run and event.get("event") == "clip_processed":
            soundbites_list = event.get("soundbites", [])
            clip_file = event.get("file", "")
            if soundbites_list and clip_file:
                try:
                    # Clear existing Cairn markers before writing new ones
                    resolve_api.clear_cairn_markers(clip_file)
                    all_ok = True
                    for bite in soundbites_list:
                        ok = resolve_api.add_clip_marker(
                            clip_path=clip_file,
                            frame_id=bite["in_frame"],
                            color=bite["marker_color"],
                            name=bite["label"],
                            note=soundbites_worker.render_marker_note(bite),
                            duration=max(1, bite["out_frame"] - bite["in_frame"]),
                            custom_data=f"cairn:{bite['id']}",
                        )
                        if not ok:
                            all_ok = False
                    resolve_ok = all_ok
                except Exception as exc:
                    resolve_ok = False
                    print(
                        f"[bridge.py] Resolve marker write failed for {clip_file}: {exc}",
                        file=sys.stderr,
                    )

        yield {"id": msg_id, "type": "soundbites_progress", "resolve_ok": resolve_ok, **event}


def handle_matching(msg: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """
    Match B-roll clips to soundbites for a shoot folder.

    Expected payload keys:
      path      -- absolute path to organised shoot folder (required)
      api_key   -- Anthropic API key (required)
      dry_run   -- bool, default False (skip Resolve metadata writes when True)

    Yields matching_progress events per soundbite, then matching_complete.
    """
    payload = msg.get("payload", {})
    msg_id: str = msg["id"]

    path: str = payload["path"]
    api_key: str = payload.get("api_key", "")
    dry_run: bool = bool(payload.get("dry_run", False))

    for event in matching_worker.process_all(path, api_key):
        if event.get("event") == "matching_complete":
            if not dry_run:
                # Write B-roll Shot + Description fields to Resolve
                # Re-read sidecars to get final aggregated match data
                from sidecar import sidecar_root

                root = sidecar_root(path)
                if root.exists():
                    for scar_file in root.glob("*.json"):
                        if scar_file.name == "brief.json":
                            continue
                        try:
                            import json as _json

                            sc = _json.loads(scar_file.read_text())
                        except Exception:
                            continue
                        if sc.get("classification") != "B-ROLL":
                            continue
                        matches = sc.get("matches", [])
                        if not matches:
                            continue
                        clip_file = sc.get("source_path", "")
                        if not clip_file:
                            continue
                        shot_str = matching_worker._compute_shot_string(matches)
                        desc_str = matching_worker._compute_description_string(matches)
                        try:
                            resolve_api.set_shot_field(clip_file, shot_str)
                            resolve_api.set_description_field(clip_file, desc_str)
                        except Exception as exc:
                            print(
                                f"[bridge.py] Resolve B-roll metadata write failed for {clip_file}: {exc}",
                                file=sys.stderr,
                            )

                # Re-read A-roll sidecars that now have matching stage
                if root.exists():
                    for scar_file in root.glob("*.json"):
                        if scar_file.name == "brief.json":
                            continue
                        try:
                            import json as _json

                            sc = _json.loads(scar_file.read_text())
                        except Exception:
                            continue
                        if sc.get("classification") != "A-ROLL":
                            continue
                        if "matching" not in sc.get("stages_complete", []):
                            continue
                        clip_file = sc.get("source_path", "")
                        if not clip_file:
                            continue
                        for bite in sc.get("soundbites", []):
                            bite_matches = bite.get("matches", [])
                            if not bite_matches:
                                continue
                            frame_id = bite.get("in_frame")
                            if frame_id is None:
                                continue
                            matches_line = matching_worker._matches_line_for_marker(bite_matches)
                            try:
                                resolve_api.update_marker_note(
                                    clip_file, int(frame_id), matches_line
                                )
                            except Exception as exc:
                                print(
                                    f"[bridge.py] Marker note update failed for {clip_file} frame {frame_id}: {exc}",
                                    file=sys.stderr,
                                )

            yield {"id": msg_id, "type": "matching_complete", **event}
            return

        if event.get("event") == "error":
            yield {"id": msg_id, "type": "error", "message": event.get("reason", "Unknown error")}
            return

        yield {"id": msg_id, "type": "matching_progress", **event}


STREAMING_HANDLERS: dict[str, Any] = {
    "ingest": handle_ingest,
    "classify": handle_classify,
    "transcribe": handle_transcribe,
    "vision": handle_vision,
    "soundbites": handle_soundbites,
    "matching": handle_matching,
}


# ── Main loop ─────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _send(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _error(msg_id: str, message: str) -> None:
    _send({"id": msg_id, "type": "error", "message": message})


def main() -> None:
    print("[bridge.py] started", file=sys.stderr, flush=True)

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"[bridge.py] JSON decode error: {exc}", file=sys.stderr)
            continue

        msg_id: str = msg.get("id", "")
        msg_type: str = msg.get("type", "")

        if not msg_id:
            print(f"[bridge.py] Missing 'id' in message: {line}", file=sys.stderr)
            continue

        if msg_type in STREAMING_HANDLERS:
            try:
                for event in STREAMING_HANDLERS[msg_type](msg):
                    _send(event)
            except Exception as exc:
                print(
                    f"[bridge.py] Streaming handler error for {msg_type!r}: {exc}",
                    file=sys.stderr,
                )
                _error(msg_id, str(exc))
            continue

        handler = HANDLERS.get(msg_type)
        if handler is None:
            _error(msg_id, f"Unknown message type: {msg_type!r}")
            continue

        try:
            response = handler(msg)
            _send(response)
        except Exception as exc:
            print(f"[bridge.py] Handler error for {msg_type!r}: {exc}", file=sys.stderr)
            _error(msg_id, str(exc))

    print("[bridge.py] stdin closed — exiting", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
