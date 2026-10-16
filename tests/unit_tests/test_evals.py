"""S7b seam: on-demand trajectory evals over stored traces.

Seams under test (highest first):
- metric discovery from prompts dir (catches auto-discovery without runner
  change; misses prompt wording quality).
- trajectory scoring with judge seeing full eval record (catches intent plus
  tools plus approvals vs final-text-only; misses real LLM judgment).
- timestamped JSON report (catches totals plus rate plus per-metric plus
  per-trace; misses dashboard rendering).
- failure to case (catches regression capture; misses production triage).
"""

from pathlib import Path
from typing import Any


def _record(trace_id: str, user_text: str, output: str) -> dict[str, object]:
    return {
        "trace_id": trace_id,
        "timestamp": "2026-10-16T00:00:00+00:00",
        "model": "fake-model",
        "input": [{"role": "human", "content": user_text}],
        "output": output,
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        "latency_ms": 1.0,
        "request_id": "req-" + trace_id,
        "thread_id": "thread-1",
        "user_id": "user-1",
    }


def _metric(name: str = "helpfulness", prompt: str = "Be helpful.") -> Any:
    from evals.eval_runner import Metric

    return Metric(name=name, prompt_path=Path(name + ".md"), prompt=prompt)


def test_metric_pass_fail_via_fakes() -> None:
    from evals.eval_runner import score_trace

    record = _record("t-pass", "book a slot", "Booked 2026-10-23 at 10:00.")

    def _always_pass(_record: dict[str, object], _name: str, _prompt: str) -> bool:
        return True

    def _always_fail(_record: dict[str, object], _name: str, _prompt: str) -> bool:
        return False

    assert score_trace(record, _metric(), _always_pass) is True
    assert score_trace(record, _metric(), _always_fail) is False


def test_score_uses_full_trajectory_not_just_output() -> None:
    from evals.eval_runner import score_trace

    seen: dict[str, object] = {}

    def _judge(record: dict[str, object], _name: str, _prompt: str) -> bool:
        seen.update(record)
        inputs = record.get("input")
        assert isinstance(inputs, list) and len(inputs) == 1
        content = inputs[0].get("content", "") if isinstance(inputs[0], dict) else ""
        # Judge keys off intent (input), not just final text.
        return "book" in str(content).lower()

    booking = _record("t-book", "book a slot tomorrow", "Done.")
    chat = _record("t-chat", "hello there", "Done.")
    assert score_trace(booking, _metric(), _judge) is True
    assert score_trace(chat, _metric(), _judge) is False
    assert seen["trace_id"] == "t-chat"


def test_trajectory_text_covers_intent_and_output() -> None:
    from evals.eval_runner import trajectory_text

    record = _record("t-text", "refund policy please", "Full refunds within 30 days.")
    text = trajectory_text(record)
    assert "refund policy please" in text
    assert "Full refunds within 30 days." in text


def test_discover_metrics_auto_discovers_new_file(tmp_path: Path) -> None:
    from evals.eval_runner import discover_metrics

    (tmp_path / "helpfulness.md").write_text("# Helpfulness\nPass when helpful.\n")
    (tmp_path / "toxicity.md").write_text("# Toxicity\nPass when safe.\n")
    first = discover_metrics(tmp_path)
    assert sorted(m.name for m in first) == ["helpfulness", "toxicity"]
    (tmp_path / "custom.md").write_text("# Custom\nPass when custom holds.\n")
    second = discover_metrics(tmp_path)
    assert sorted(m.name for m in second) == ["custom", "helpfulness", "toxicity"]
    assert all(m.prompt.strip() != "" for m in second)


def test_default_prompts_present() -> None:
    from evals.eval_runner import DEFAULT_PROMPTS_DIR, discover_metrics

    metrics = discover_metrics(DEFAULT_PROMPTS_DIR)
    names = sorted(m.name for m in metrics)
    assert names == ["conciseness", "hallucination", "helpfulness", "relevancy", "toxicity"]


