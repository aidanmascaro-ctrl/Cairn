"""
DEPRECATED — do not use. Superseded by ``resolve_api.ingest_into_resolve``.

This script used to be spawned by main.js as a SECOND OS process that opened its
own DaVinci Resolve Scripting connection to import a shoot folder. That violates
Taste Invariant #3 ("never call the Resolve Scripting API from multiple processes
simultaneously — the API is not thread-safe") and deadlocked when run from inside
the Electron plugin sandbox while the WorkflowIntegration connection was live.

The Resolve import now runs through the single Python bridge worker:
    bridge.py handle_ingest -> resolve_api.ingest_into_resolve

This file is retained only as a tombstone so the wrong pattern is not silently
re-introduced. Running it directly does nothing but print this notice — it must
never open a Resolve connection.
"""

from __future__ import annotations

import sys

_MESSAGE = (
    "resolve_import.py is deprecated and intentionally inert. "
    "Use resolve_api.ingest_into_resolve via the bridge worker instead."
)


def main() -> None:
    print(_MESSAGE, file=sys.stderr)


if __name__ == "__main__":
    main()
