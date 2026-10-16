# Getting Started

This guide takes a fresh checkout to the first graph answer.
Every step uses uv only.
S0 ships an echo graph and the setup below already works.

## Prerequisites

- Install uv with [the official uv guide](https://docs.astral.sh/uv/).
- Install Docker with Compose only when you want local Postgres.
- No LLM key is needed for the S0 echo graph.
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

No LLM key is needed for the S0 echo graph.
`.env:1` stays optional until later milestones need providers or Postgres.
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
S0 needs no database and this step stays optional.

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

The graph module `src/agent/graph.py:1` exports `graph`.
The dev server exposes Studio on the printed URL.
Open that Studio URL in the browser.
Select the `agent` graph declared in `langgraph.json:1`.
Create a thread for your first conversation.
Send a greeting message and read the echoed answer.
The echo carries no LLM call, so no key or database is needed.

## Next steps

- Read `docs/architecture.md:1` for the system diagram.
- Read `docs/configuration.md:1` for every env var.
- Read `docs/structure.md:1` for file roles and growth rules.
- Read `docs/examples.md:1` for the five agent specs.

## Troubleshooting

The dev server reports a missing graph when `src/agent/graph.py:1` fails to export `graph`.
The database refuses connections when Postgres is down or `POSTGRES_URI` is wrong.
Check the container with `docker compose ps` against `docker-compose.yml:2`.
Rerun `uv sync` when imports fail after pulling new changes.
Confirm the toolchain with `uv run pytest` before opening an issue.
