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
Without `POSTGRES_URI` the graph runs ephemeral; with it set, threads persist per `thread_id`.

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `POSTGRES_URI` | empty (ephemeral loop) | no | Full connection string; enables persisted threads when set |
| `POSTGRES_USER` | `postgres` | no | User for the compose service |
| `POSTGRES_PASSWORD` | `postgres` | no | Password for the compose service |
| `POSTGRES_DB` | `postgres` | no | Database name for the compose service |

`POSTGRES_URI` maps to `.env.example:5`.
The compose values map to `docker-compose.yml:7` through `docker-compose.yml:9`.
Start the service when you need persistence.

```bash
docker compose up -d postgres
```

## Settings

`src/core/config.py:1` loads typed settings with documented defaults.
Local runs read `.env`, deployed runs use real environment values.

| Variable | Default | Status | Description |
| --- | --- | --- | --- |
| `APP_ENV` | `development` | live | Selects development, staging, or production behavior |
| `LOG_LEVEL` | `INFO` | live | Controls console verbosity for local runs |
| `APP_HOST` | `127.0.0.1` | live | Host for the local HTTP server |
| `APP_PORT` | `8000` | live | Port for the local HTTP server |
| `CACHE_TTL_SECONDS` | `60` | live | Memory search cache TTL |
| `LONG_TERM_MEMORY_COLLECTION_NAME` | `longterm_memory` | live | Logical vector collection name |
| `LONG_TERM_MEMORY_MODEL` | empty | live | Reserved for LLM extraction |
| `LONG_TERM_MEMORY_EMBEDDER_MODEL` | empty | live | Reserved for real embeddings |
| `VALKEY_HOST` | empty | live | Enables shared cache; empty keeps in-memory fallback |
| `DEFAULT_LLM_MODEL` | planned | planned | Starting model for the chat node |
| `DEFAULT_LLM_TEMPERATURE` | planned | planned | Sampling temperature for chat completions |
| `MAX_TOKENS` | planned | planned | Cap per LLM response |
| `JWT_SECRET_KEY` | planned | planned | Signs auth tokens once the auth layer exists |
| `LANGFUSE_TRACING_ENABLED` | planned | planned | Disables tracing during development when false |

## Validation

Run `uv run pytest` to confirm the toolchain.
Run `docker compose ps` to confirm Postgres.
Open the Studio URL to confirm the graph loads.
Reread `docs/getting-started.md:1` when any step fails.
