"""S6 Prometheus metrics without extra dependencies.

In-process counters and histograms render as Prometheus text exposition at
``GET /metrics``. Labels stay low-cardinality: HTTP signals split by method,
path, and status; LLM signals split by model; the store signal is one gauge.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)


@dataclass
class _Histogram:
    buckets: list[int] = field(default_factory=lambda: [0] * len(_BUCKETS))
    total: float = 0.0
    count: int = 0

    def observe(self, value: float) -> None:
        for index, bound in enumerate(_BUCKETS):
            if value <= bound:
                self.buckets[index] += 1
                break
        self.total += value
        self.count += 1


_lock = threading.Lock()
_http_counts: dict[tuple[str, str, str], int] = {}
_http_durations: dict[tuple[str, str], _Histogram] = {}
_llm_durations: dict[str, _Histogram] = {}
_db_connections: int = 0


def observe_http(method: str, path: str, status: int, duration_seconds: float) -> None:
    """Count one request and add its latency to the HTTP histogram."""
    with _lock:
        key = (method, path, str(status))
        _http_counts[key] = _http_counts.get(key, 0) + 1
        histogram = _http_durations.get((method, path))
        if histogram is None:
            histogram = _Histogram()
            _http_durations[(method, path)] = histogram
        histogram.observe(max(duration_seconds, 0.0))


def observe_llm(model: str, duration_seconds: float) -> None:
    """Add one model attempt latency to the LLM histogram."""
    with _lock:
        histogram = _llm_durations.get(model)
        if histogram is None:
            histogram = _Histogram()
            _llm_durations[model] = histogram
        histogram.observe(max(duration_seconds, 0.0))


def set_db_connections(count: int) -> None:
    """Set the active database connection gauge."""
    global _db_connections
    with _lock:
        _db_connections = max(count, 0)


def reset_metrics() -> None:
    """Clear every series, used by tests to assert exact shapes."""
    global _db_connections
    with _lock:
        _http_counts.clear()
        _http_durations.clear()
        _llm_durations.clear()
        _db_connections = 0


def render_text() -> str:
    """Render all series in Prometheus text exposition format."""
    with _lock:
        counts = dict(_http_counts)
        http_hists = {key: _snapshot(value) for key, value in _http_durations.items()}
        llm_hists = {key: _snapshot(value) for key, value in _llm_durations.items()}
        db_value = _db_connections
    lines = [
        "# HELP http_requests_total Request count by method, path and status.",
        "# TYPE http_requests_total counter",
    ]
    for (method, path, status), total in sorted(counts.items()):
        labels = _labels(method=method, path=path, status=status)
        lines.append(f"http_requests_total{labels} {total}")
    lines += [
        "# HELP http_request_duration_seconds Request latency by method and path.",
        "# TYPE http_request_duration_seconds histogram",
    ]
    for (method, path), snapshot in sorted(http_hists.items()):
        base = {"method": method, "path": path}
        lines.extend(_histogram_lines("http_request_duration_seconds", base, snapshot))
    lines += [
        "# HELP llm_inference_duration_seconds LLM latency by model.",
        "# TYPE llm_inference_duration_seconds histogram",
    ]
    for model, snapshot in sorted(llm_hists.items()):
        lines.extend(_histogram_lines("llm_inference_duration_seconds", {"model": model}, snapshot))
    lines += [
        "# HELP db_connections Active database connections.",
        "# TYPE db_connections gauge",
        f"db_connections {db_value}",
    ]
    return "\n".join(lines) + "\n"


def _snapshot(histogram: _Histogram) -> tuple[list[int], float, int]:
    cumulative: list[int] = []
    running = 0
    for count in histogram.buckets:
        running += count
        cumulative.append(running)
    return cumulative, histogram.total, histogram.count


def _histogram_lines(
    name: str, base: dict[str, str], snapshot: tuple[list[int], float, int]
) -> list[str]:
    cumulative, total, count = snapshot
    lines: list[str] = []
    for bound, bucket_count in zip(_BUCKETS, cumulative, strict=True):
        labels = _labels(le=_format_bound(bound), **base)
        lines.append(f"{name}_bucket{labels} {bucket_count}")
    lines.append(f"{name}_bucket{_labels(le='+Inf', **base)} {count}")
    lines.append(f"{name}_sum{_labels(**base)} {total}")
    lines.append(f"{name}_count{_labels(**base)} {count}")
    return lines


def _labels(**pairs: str) -> str:
    parts = [f'{key}="{_escape(value)}"' for key, value in sorted(pairs.items())]
    return "{" + ",".join(parts) + "}"


def _escape(value: str) -> str:
    return value.replace("\\", r"\\").replace('"', r"\"").replace("\n", r"\n")


def _format_bound(bound: float) -> str:
    return repr(bound)
