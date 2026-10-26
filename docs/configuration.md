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
| `POSTGRES_POOL_MAX_SIZE` | `5` | no | Max connections for the Postgres store pool |
| `POSTGRES_USER` | `postgres` | no | User for the compose service |
| `POSTGRES_PASSWORD` | `postgres` | no | Password for the compose service |
| `POSTGRES_DB` | `postgres` | no | Database name for the compose service |

`POSTGRES_URI` maps to `.env.example:9` and `POSTGRES_POOL_MAX_SIZE` to `.env.example:10`.
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
| `VALKEY_HOST` | empty | live | Enables shared cache; empty keeps in-memory fallback |
| `DEFAULT_LLM_MODEL` | `default-openai` | live | Starting model for the chat node |
| `DEFAULT_LLM_TEMPERATURE` | `0.2` | live | Sampling temperature for chat completions |
| `MAX_LLM_CALL_RETRIES` | `3` | live | Attempts per model before fallback |
| `LLM_TOTAL_TIMEOUT_SECONDS` | `60` | live | Total budget for the retry plus fallback cycle |
| `MAX_TOKENS` | planned | planned | Cap per LLM response |
| `JWT_SECRET_KEY` | empty | live | Signs auth tokens, fails closed when empty |
| `JWT_ALGORITHM` | `HS256` | live | JWT signing algorithm |
| `JWT_USER_EXPIRE_MINUTES` | `30` | live | User token lifetime in minutes |
| `JWT_SESSION_EXPIRE_MINUTES` | `60` | live | Session token lifetime in minutes |
| `LANGSMITH_TRACING` | `false` | live | Enables LangSmith tracing when true |
| `LANGFUSE_TRACING_ENABLED` | `false` | live | Enables Langfuse long-term traces when true plus keys set |
| `LANGFUSE_PUBLIC_KEY` | empty | live | Public key for Langfuse, empty keeps tracing offline |
| `LANGFUSE_SECRET_KEY` | empty | live | Secret key for Langfuse, empty keeps tracing offline |
| `LANGFUSE_HOST` | empty | live | Custom Langfuse host, empty uses default cloud |
| `DEBUG` | `false` | live | Enables debug-only slow-request profiles when true |
| `SLOW_REQUEST_THRESHOLD_SECONDS` | `1.0` | live | Profile threshold in seconds, zero profiles every request |

OpenAPI `servers` lists only the local URL built from `APP_HOST` plus `APP_PORT` in `src/api/app.py:24`.
Re-export `openapi.json` after changing host or port so Bruno imports resolve `baseUrl`.

Turn cap `DEFAULT_RECURSION_LIMIT` of `25` in `src/agent/graph.py:45` applies per HTTP turn in `src/api/v1/chat.py:47,65`; stubborn tool loops surface as `503` via the app handler.

## Rate limits

Register allows 3 requests per minute per IP, login allows 5 per minute per IP.
Limits are enforced in `src/core/limiter.py:1` with per-process memory storage.
Session create, chat, rename, and delete carry no limits in this slice: they
require valid tokens, so brute force is not the threat and abuse control is
a follow-up. Per-account login throttling is also deferred: keying on the
form address is infeasible in a sync key function, and IP limits plus argon2
cost plus generic errors are the spec controls against credential stuffing.

## Validation

Run `uv run pytest` to confirm the toolchain.
Run `docker compose ps` to confirm Postgres.
Open the Studio URL to confirm the graph loads.
Reread `docs/getting-started.md:1` when any step fails.
