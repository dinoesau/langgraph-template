# Getting Started

This guide takes a fresh checkout to the first graph answer.
Every step uses uv only.
The echo loop plus HTTP skeleton are live and the setup below already works.

## Prerequisites

- Install uv with [the official uv guide](https://docs.astral.sh/uv/).
- Install Docker with Compose only when you want local Postgres.
- Get a key from at least one LLM provider.
- Use Python 3.11 or newer as declared in `pyproject.toml:6`.
- You need no other package manager.

## Setup

Clone the repository and enter the folder.

```bash
git clone <your-repo-url> langgraph-template
cd langgraph-template
```

Copy the example env file.

```bash
cp .env.example .env
```

Fill at least one LLM key in `.env:1`.
Install dependencies with uv.

```bash
uv sync
```

This matches the `install` target in `Makefile:8`.
Run the unit tests to confirm the toolchain.

```bash
uv run pytest
```

Start optional Postgres in the background.

```bash
docker compose up -d postgres
```

The service is defined in `docker-compose.yml:2`.
Start the LangGraph dev server.

```bash
uv run langgraph dev
```

This matches the `dev` target in `Makefile:11`.

## First call

The graph module `src/agent/graph.py:1` ships an echo loop with no models.
It answers greetings with no provider keys and the tool loop arrives later.
Once the module exports `graph`, the dev server exposes Studio on the printed URL.
Open that Studio URL in the browser.
Select the `agent` graph declared in `langgraph.json:1`.
Create a thread for your first conversation.
Send a greeting message and read the answer.
Use the ReAct prompt in `docs/examples.md:8` for the first tool loop.

## Next steps

- Read `docs/architecture.md:1` for the system diagram.
- Read `docs/configuration.md:1` for every env var.
- Read `docs/structure.md:1` for file roles and growth rules.
- Read `docs/examples.md:1` for the five agent specs.

## Troubleshooting

The dev server fails to load when the graph export drifts from `langgraph.json:1`.
The database refuses connections when Postgres is down or `POSTGRES_URI` is wrong.
Check the container with `docker compose ps` against `docker-compose.yml:2`.
Rerun `uv sync` when imports fail after pulling new changes.
Confirm the toolchain with `uv run pytest` before opening an issue.
