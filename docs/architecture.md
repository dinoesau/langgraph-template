# Architecture

This doc shows how the starter fits together today and where it grows next.
S0 ships the echo graph in `src/agent/graph.py:1` with state and a pure chat node.
S1 adds the HTTP skeleton in `src/api/app.py:1` with typed settings.
S2a adds persisted threads with a lifespan checkpointer plus app migrations.
S2b adds per-user semantic memory with a shared cache front plus background writes.
S3 adds the chat to tool to chat loop with model retries and fallback.
S4 adds auth with scoped JWTs.
Observability is still marked as planned.
The layout follows the spec in `docs/structure.md:1`.

## System overview

```mermaid
graph TB
    Studio["Studio (browser)"] --> Dev["Dev server"]
    Dev --> Graph["Graph (S0 echo)"]
    Graph --> Chat["chat node (S0 echo)"]
    Graph --> ToolCall["tool_call node (planeado S3)"]
    Chat --> LLM["LLM provider (planeado)"]
    ToolCall --> Tools["tools (planeado)"]
    Graph <--> Ckpt["Checkpointer (S2a lifespan)"]
    Ckpt --> PG[("PostgreSQL plus pgvector (S2b)")]
    Graph --> API["HTTP API (S1 skeleton)"]
    API --> Mem["MemoryService (S2b per user)"]
    Mem --> Cache["Cache (S2b Valkey or memory)"]
    Mem --> PG
    Cfg["Config (S1 typed)"] -.-> Graph
```

Studio is the local client until the HTTP API exists.
The dev server loads the `agent` graph declared in `langgraph.json:1`.
Postgres is optional and persists checkpoints once the lifespan checkpointer connects.

## Component responsibilities

| Component | Path | Status | Responsibility |
| --- | --- | --- | --- |
| Deploy contract | `langgraph.json:1` | Existe | Maps the `agent` name to the graph variable and points env to `.env` |
| Package state | `src/agent/state.py:1` | Existe | Defines `GraphState` with `messages` only |
| Graph wiring | `src/agent/graph.py:1` | Existe | Builds the chat to tool_call loop and exports `graph` |
| Nodes | `src/agent/nodes.py:1` | Existe | Runs one LLM step and executes tool calls concurrently |
| Tools | `src/agent/tools/:1` | Existe | Holds one capability module per file behind a shared list |
| Routes | `src/agent/routes.py:1` | Existe | Keeps pure routing from state to the next node name |
| Prompts | `src/agent/prompts/:1` | Planeado | Stores system templates loaded once at startup |
| Context schema | `src/agent/configuration.py:1` | Planeado | Declares model name, limits, and store name |
| HTTP API | `src/api/v1/:1` | Existe | Skeleton translates HTTP into graph invoke or stream calls |
| Settings | `src/core/config.py:1` | Existe | Loads typed settings from env vars |
| Services | `src/services/:1` | Existe | Owns database access with lifespan checkpointer plus S2b memory and cache plus the S3 LLM fallback service |
| Checkpointer | AsyncPostgresSaver | Existe | Persists full state per thread for resume and multi-turn memory |
| Memory | `src/services/memory.py:1` | Existe | Recalls top memories per user behind the cache, writes in background |
| Cache | `src/core/cache.py:1` | Existe | Fronts Valkey with an in-memory TTL fallback |
| Storage | `docker-compose.yml:2` | Existe | Runs pgvector Postgres on port 5432 plus Valkey on port 6379 |
| Tests | `tests/unit_tests/:1` | Existe | Covers the loop, tools, and fallback with fake models |

## Request lifecycle

The S3 lifecycle runs today with the tool loop in `src/agent/graph.py:1`.
Each step below runs in order for one user message.

1. Studio sends a user message to the dev server.
2. The dev server loads the `agent` graph from `langgraph.json:1`.
3. The `chat` node calls one model entry with retries and circular fallback.
4. Replies with `tool_calls` run concurrently in `tool_call` and return to `chat`; replies without end the loop.
5. The lifespan checkpointer persists state per `thread_id` when Postgres is reachable, else the invoke stays stateless.
6. The final message returns to Studio.

The S1 HTTP lifecycle runs through `src/api/app.py:1`.

1. Client posts to `/api/v1/chat` or `/api/v1/chat/stream`.
2. `RequestContextMiddleware` assigns or reuses `X-Request-ID` and binds `thread_id` into the JSON access log to stdout.
3. The route awaits the loop with `graph.ainvoke` or `graph.astream`.
4. The reply returns with `thread_id` pass-through plus `X-Request-ID`; stream SSE data events carry `thread_id` and end with `[DONE]`.
5. `GET /health` returns status ok plus dependencies (`loop: ok`, `store: ready` or `not_configured` or `unavailable`, `cache: ready` or `not_configured` or `unavailable`). `GET /metrics` returns the stub.

Auth rides the session token per `docs/authentication.md:1`; rate limits stay planned; background memory writes are live in S2b.

## Agent graph

S3 runs the two-node loop below in `src/agent/graph.py:1`.
Pure routing lives in `src/agent/routes.py:1`, concurrent tools in `src/agent/nodes.py:1`.

```mermaid
graph LR
    START --> chat
    chat -->|tool calls present| tool_call
    tool_call --> chat
    chat -->|no tool calls| END
```

The S3 `chat` node calls one model entry with retries and circular fallback.
The `tool_call` node runs tools concurrently and feeds results back to `chat`.
The loop ends when the reply carries no tool calls.

## Growth path

Start with flat files in `src/agent/` per `docs/structure.md:5`.
Add `src/api/` only when shipping HTTP to users.
Add `src/core/` and `src/services/` only for shared concerns like config or memory.
Keep the checkpointer injected by factory and never hardcode it in the graph module.
Build the five agent specs in `docs/examples.md:1` in order and router last.
