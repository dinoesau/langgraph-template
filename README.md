# langgraph-template

This starter gives you a minimal production-minded base for LangGraph agents.
It keeps the deploy contract small so you can grow the codebase step by step.
It is uv-only for install, run, lint, and test.
It adapts the layout ideas from the wassim249 template without copying its code.
S0 ships an echo graph and later milestones add tools and services.
S1 adds the API skeleton with typed config and request logging.
S2a persists threads per `thread_id` through a lifespan checkpointer with app tables under Alembic.

## What's included

- Deploy contract in `langgraph.json:1` maps the graph name to its module variable.
- Dependencies in `pyproject.toml:1` use uv-only setup with a `src` layout.
- Commands in `Makefile` wrap `uv sync`, dev server, tests, lint, and format.
- Layout spec in `docs/structure.md:1` defines where state, nodes, tools, and routes will live.
- Example specs in `docs/examples.md:1` describe five agents and none is implemented yet.
- S0 echo loop in `src/agent/graph.py:1` runs START to chat to END with no tools yet.
- S1 API skeleton in `src/api/app.py:1` serves health with dependencies stub, chat, stream with `thread_id`, and metrics stub.
- S1 typed settings in `src/core/config.py:1` load env with fail-fast validation.
- S2a agent factory in `src/agent/graph.py:1` injects the checkpointer without importing storage.
- S2a lifespan checkpointer in `src/services/database.py:1` persists threads and reports store readiness on health.
- S2a app schema in `src/models/:1` with Alembic migration `0001_create_users_sessions`, excluding loop and memory tables.
- S2b memory in `src/services/memory.py:1` recalls per user and writes in background.
- S2b cache in `src/core/cache.py:1` fronts recall with Valkey plus in-memory TTL fallback.
- S3 tool loop in `src/agent/graph.py:1` runs chat to tool to chat until no tool calls remain.
- S3 LLM fallback in `src/services/llm/service.py:1` retries per model and walks `src/services/llm/registry.py:1` in circular order.
- S4 auth in `src/api/v1/auth.py:1` issues user plus session JWTs with guards in `src/api/deps.py:1`.
- S5a retrieval in `src/services/retrieval.py:1` serves top-k chunks to `src/agent/rag.py:1` with grounded replies.
- S5b router in `src/agent/routes.py:1` sends entry requests to RAG, booking, or chat in `src/agent/graph.py:60`.
- S5c workers in `src/agent/triage.py:1` fan out tickets in parallel and merge one triage card.
- S5d grader in `src/agent/nodes.py:70` loops generator to grader with a guard at `src/agent/nodes.py:21`.
- S6 observability in `src/core/tracing.py:1` traces model calls with logs in `src/core/middleware.py:1` and metrics in `src/core/metrics.py:1`.
- S7a journeys in `tests/integration_tests/test_api_journeys.py:1` cover register to chat over HTTP.
- S7b evals in `evals/eval_runner.py:1` score stored traces on demand with JSON reports.
- S8 stack in `docker-compose.yml:1` runs app plus postgres plus Valkey plus Prometheus plus Grafana.
- Tests in `tests/unit_tests/` cover the echo node and graph without model calls.
- Tests in `tests/unit_tests/test_persistence.py:1` cover thread resume through the factory with a fake saver.
- Tests in `tests/unit_tests/test_migrations.py:1` cover the migration boundary on SQLite without Docker.
- Tests in `tests/integration_tests/test_postgres_persistence.py:1` prove resume across restarts on ephemeral Postgres.
- Tests in `tests/unit_tests/test_api.py:1` cover the S1 routes without network calls.
- Tests in `tests/integration_tests/test_placeholder.py:1` hold placeholders with real models.

## Quickstart

You need uv installed on your machine.
You need Python 3.11 or newer.
Copy the example env file before the first run.

```bash
cp .env.example .env
uv sync
uv run langgraph dev
uv run pytest
```

The dev server entry comes from `langgraph.json:1`.
The graph module exports `graph` and the dev server loads it for Studio.
Use `Makefile` when you prefer short targets for the same uv commands.
Read `docs/structure.md:1` before adding new files so the layout stays small.

## Documentation

| Guide | What it covers |
| --- | --- |
| [Getting Started](docs/getting-started.md) | Requirements, setup, and first run |
| [Structure](docs/structure.md) | Full layout spec and file roles |
| [Examples](docs/examples.md) | Example agent specs to build |
| [Architecture](docs/architecture.md) | System design and request flow |
| [Configuration](docs/configuration.md) | Environment variables and defaults |
| [Authentication](docs/authentication.md) | Planned JWT flow and sessions |
| [Database](docs/database.md) | Planned schema, migrations, and pgvector |
| [LLM Service](docs/llm-service.md) | Planned models, retries, and fallback |
| [Memory](docs/memory.md) | Planned long-term memory and cache |
| [Observability](docs/observability.md) | Planned tracing, logging, and metrics |
| [Evaluation](docs/evaluation.md) | Trajectory scoring and on-demand eval runs |
| [Docker](docs/docker.md) | Planned containers and local services |
| [Testing](docs/testing.md) | Unit, integration, and E2E layers |  
| [Issue Tracker](docs/agents/issue-tracker.md) | GitHub Issues via `gh`, conventions and wayfinding ops |
| [Triage Labels](docs/agents/triage-labels.md) | Canonical triage roles to tracker labels |
| [Domain Docs](docs/agents/domain.md) | Glossary and ADR consumer rules, single-context |

## Project structure

```text
langgraph-template/
├── src/agent/        # S0 echo graph, state, nodes, factory
├── src/api/          # S1 HTTP skeleton, chat routes
├── src/core/         # S1 typed settings and request middleware
├── src/models/       # S2a app tables: users, sessions
├── src/services/     # S2a lifespan checkpointer plus store readiness
├── alembic/          # S2a app migrations, never loop or memory tables
├── tests/            # unit echo tests plus integration placeholders
├── docs/             # 13 guides: structure, examples, testing, services
├── langgraph.json    # deploy contract
├── pyproject.toml    # uv-only dependencies
└── README.md         # this file
```

Full layout details live in `docs/structure.md:1`.
Agent examples live in `docs/examples.md:1`.
Start flat in `src/agent/__init__.py:1`.
Grow into `src/api/` and `src/services/` only when shipping to users.

## FAQ

**Is this ready to deploy today?**
No.
The S0 echo graph runs locally in Studio and production concerns are still planned.

**How do I add the first agent?**
S0 echo already runs in `src/agent/graph.py:1`.
Follow `docs/structure.md:1` for file roles.
Follow `docs/examples.md:1` and build the ReAct tool agent next.

**Why uv-only?**
One tool covers install, run, lint, and test.
This avoids drift between local and clean environments.

## References

- Application structure - Docs by LangChain - https://docs.langchain.com/oss/python/langgraph/application-structure
- langchain-ai/new-langgraph-project - https://github.com/langchain-ai/new-langgraph-project
- langchain-ai/langgraph-example-pyproject - https://github.com/langchain-ai/langgraph-example-pyproject
- wassim249/fastapi-langgraph-agent-production-ready-template - https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template
- Workflows and agents patterns - https://docs.langchain.com/oss/python/langgraph/workflows-agents
