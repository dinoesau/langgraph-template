# Configuration

All settings come from environment variables.
Local dev reads `.env` through the pointer in `langgraph.json:1`.
Typed settings live in `src/core/config.py:1`.

## Setup

Copy the template file to create your local env.

```bash
cp .env.example .env
```

The template lives in `.env.example:1`.
Never commit `.env` to git.
Production sets real values in the deploy environment instead.

## LLM keys

Set at least one provider key before starting the dev server.

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | empty | one of the three | Key for OpenAI models |
| `ANTHROPIC_API_KEY` | empty | one of the three | Key for Anthropic models |
| `GOOGLE_API_KEY` | empty | one of the three | Key for Google models |
| `LANGSMITH_API_KEY` | empty | no | Enables LangSmith tracing when set |

Each key maps to `.env.example:1` through `.env.example:4` in order.

## Database

Postgres is optional and local dev works without it.
The lifespan wires `AsyncPostgresSaver` when `POSTGRES_URI` is reachable and degrades to stateless otherwise.
`POSTGRES_*` are live in `src/core/config.py:1` with the defaults below.
An empty `POSTGRES_URI` disables persistence and reports `not_configured`.
`GET /health` reports `ready`, `not_configured`, or `unavailable` in `dependencies.store`.

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `POSTGRES_URI` | `postgresql://postgres:postgres@localhost:5432/postgres` | no | Full connection string for local dev, empty disables persistence |
| `POSTGRES_USER` | `postgres` | no | User for the compose service |
| `POSTGRES_PASSWORD` | `postgres` | no | Password for the compose service |
| `POSTGRES_DB` | `postgres` | no | Database name for the compose service |

`POSTGRES_URI` maps to `.env.example:5`.
The compose values map to `docker-compose.yml:7` through `docker-compose.yml:9`.
Start the service when you need persistence.
Apply app migrations with `make migrate` once Postgres is up.

```bash
docker compose up -d postgres
```

## App settings

These variables are live in `src/core/config.py:1`.
They fail fast with a validation error on bad values.

| Variable | Default | Status | Description |
| --- | --- | --- | --- |
| `APP_ENV` | `development` | Existe | Selects development, staging, or production behavior |
| `LOG_LEVEL` | `INFO` | Existe | Controls console verbosity for local runs |
| `HOST` | `127.0.0.1` | Existe | Bind host for the API server |
| `PORT` | `8000` | Existe | Bind port for the API server, 1 to 65535 |
| `POSTGRES_URI` | `postgresql://...` | Existe | Full connection string, empty disables persistence |
| `POSTGRES_USER` | `postgres` | Existe | User for the compose service |
| `POSTGRES_PASSWORD` | `postgres` | Existe | Password for the compose service |
| `POSTGRES_DB` | `postgres` | Existe | Database name for the compose service |
| `JWT_SECRET_KEY` | dev placeholder | Existe | Signs auth tokens, 32+ chars, rotate in production |
| `JWT_ACCESS_TOKEN_EXPIRE_DAYS` | `7` | Existe | Days until user and session tokens expire, 1 to 365 |

`APP_ENV` maps to `.env.example:6`.
`LOG_LEVEL` maps to `.env.example:7`.
`HOST` maps to `.env.example:8`.
`PORT` maps to `.env.example:9`.
`JWT_SECRET_KEY` maps to `.env.example:10`.
`JWT_ACCESS_TOKEN_EXPIRE_DAYS` maps to `.env.example:11`.

## Memory and cache

These variables are live in `src/core/config.py:1`.
They control per-user recall, the shared cache front, and background writes.
See `docs/memory.md:1` for the full memory guide.

| Variable | Default | Status | Description |
| --- | --- | --- | --- |
| `CACHE_TTL_SECONDS` | `60` | Existe | TTL for cached memory search, 1 to 3600 seconds |
| `VALKEY_HOST` | empty | Existe | Enables the shared cache once set, empty keeps the in-memory fallback |
| `VALKEY_PORT` | `6379` | Existe | Port for the shared cache service |
| `LONG_TERM_MEMORY_COLLECTION_NAME` | `longterm_memory` | Existe | Vector collection name for the memory store |
| `LONG_TERM_MEMORY_EMBEDDER_MODEL` | empty | Existe | Reserved embedder name, embeddings are an offline placeholder today |
| `LONG_TERM_MEMORY_MODEL` | empty | Existe | Reserved extraction model name |
| `LONG_TERM_MEMORY_TOP_K` | `3` | Existe | Memories injected as context per turn, 1 to 20 |

Chat identity always comes from the session token, so recall is scoped to the token owner.
See `docs/authentication.md:1` for the token scopes.

## Planned settings

These names are reserved for `src/core/config.py:1` and have no effect today.
Do not rely on them until each module ships.

| Variable | Default | Status | Description |
| --- | --- | --- | --- |
| `DEFAULT_LLM_MODEL` | planeado | planeado | Starting model for the chat node |
| `DEFAULT_LLM_TEMPERATURE` | planeado | planeado | Sampling temperature for chat completions |
| `MAX_TOKENS` | planeado | planeado | Cap per LLM response |
| `LANGFUSE_TRACING_ENABLED` | planeado | planeado | Disables tracing during development when false |

## Validation

Run `uv run pytest` to confirm the toolchain.
Run `docker compose ps` to confirm Postgres.
Open the Studio URL to confirm the graph once `src/agent/graph.py:1` exists.
Reread `docs/getting-started.md:1` when any step fails.
