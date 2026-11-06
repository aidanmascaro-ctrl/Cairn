#!/usr/bin/env python3
"""
Cairn Python worker — JSON-per-line bridge.

Reads one JSON object per line from stdin, dispatches to a handler by the
'type' field, writes one or more JSON responses per line to stdout.
All other logging goes to stderr so it doesn't pollute the protocol stream.

Two handler types:
  HANDLERS           — return a single dict (one response)
  STREAMING_HANDLERS — generators that yield dicts (multiple responses)

The Node side (src/orchestrator/python-bridge.ts) is the sole caller.
"""

from __future__ import annotations

import json
import math
import platform
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import classify as classify_worker
import ingest as ingest_worker
import resolve_api
import selects as selects_worker
import selects_learn
import selects_score
import selects_timeline
import sidecar
import timeline as timeline_worker

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


def handle_health_check(msg: dict[str, Any]) -> dict[str, Any]:
    """
    Verify the Python ↔ Resolve scripting connection. Returns scripting_ok=True
    if `dvr.scriptapp("Resolve")` resolves AND a project is open. Returns a
    human-readable reason on failure so the UI can surface it as a banner.
    """
    scripting_ok = False
    reason: str | None = None
    try:
        resolve = resolve_api.get_resolve()
        pm = resolve.GetProjectManager()
        project = pm.GetCurrentProject() if pm else None
        if project is None:
            reason = "No DaVinci Resolve project is open."
        else:
            scripting_ok = True
    except resolve_api.ResolveUnavailableError as exc:
        reason = (
            "DaVinci Resolve scripting is not available. "
            "Open Resolve and enable External Scripting in "
            "Preferences → System → General. "
            f"({exc})"
        )
    except Exception as exc:
        reason = f"Unexpected error checking Resolve scripting: {exc}"
    return {
        "id": msg["id"],
        "type": "health_check_result",
        "scripting_ok": scripting_ok,
        "reason": reason,
    }


def handle_classify_confirm(msg: dict[str, Any]) -> dict[str, Any]:
    """
    Persist a user-confirmed classification override for one AMBIGUOUS clip.
    Updates the sidecar JSON, then writes the keyword + clip colour to Resolve.
    """
    payload = msg.get("payload", {})
    clip_path: str = payload["clip_path"]
    label: str = payload["label"]
    shoot_folder: str = payload["shoot_folder"]

    ingest_worker.preflight(Path(shoot_folder))

    sidecar.merge_clip(
        shoot_folder,
        clip_path,
        updates={"classification": label, "user_confirmed": True},
        new_stage="classify",
    )

    resolve_ok = False
    resolve_reason: str | None = None
    try:
        resolve_ok, resolve_reason = resolve_api.classify_write_resolve(clip_path, label)
    except Exception as exc:
        resolve_reason = f"Resolve write raised: {exc}"
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
        "resolve_reason": resolve_reason,
    }


def handle_build_timeline(msg: dict[str, Any]) -> dict[str, Any]:
    """
    Build the assembly timeline (Stage 4) — A-roll on V1/A1, B-roll on V2/A2.
    Single-response handler; returns ok:true with counts or ok:false with error.
    """
    payload = msg.get("payload", {})
    path: str = payload["path"]
    name: str | None = payload.get("name")

    result = timeline_worker.build_timeline(path, name)
    return {
        "id": msg["id"],
        "type": "build_timeline_result",
        **result,
    }


def handle_classify_inspect(msg: dict[str, Any]) -> dict[str, Any]:
    """
    Re-analyse a single clip and return annotated frames for the visual inspector.
    The heavy work (frame extraction + cv2 overlay) runs in classify_worker.
    """
    payload = msg.get("payload", {})
    clip_path: str = payload["clip_path"]

    result = classify_worker.inspect_clip(clip_path)
    return {"id": msg["id"], "type": "classify_inspect_result", **result}


# ── Stage 5: Selects ──────────────────────────────────────────────────────────


def _shoot(payload: dict[str, Any]) -> str:
    path = payload.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("payload.path (shoot folder) is required")
    if not Path(path).is_dir():
        raise ValueError(f"Shoot folder does not exist: {path}")
    return path


def _explicit_settings(payload: dict[str, Any]) -> selects_score.SelectsSettings | None:
    """Settings or a preset id from the caller, or None to use what the shoot last used."""
    raw = payload.get("settings")
    if isinstance(raw, dict):
        return selects_score.settings_from_dict(raw)
    preset_id = payload.get("preset_id")
    if isinstance(preset_id, str) and preset_id:
        return selects_learn.resolve_preset(preset_id)
    return None


def _settings(
    payload: dict[str, Any], shoot: str
) -> tuple[selects_score.SelectsSettings, list[str]]:
    """Explicit settings win, then a preset, then the shoot's saved tuning — whose
    unreadable-file warning is returned rather than dropped."""
    explicit = _explicit_settings(payload)
    if explicit is not None:
        return explicit, []
    saved, warning = selects_worker.load_settings_with_warning(shoot)
    return saved, [warning] if warning else []


