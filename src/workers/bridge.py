#!/usr/bin/env python3
"""
Cairn Python worker — JSON-per-line bridge.

Reads one JSON object per line from stdin, dispatches to a handler by the
'type' field, writes one JSON response per line to stdout. All other logging
goes to stderr so it doesn't pollute the protocol stream.

The Node side (src/orchestrator/python-bridge.ts) is the sole caller.
"""

from __future__ import annotations

import json
import platform
import sys
from datetime import UTC, datetime
from typing import Any

# ── Handlers ──────────────────────────────────────────────────────────────────


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
