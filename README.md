# langgraph-template

This starter gives you a minimal production-minded base for LangGraph agents.
It keeps the deploy contract small so you can grow the codebase step by step.
It is uv-only for install, run, lint, and test.
It adapts the layout ideas from the wassim249 template without copying its code.
Most agent features are planned and the repo ships an echo loop with HTTP chat.

## What's included

- Deploy contract in `langgraph.json:1` maps the graph name to its module variable.
- Dependencies in `pyproject.toml:1` use uv-only setup with a `src` layout.
- Commands in `Makefile` wrap `uv sync`, dev server, tests, lint, and format.
- Layout spec in `docs/structure.md:1` defines where state, nodes, tools, and routes will live.
- Example specs in `docs/examples.md:1` describe five agents and none is implemented yet.
- Echo loop with `graph` plus `state` answers greetings without models, tools arrive with the tool-calling loop.
- HTTP skeleton with health plus chat plus typed settings is live, LLM fallback plus memory plus database plus auth plus observability are planned.
- Tests in `tests/unit_tests/` cover the loop plus settings plus HTTP without model calls.
- Tests in `tests/integration_tests/` hold placeholders with real models.

## Quickstart

You need uv installed on your machine.
You need Python 3.11 or newer.
Copy the example env file before the first run.

```bash
cp .env.example .env
uv sync
uv run langgraph dev
uv run uvicorn --app-dir src api.app:app
uv run pytest
```

The dev server entry comes from `langgraph.json:1`.
The graph module exists as an echo loop and the dev server loads it.
The HTTP skeleton serves on `:8000` with `GET /api/v1/health` and `POST /api/v1/chat`.
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
| [Evaluation](docs/evaluation.md) | Planned trajectory scoring and eval runs |
| [Docker](docs/docker.md) | Planned containers and local services |
| [Testing](docs/testing.md) | Unit, integration, and E2E layers |  
| [Issue Tracker](docs/agents/issue-tracker.md) | GitHub Issues via `gh`, conventions and wayfinding ops |
| [Triage Labels](docs/agents/triage-labels.md) | Canonical triage roles to tracker labels |
| [Domain Docs](docs/agents/domain.md) | Glossary and ADR consumer rules, single-context |

## Project structure

```text
langgraph-template/
├── src/agent/        # planned graph, state, nodes, tools, routes
├── tests/            # unit placeholders plus integration placeholders
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
The deploy contract maps to an echo loop, production agent features are still planned.

**How do I add the first agent?**
Follow `docs/structure.md:1` for file roles.
Follow `docs/examples.md:1` and build the ReAct tool agent first.

**Why uv-only?**
One tool covers install, run, lint, and test.
This avoids drift between local and clean environments.

## References

- Application structure - Docs by LangChain - https://docs.langchain.com/oss/python/langgraph/application-structure
- langchain-ai/new-langgraph-project - https://github.com/langchain-ai/new-langgraph-project
- langchain-ai/langgraph-example-pyproject - https://github.com/langchain-ai/langgraph-example-pyproject
- wassim249/fastapi-langgraph-agent-production-ready-template - https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template
- Workflows and agents patterns - https://docs.langchain.com/oss/python/langgraph/workflows-agents