def test_report_shape_has_totals_rate_per_metric_per_trace(tmp_path: Path) -> None:
    from evals.eval_runner import build_report, discover_metrics, run_from_records

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "helpfulness.md").write_text("# Helpfulness\nPass when helpful.\n")
    (prompts / "toxicity.md").write_text("# Toxicity\nPass when safe.\n")
    metrics = discover_metrics(prompts)
    records = [
        _record("t-1", "hello", "Hi there."),
        _record("t-2", "hello", "Hi there."),
    ]

    def _pass_all(_record: dict[str, object], name: str, _prompt: str) -> bool:
        return name == "helpfulness"

    report = run_from_records(records, metrics, _pass_all, duration_seconds=2.0)
    assert report["total_traces"] == 2
    assert report["total_checks"] == 4
    assert report["passed_checks"] == 2
    assert report["failed_checks"] == 2
    assert report["success_rate"] == 0.5
    assert report["duration_seconds"] == 2.0
    assert set(report["metrics"].keys()) == {"helpfulness", "toxicity"}
    assert report["metrics"]["helpfulness"] == {"pass": 2, "fail": 0, "rate": 1.0}
    assert report["metrics"]["toxicity"] == {"pass": 0, "fail": 2, "rate": 0.0}
    assert len(report["traces"]) == 2
    first_trace = report["traces"][0]
    assert first_trace["trace_id"] == "t-1"
    assert first_trace["passed"] is False
    assert first_trace["failures"] == ["toxicity"]

    written = build_report(report, tmp_path)
    assert written.exists()
    assert written.suffix == ".json"


def test_write_report_is_timestamped(tmp_path: Path) -> None:
    from evals.eval_runner import run_from_records, write_report

    report = run_from_records([], [], lambda _r, _n, _p: True, duration_seconds=0.1)
    first = write_report(report, tmp_path)
    second = write_report(report, tmp_path)
    assert first.name.startswith("eval-report-")
    assert first.suffix == ".json"
    assert first != second or first.exists()
    assert (tmp_path / first.name).exists()


def test_failure_to_case_flow(tmp_path: Path) -> None:
    from evals.eval_runner import run_from_records, save_failure_cases

    metrics = [_metric("helpfulness"), _metric("toxicity")]
    records = [
        _record("t-ok", "hello", "Hi."),
        _record("t-bad", "hello", "Bad output."),
    ]

    def _judge(record: dict[str, object], _name: str, _prompt: str) -> bool:
        return str(record.get("trace_id")) != "t-bad"

    report = run_from_records(records, metrics, _judge, duration_seconds=0.1)
    cases_dir = tmp_path / "cases"
    paths = save_failure_cases(report, {r["trace_id"]: r for r in records}, cases_dir)
    assert len(paths) == 1
    assert paths[0].name.startswith("t-bad")
    assert paths[0].suffix == ".json"
    import json

    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert payload["trace_id"] == "t-bad"
    assert sorted(payload["failures"]) == ["helpfulness", "toxicity"]


def test_load_recent_eval_records_wires_tracing() -> None:
    from evals.eval_runner import load_recent_eval_records
    from src.core import tracing

    tracing.clear_traces()
    made = tracing.record_trace(
        model="fake",
        inputs=[{"role": "human", "content": "eval me"}],
        output="traced reply",
        usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        latency_ms=1.0,
        request_id="req-eval-wire",
        thread_id="thread-eval",
        user_id="user-eval",
    )
    assert made is not None
    records = load_recent_eval_records(limit=5)
    assert len(records) == 1
    assert records[0]["trace_id"] == made.trace_id
    assert records[0]["output"] == "traced reply"
    tracing.clear_traces()


def test_import_has_no_side_effects(tmp_path: Path) -> None:
    import importlib

    before = {p.name for p in tmp_path.iterdir()}
    importlib.import_module("evals.eval_runner")
    after = {p.name for p in tmp_path.iterdir()}
    assert before == after


def test_jsonl_loader_normalizes_stored_inputs(tmp_path: Path) -> None:
    import json

    from evals.eval_runner import (
        load_records_from_jsonl,
        normalize_stored_record,
        trajectory_text,
    )

    stored = {
        "trace_id": "t-jsonl",
        "timestamp": "2026-10-16T00:00:00+00:00",
        "model": "fake",
        "inputs": [{"role": "human", "content": "book a slot"}],
        "output": "Booked.",
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        "latency_ms": 1.0,
        "request_id": "req-1",
        "thread_id": "thread-1",
        "user_id": "user-1",
        "error": None,
    }
    assert normalize_stored_record(stored)["input"] == stored["inputs"]
    traces_file = tmp_path / "traces.jsonl"
    traces_file.write_text(json.dumps(stored) + "\n", encoding="utf-8")
    records = load_records_from_jsonl(traces_file, limit=10)
    assert len(records) == 1
    assert records[0]["trace_id"] == "t-jsonl"
    assert "book a slot" in trajectory_text(records[0])
