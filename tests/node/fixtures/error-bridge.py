#!/usr/bin/env python3
"""
Test fixture: minimal bridge that responds to ping normally and
injects an error response for every other message type. Used to
verify that PythonBridge.handleLine() rejects promises on type="error".
"""

import json
import sys

for raw in sys.stdin:
    line = raw.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue

    msg_id = msg.get("id", "")
    msg_type = msg.get("type", "")

    if msg_type == "ping":
        sys.stdout.write(
            json.dumps(
                {
                    "id": msg_id,
                    "type": "pong",
                    "python_version": "3.0.0",
                    "timestamp_iso": "2026-01-01T00:00:00+00:00",
                }
            )
            + "\n"
        )
        sys.stdout.flush()
    elif msg_type == "classify":
        # Emit one progress event, then crash with an error
        sys.stdout.write(
            json.dumps(
                {
                    "id": msg_id,
                    "type": "classify_progress",
                    "event": "classify_started",
                    "total_clips": 1,
                }
            )
            + "\n"
        )
        sys.stdout.flush()
        sys.stdout.write(
            json.dumps(
                {
                    "id": msg_id,
                    "type": "error",
                    "message": "simulated worker crash",
                }
            )
            + "\n"
        )
        sys.stdout.flush()
    else:
        sys.stdout.write(
            json.dumps(
                {
                    "id": msg_id,
                    "type": "error",
                    "message": f"simulated error for type={msg_type}",
                }
            )
            + "\n"
        )
        sys.stdout.flush()
