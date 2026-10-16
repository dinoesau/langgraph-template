"""S6 debug-only slow-request profiler keyed by X-Request-ID.

When debug mode is on and a request runs past the threshold, one JSON report
lands in the profile directory named ``<request_id>.json``. The request id
matches the ``X-Request-ID`` response header, so one slow request joins logs
and traces end to end. Profiling never raises: failures return None.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


def should_profile(duration_ms: float, *, debug: bool, threshold_ms: float) -> bool:
    """Return True only in debug mode once latency reaches the threshold."""
    if not debug:
        return False
    return duration_ms >= threshold_ms


def profile_filename(request_id: str) -> str:
    """Build the profile file name from a request id, sanitized for disk."""
    safe = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in request_id)
    return f"{safe or 'unknown'}.json"


def write_profile(
    *,
    request_id: str,
    method: str,
    path: str,
    duration_ms: float,
    thread_id: str,
    directory: str,
    debug: bool,
    threshold_ms: float,
) -> Path | None:
    """Write one slow-request JSON report, or None when skipped or failed."""
    if not should_profile(duration_ms, debug=debug, threshold_ms=threshold_ms):
        return None
    payload = {
        "request_id": request_id,
        "thread_id": thread_id,
        "method": method,
        "path": path,
        "duration_ms": duration_ms,
        "captured_at": datetime.now(tz=UTC).isoformat(),
    }
    try:
        target = Path(directory) / profile_filename(request_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return target
    except OSError:
        return None
