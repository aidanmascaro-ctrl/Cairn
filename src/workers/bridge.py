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

import ingest as ingest_worker
import resolve_api
import sidecar

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


HANDLERS: dict[str, Any] = {
    "ping": handle_ping,
    "echo": handle_echo,
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
        for clip_path in clip_paths.get("photos", []):
            sidecar.write_clip(
                shoot_folder=shoot_folder,
                absolute_path=clip_path,
                bin_path="02_PHOTOS",
                kind="photo",
                metadata=metadata,
            )

        yield {
            "id": msg_id,
            "type": "ingest_progress",
            "event": "sidecars_written",
            "count": len(clip_paths.get("a_cam", [])) + len(clip_paths.get("photos", [])),
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


STREAMING_HANDLERS: dict[str, Any] = {
    "ingest": handle_ingest,
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