def handle_selects_presets(msg: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": msg["id"],
        "type": "selects_presets_result",
        "presets": selects_learn.list_all_presets(),
    }


def handle_selects_rescore(msg: dict[str, Any]) -> dict[str, Any]:
    payload = msg.get("payload", {})
    shoot = _shoot(payload)
    # None → rescore loads the saved tuning itself and reports if it was unreadable.
    result = selects_worker.rescore(shoot, _explicit_settings(payload), persist=True)
    return {"id": msg["id"], "type": "selects_rescore_result", **result}


def handle_selects_rate(msg: dict[str, Any]) -> dict[str, Any]:
    payload = msg.get("payload", {})
    shoot = _shoot(payload)
    clip_path = payload.get("clip_path")
    if not isinstance(clip_path, str) or not clip_path:
        raise ValueError("payload.clip_path is required")
    rating = payload.get("rating")
    try:
        start_sec = float(payload["start_sec"])
        end_sec = float(payload["end_sec"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("payload.start_sec and payload.end_sec must be numbers") from exc
    ratings = selects_learn.rate(
        shoot,
        clip_path,
        start_sec,
        end_sec,
        rating if isinstance(rating, str) else None,
        str(payload.get("source", "select")),
    )
    return {
        "id": msg["id"],
        "type": "selects_rate_result",
        "clip_path": clip_path,
        "ratings": ratings,
    }


def handle_selects_learn_context(msg: dict[str, Any]) -> dict[str, Any]:
    payload = msg.get("payload", {})
    shoot = _shoot(payload)
    settings, warnings = _settings(payload, shoot)
    ctx = {**selects_learn.learn_context(shoot, settings), "warnings": warnings}
    return {"id": msg["id"], "type": "selects_learn_context_result", **ctx}


def handle_selects_evaluate(msg: dict[str, Any]) -> dict[str, Any]:
    payload = msg.get("payload", {})
    shoot = _shoot(payload)
    settings, warnings = _settings(payload, shoot)
    result = {**selects_learn.evaluate(shoot, settings), "warnings": warnings}
    return {"id": msg["id"], "type": "selects_evaluate_result", **result}


def handle_selects_save_preset(msg: dict[str, Any]) -> dict[str, Any]:
    payload = msg.get("payload", {})
    raw = payload.get("settings")
    if not isinstance(raw, dict):
        raise ValueError("payload.settings is required")
    accuracy = payload.get("accuracy")
    entry = selects_learn.save_user_preset(
        str(payload.get("name", "")),
        selects_score.settings_from_dict(raw),
        str(payload.get("description", "")),
        str(payload.get("rationale", "")),
        float(accuracy) if isinstance(accuracy, (int, float)) and math.isfinite(accuracy) else None,
    )
    return {"id": msg["id"], "type": "selects_save_preset_result", "preset": entry}


def handle_selects_build_timeline(msg: dict[str, Any]) -> dict[str, Any]:
    payload = msg.get("payload", {})
    shoot = _shoot(payload)
    name = payload.get("name")
    result = selects_timeline.build_selects_timeline(
        shoot,
        _explicit_settings(payload),  # None → the build's rescore reports saved-tuning issues
        layout=str(payload.get("layout", "context")),
        order=str(payload.get("order", "timeline")),
        include_markers=bool(payload.get("include_markers", True)),
        clip_markers=bool(payload.get("clip_markers", True)),
        name=name if isinstance(name, str) and name.strip() else None,
    )
    return {"id": msg["id"], "type": "selects_build_result", **result}


def handle_selects_analyse(msg: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Analyse every clip once (stream). Re-tuning afterwards never decodes again."""
    payload = msg.get("payload", {})
    shoot = _shoot(payload)
    rate = float(payload.get("sampling_rate", 4.0))
    if rate not in (*selects_score.SAMPLING_RATES, selects_score.EVERY_FRAME):
        raise ValueError(
            f"sampling_rate must be one of {selects_score.SAMPLING_RATES} or 0 (every frame)"
        )
    for event in selects_worker.analyse_all(shoot, rate, force=bool(payload.get("force", False))):
        if event["event"] == "analyse_complete":
            yield {"id": msg["id"], "type": "selects_analyse_complete", **event}
            return
        yield {"id": msg["id"], "type": "selects_progress", **event}


HANDLERS: dict[str, Any] = {
    "ping": handle_ping,
    "echo": handle_echo,
    "health_check": handle_health_check,
    "classify_confirm": handle_classify_confirm,
    "classify_inspect": handle_classify_inspect,
    "build_timeline": handle_build_timeline,
    "selects_presets": handle_selects_presets,
    "selects_rescore": handle_selects_rescore,
    "selects_rate": handle_selects_rate,
    "selects_learn_context": handle_selects_learn_context,
    "selects_evaluate": handle_selects_evaluate,
    "selects_save_preset": handle_selects_save_preset,
    "selects_build_timeline": handle_selects_build_timeline,
}

# ── Streaming handlers ────────────────────────────────────────────────────────


def handle_ingest(msg: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """
    Run the ingest pipeline for one shoot folder.

    Payload keys:
      path          — absolute path to shoot folder (required)
      name          — shoot name override (optional)
      dry_run       — bool, default false
      write_sidecar — bool, default true
      resolve       — bool, default true
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

        # Offload shoots: one bin per camera folder (Footage/<Camera>).
        camera_clips: dict[str, list[str]] = result.get("camera_clips", {})
        for camera, paths in camera_clips.items():
            for clip_path in paths:
                sidecar.write_clip(
                    shoot_folder=shoot_folder,
                    absolute_path=clip_path,
                    bin_path=f"Footage/{camera}",
                    kind="video",
                    metadata=metadata,
                )

        sidecar_count = (
            len(clip_paths.get("a_cam", []))
            + len(clip_paths.get("c_cam", []))
            + len(clip_paths.get("photos", []))
            + sum(len(paths) for paths in camera_clips.values())
        )
        yield {
            "id": msg_id,
            "type": "ingest_progress",
            "event": "sidecars_written",
            "count": sidecar_count,
        }

    resolve_counts: dict[str, int] = {}
    refused_fields: list[str] = []
    if do_resolve:
        try:
            if result.get("layout") == "offload":
                imported = resolve_api.ingest_offload_into_resolve(
                    shoot_folder=result["folder"],
                    camera_clips=result.get("camera_clips", {}),
                    photo_paths=result.get("clip_paths", {}).get("photos", []),
                    metadata=result.get("metadata", {}),
                )
            else:
                imported = resolve_api.ingest_into_resolve(
                    shoot_folder=result["folder"],
                    clip_paths=result.get("clip_paths", {}),
                    metadata=result.get("metadata", {}),
                )
            resolve_counts, refused_fields = imported.counts, imported.refused_fields
            yield {
                "id": msg_id,
                "type": "ingest_progress",
                "event": "resolve_imported",
                **resolve_counts,
            }
            if refused_fields:
                yield {
                    "id": msg_id,
                    "type": "ingest_progress",
                    "event": "metadata_refused",
                    "fields": refused_fields,
                }
        except resolve_api.ResolveUnavailableError as exc:
            yield {
                "id": msg_id,
                "type": "ingest_progress",
                "event": "resolve_skipped",
                "reason": str(exc),
            }
        except Exception as exc:
            # Files are already organised and sidecars written by now: report the
            # Resolve failure but still send ingest_complete, so that work isn't lost.
            print(f"[bridge] Resolve import failed: {exc!r}", file=sys.stderr)
            yield {
                "id": msg_id,
                "type": "ingest_progress",
                "event": "resolve_skipped",
                "reason": f"Resolve import failed: {exc}",
            }

    yield {
        "id": msg_id,
        "type": "ingest_complete",
        "folder": result.get("folder", path),
        "files_moved": result.get("files_moved", 0),
        "shells_removed": result.get("shells_removed", 0),
        "metadata": result.get("metadata", {}),
        "clip_paths": result.get("clip_paths", {}),
        "layout": result.get("layout", "organised"),
        "camera_clips": result.get("camera_clips", {}),
        "resolve_counts": resolve_counts,
        "metadata_refused": refused_fields,
        "dry_run": dry_run,
        "timestamp_iso": _now(),
    }


def handle_classify(msg: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """
    Classify every video clip in a shoot folder (duration + face detection).

    Payload keys:
      path    — absolute path to the organised shoot folder (required)
      dry_run — bool, default false (skip Resolve colour writes when true)
    """
    payload = msg.get("payload", {})
    msg_id: str = msg["id"]

    path: str = payload["path"]
    dry_run: bool = bool(payload.get("dry_run", False))

    # A dry run writes no sidecars, so it can read a locked or archived shoot.
    ingest_worker.preflight(Path(path), require_writable=not dry_run)

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
        sidecar_ok: bool | None = None
        if not dry_run and event.get("event") == "clip_classified":
            try:
                sidecar.merge_clip(
                    path,
                    event["file"],
                    updates={"classification": event["label"]},
                    new_stage="classify",
                )
                sidecar_ok = True
            except Exception as exc:
                sidecar_ok = False
                print(
                    f"[bridge.py] Sidecar write failed for {event['file']}: {exc}",
                    file=sys.stderr,
                )
            try:
                resolve_ok, _reason = resolve_api.classify_write_resolve(
                    event["file"], event["label"]
                )
            except Exception as exc:
                resolve_ok = False
                print(
                    f"[bridge.py] Resolve write failed for {event['file']}: {exc}",
                    file=sys.stderr,
                )

        yield {
            "id": msg_id,
            "type": "classify_progress",
            "resolve_ok": resolve_ok,
            "sidecar_ok": sidecar_ok,
            **event,
        }


STREAMING_HANDLERS: dict[str, Any] = {
    "ingest": handle_ingest,
    "classify": handle_classify,
    "selects_analyse": handle_selects_analyse,
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
