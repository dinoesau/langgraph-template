# Observability

Tracing and metrics are planned.
They are not implemented yet.
This guide describes the target setup for this starter.

## Status

LangSmith keys are already present for local runs.
See `.env.example:1` for the key placeholders.
See `langgraph.json:1` for the env pointer used by the CLI.
Structured logs are planned.
Prometheus metrics are planned.
Grafana dashboards are planned.
See `docs/structure.md:195` for the observability contract.

```mermaid
graph LR
    App["App"]

    App -->|"LLM traces"| Traces["Trace backend"]
    App -->|"HTTP metrics"| Prometheus
    Prometheus --> Grafana
    App -->|"structured logs"| Logs["Log output"]
    App -->|"slow request profiles"| Profiles["JSON files"]
```

## Tracing

Every LLM call will be traced.
Traces will include input messages and output text.
Traces will include token usage and latency.
Traces will include model name and call parameters.
LangSmith will cover local runs through the CLI.
Langfuse is planned as the long term trace backend.
Traces will feed the eval framework in `evals/eval_runner.py:1`.
That file is planned and does not exist yet.
Disable tracing for offline work with env flags.

Planned setup:

```bash
cp .env.example .env
uv sync
uv run langgraph dev
```

Set `LANGFUSE_TRACING_ENABLED=false` to run without remote traces.
Set it to `true` once keys are configured.

## Structured logging

All logs will use structured events.
Development will print readable console output.
Production will print JSON to stdout.
Every line will carry request and session context when available.
Context fields will be added by middleware when FastAPI exists.
See `docs/structure.md:174` for the middleware role.

Rules to follow:

```python
# Good
logger.info("chat_request_received", session_id=session.id, message_count=5)
```

Use event names in lowercase with underscores.
Pass variables as keyword arguments.
Never interpolate values into the event string.
Use `logger.exception` inside `except` blocks to keep the traceback.

Planned levels by environment:

| Environment | Level |
| --- | --- |
| development | DEBUG |
| staging | INFO |
| production | WARNING |

## Prometheus metrics

Metrics will be exposed at `GET /metrics`.
Prometheus will scrape that endpoint on a fixed interval.
Grafana will render the preconfigured dashboards.

Planned metrics:

| Metric | Type | Description |
| --- | --- | --- |
| http_requests_total | Counter | Request count by method and status |
| http_request_duration_seconds | Histogram | Request latency by endpoint |
| llm_inference_duration_seconds | Histogram | LLM latency by model |
| db_connections | Gauge | Active database connections |

Code will live in `src/core/middleware.py:1` when added.
Dashboards will live in `grafana/:1` when added.
Scrape config will live in `prometheus/:1` when added.
All three paths are planned and do not exist yet.

## Request profiling

Slow request profiling is planned for debug mode only.
It will profile every request when `DEBUG=true`.
It will save a JSON report when latency passes the threshold.
Each file will be named with the request identifier.
The identifier will match the `X-Request-ID` response header.
Use that header to join logs and traces for one request.
Set the threshold to zero to profile every request.

## Request identifier

Every request will get a unique `X-Request-ID`.
The value will be returned in the response headers.
The value will be bound to every log line for that request.
The value will name the profile file when profiling is on.
This makes one slow request easy to follow end to end.

## What exists today

`Makefile:8` installs deps with `uv sync`.
`Makefile:11` starts the dev server.
`Makefile:14` runs tests with `uv run pytest`.
No metrics endpoint exists yet.
No log middleware exists yet.
No dashboard files exist yet.

## Next steps

Add `src/core/middleware.py` for identifiers and metrics.
Add `GET /metrics` when `src/api/v1/` is created.
Add `prometheus/` scrape config for local stack.
Add `grafana/` dashboards for latency and errors.
Document env flags in `.env.example:1`.
