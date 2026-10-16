"""S7b on-demand trajectory evals over stored traces.

The runner scores full trajectories (intent plus tools plus approvals as
captured in the trace inputs plus output plus error), not just final text.
Metric prompts are auto-discovered from markdown files so a new file needs
no runner change. Reports are timestamped JSON files with totals, rate,
per-metric counts, and per-trace detail. The runner never runs with unit
tests: it is a separate entry point via ``uv run python evals/eval_runner.py``
or ``make eval``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

JudgeFn = Callable[[dict[str, object], str, str], bool]

_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_PROMPTS_DIR = _HERE.parent / "metrics" / "prompts"
DEFAULT_REPORTS_DIR = _HERE.parent / "reports"
DEFAULT_CASES_DIR = _HERE.parent / "cases"


@dataclass(frozen=True)
class Metric:
    """One scoring prompt discovered from the prompts directory."""

    name: str
    prompt_path: Path
    prompt: str


def discover_metrics(prompts_dir: Path | str) -> list[Metric]:
    """Collect every markdown prompt, sorted by file stem."""
    directory = Path(prompts_dir)
    metrics: list[Metric] = []
    if not directory.is_dir():
        return []
    for path in sorted(directory.glob("*.md")):
        if not path.is_file():
            continue
        prompt = path.read_text(encoding="utf-8").strip()
        if not prompt:
            continue
        metrics.append(Metric(name=path.stem, prompt_path=path, prompt=prompt))
    return metrics


def _extract_inputs(record: dict[str, object]) -> list[object]:
    """Return trajectory inputs from eval (input) or stored (inputs) shape."""
    for key in ("input", "inputs"):
        value = record.get(key)
        if isinstance(value, list):
            return list(value)
    return []


def trajectory_text(record: dict[str, object]) -> str:
    """Render intent plus output plus error for judges and review."""
    lines: list[str] = ["trajectory:"]
    for item in _extract_inputs(record):
        if isinstance(item, dict):
            role = str(item.get("role", "unknown"))
            content = str(item.get("content", ""))
            lines.append(f"{role}: {content}")
    output = record.get("output")
    lines.append(f"assistant: {output!s}" if output is not None else "assistant: ")
    error = record.get("error")
    if error is not None:
        lines.append(f"error: {error!s}")
    return "\n".join(lines)


def normalize_stored_record(payload: dict[str, Any]) -> dict[str, object]:
    """Normalize a stored JSONL trace to the eval runner input shape."""
    normalized: dict[str, object] = {
        "trace_id": str(payload.get("trace_id", "unknown")),
        "timestamp": str(payload.get("timestamp", "")),
        "model": str(payload.get("model", "")),
        "input": payload.get("input", payload.get("inputs", [])),
        "output": payload.get("output", ""),
        "usage": payload.get("usage", {}),
        "latency_ms": payload.get("latency_ms", 0.0),
        "request_id": str(payload.get("request_id", "")),
        "thread_id": str(payload.get("thread_id", "")),
        "user_id": str(payload.get("user_id", "")),
    }
    if payload.get("error") is not None:
        normalized["error"] = payload["error"]
    return normalized


def load_records_from_jsonl(path: Path | str, limit: int = 50) -> list[dict[str, object]]:
    """Load the newest JSONL traces, normalizing stored inputs to eval shape."""
    target = Path(path)
    if limit <= 0 or not target.is_file():
        return []
    lines = target.read_text(encoding="utf-8").splitlines()
    records: list[dict[str, object]] = []
    for line in lines[-limit:]:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(normalize_stored_record(payload))
    return records


def heuristic_judge(record: dict[str, object], _metric_name: str, _prompt: str) -> bool:
    """Offline default: pass when the trajectory has intent and output."""
    inputs = _extract_inputs(record)
    has_input = any(
        isinstance(item, dict) and str(item.get("content", "")).strip() != "" for item in inputs
    )
    output = record.get("output")
    has_output = isinstance(output, str) and output.strip() != ""
    has_error = record.get("error") is not None
    return bool(has_input and has_output and not has_error)


def score_trace(record: dict[str, object], metric: Metric, judge: JudgeFn) -> bool:
    """Score one trace against one metric with the full eval record."""
    return bool(judge(record, metric.name, metric.prompt))


def run_from_records(
    records: Sequence[dict[str, object]],
    metrics: Sequence[Metric],
    judge: JudgeFn,
    duration_seconds: float = 0.0,
) -> dict[str, Any]:
    """Score every trace against every metric and summarize the run."""
    per_metric_pass: dict[str, int] = {metric.name: 0 for metric in metrics}
    per_metric_fail: dict[str, int] = {metric.name: 0 for metric in metrics}
    trace_entries: list[dict[str, Any]] = []
    passed_checks = 0
    total_checks = len(records) * len(metrics)

    for record in records:
        trace_id = str(record.get("trace_id", "unknown"))
        results: dict[str, str] = {}
        failures: list[str] = []
        for metric in metrics:
            passed = score_trace(record, metric, judge)
            results[metric.name] = "pass" if passed else "fail"
            if passed:
                per_metric_pass[metric.name] += 1
                passed_checks += 1
            else:
                per_metric_fail[metric.name] += 1
                failures.append(metric.name)
        trace_entries.append(
            {
                "trace_id": trace_id,
                "passed": len(failures) == 0,
                "failures": sorted(failures),
                "results": results,
            }
        )

    failed_checks = total_checks - passed_checks
    success_rate = (passed_checks / total_checks) if total_checks > 0 else 1.0
    metrics_block: dict[str, dict[str, float | int]] = {}
    for metric in metrics:
        pass_count = per_metric_pass[metric.name]
        fail_count = per_metric_fail[metric.name]
        total = pass_count + fail_count
        rate = (pass_count / total) if total > 0 else 1.0
        metrics_block[metric.name] = {"pass": pass_count, "fail": fail_count, "rate": rate}

    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "total_traces": len(records),
        "total_checks": total_checks,
        "passed_checks": passed_checks,
        "failed_checks": failed_checks,
        "success_rate": success_rate,
        "duration_seconds": duration_seconds,
        "judge_model": os.environ.get("EVALUATION_LLM", "heuristic"),
        "metrics": metrics_block,
        "traces": trace_entries,
    }


def write_report(report: dict[str, Any], reports_dir: Path | str) -> Path:
    """Write a timestamped JSON report and return its path."""
    directory = Path(reports_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=UTC).strftime("eval-report-%Y%m%dT%H%M%S%fZ")
    target = directory / f"{stamp}.json"
    counter = 1
    while target.exists():
        target = directory / f"{stamp}-{counter}.json"
        counter += 1
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return target


def build_report(report: dict[str, Any], reports_dir: Path | str) -> Path:
    """Write a timestamped JSON report; kept as alias for write_report."""
    return write_report(report, reports_dir)


def save_failure_cases(
    report: dict[str, Any],
    records_by_id: dict[object, dict[str, object]],
    cases_dir: Path | str,
) -> list[Path]:
    """Persist one JSON case per failing trace for regression capture."""
    directory = Path(cases_dir)
    directory.mkdir(parents=True, exist_ok=True)
    traces = report.get("traces")
    saved: list[Path] = []
    if not isinstance(traces, list):
        return saved
    generated_at = report.get("generated_at", "")
    for entry in traces:
        if not isinstance(entry, dict):
            continue
        if entry.get("passed", True):
            continue
        trace_id = str(entry.get("trace_id", "unknown"))
        record = records_by_id.get(trace_id, {})
        payload: dict[str, Any] = {
            "trace_id": trace_id,
            "failures": entry.get("failures", []),
            "results": entry.get("results", {}),
            "generated_at": generated_at,
            "record": record,
        }
        target = directory / f"{trace_id}.json"
        counter = 1
        while target.exists():
            target = directory / f"{trace_id}-{counter}.json"
            counter += 1
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        saved.append(target)
    return saved


def load_recent_eval_records(limit: int = 50) -> list[dict[str, object]]:
    """Load the newest buffered traces through the S6 eval input shape."""
    from src.core.tracing import recent_traces, to_eval_record

    return [to_eval_record(record) for record in recent_traces(limit=limit)]


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score stored trajectories.")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--prompts-dir", type=str, default=str(DEFAULT_PROMPTS_DIR))
    parser.add_argument("--reports-dir", type=str, default=str(DEFAULT_REPORTS_DIR))
    parser.add_argument("--cases-dir", type=str, default=str(DEFAULT_CASES_DIR))
    parser.add_argument("--traces-file", type=str, default="")
    parser.add_argument("--no-cases", action="store_true")
    return parser.parse_args(argv)


def _resolve_records(limit: int, traces_file: str) -> list[dict[str, object]]:
    """Load traces from file when available, else from the in-memory buffer."""
    candidate = traces_file.strip()
    if not candidate:
        try:
            from src.core.config import get_settings

            candidate = get_settings().TRACE_LOG_PATH.strip()
        except (ValueError, OSError, RuntimeError):
            candidate = ""
    if candidate:
        from_file = load_records_from_jsonl(candidate, limit=limit)
        if from_file:
            return from_file
    return load_recent_eval_records(limit=limit)


def main(argv: Sequence[str] | None = None) -> int:
    """Discover prompts, score recent traces, write report, save cases."""
    args = _parse_args(argv)
    limit = int(args.limit)
    metrics = discover_metrics(Path(str(args.prompts_dir)))
    if not metrics:
        print(f"No metric prompts found in {args.prompts_dir}", flush=True)
        return 1
    records = _resolve_records(limit=limit, traces_file=str(args.traces_file))
    started = time.perf_counter()
    report = run_from_records(records, metrics, heuristic_judge, duration_seconds=0.0)
    duration = time.perf_counter() - started
    report["duration_seconds"] = duration
    written = write_report(report, Path(str(args.reports_dir)))
    saved: list[Path] = []
    if not bool(args.no_cases):
        saved = save_failure_cases(
            report,
            {record.get("trace_id", ""): record for record in records},
            Path(str(args.cases_dir)),
        )
    summary = {
        "report": str(written),
        "total_traces": report["total_traces"],
        "total_checks": report["total_checks"],
        "passed_checks": report["passed_checks"],
        "failed_checks": report["failed_checks"],
        "success_rate": report["success_rate"],
        "failure_cases": len(saved),
    }
    print(json.dumps(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
