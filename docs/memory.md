# Memory

Long term memory is live for per-user recall with cache front.
Extraction is heuristic; LLM extraction arrives with later slices.
This guide describes the shipped S2b behavior plus the target design.

## Status

Short term state lives in the graph checkpointer.
`GraphState` in `src/agent/state.py:1` uses `MessagesState` with `messages` plus a small `docs` list.
Graphs use `make_the_agent(checkpointer, recall_hook, store_hook)` with `AsyncPostgresSaver` per `docs/structure.md:140`.
Recall is injected as `docs` on the async path; the sync path stays echo-only.
See `src/agent/graph.py:1` for the wiring point.
See `docs/structure.md:180` for the `src/services/memory.py:1` role.
See `docs/structure.md:173` for the `src/core/cache.py:1` role.
Semantic memory and cache exist with pgvector plus Valkey in compose and in-memory fallbacks in code.
Caller id is transitional `X-User-ID` via `src/api/deps.py:1` until S4 session tokens replace it.
Today `docker-compose.yml:1` starts pgvector plus Valkey.

## Overview

Each chat turn will load a small set of relevant memories.
Memories will be extracted from past conversations.
Memories will be stored as vector embeddings.
Retrieval will be semantic per user on each request.
The agent will receive them as plain context text.

```mermaid
sequenceDiagram
    participant G as LangGraph
    participant MS as MemoryService
    participant Cache as Cache
    participant M as MemoryStore
    participant PG as pgvector

    Note over G: On every chat request
    G->>MS: search user_id and query
    MS->>Cache: get memory key
    alt cache hit
        Cache-->>MS: cached result
    else cache miss
        MS->>M: semantic search
        M->>PG: vector similarity search
        PG-->>M: top-k memories
        M-->>MS: formatted results
        MS->>Cache: set key with TTL
    end
    MS-->>G: relevant memories string

    Note over G: After LLM response in background
    G-)MS: add user_id and messages
    MS->>M: extract and store embeddings
    M->>PG: store new vectors
```

## Cache layer

Memory search results will be cached for a short time.
The cache will avoid repeated vector queries for similar questions.
With Valkey the cache will be shared across app instances.
Without Valkey the code will fall back to an in-memory TTL cache.
The fallback will suit a single instance for local work.
Cache keys will look like `memory:{user_id}:{query_hash}`.
TTL will come from `CACHE_TTL_SECONDS` with a default of 60 seconds.
Only successful and non-empty results will be cached.
Errors will never be cached.

## Memory updates

Memory writes will run in the background after the response.
The response will return immediately without waiting for storage.
Memory work will not slow down the chat path.
Failures in background writes will be logged and ignored.
The chat response will stay correct even when memory fails.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| LONG_TERM_MEMORY_COLLECTION_NAME | longterm_memory | Vector collection name |
| LONG_TERM_MEMORY_MODEL | empty | LLM used to extract memories |
| LONG_TERM_MEMORY_EMBEDDER_MODEL | empty | Embedding model for search |
| CACHE_TTL_SECONDS | 60 | Memory search cache TTL |
| VALKEY_HOST | empty | Set to enable shared cache |

Values above are planned.
See `.env.example:1` for the current env template.
`POSTGRES_URI` in `.env.example:5` will back both checkpoints and vectors.

## Startup pre-warming

The service will open its vector connection at startup.
It will check the collection schema before serving traffic.
This will avoid paying cold init cost on the first request.
The call will live in the app lifespan when FastAPI exists.
See `docs/structure.md:181` for the database lifespan role.
Checkpointer tables `checkpoints`, `checkpoint_blobs`, and `checkpoint_writes` belong to LangGraph per `docs/structure.md:192`.
Tools live in `src/agent/tools/booking.py:1` as `get_availability`, `book_appointment`, `get_order_status`.

## Per-user isolation

Each user namespace will be isolated by `user_id`.
Search will only return vectors for the requesting user.
Users will never see memories from other users.
Session scope will still come from `thread_id` in the checkpointer.

## What exists today

`docker-compose.yml:1` starts pgvector on port 5432 plus Valkey on port 6379.
`pyproject.toml:1` carries `redis` for the shared cache front.
Vectors live in `memory_vectors`, excluded from Alembic and owned by the memory layer.
Embeddings are deterministic hash vectors until `LONG_TERM_MEMORY_EMBEDDER_MODEL` names a real model.
`langgraph.json:1` only maps the graph and env file.

## Next steps

Replace transitional `X-User-ID` with the S4 session token claim.
Replace heuristic extraction with LLM extraction once the model registry lands.
Tune `CACHE_TTL_SECONDS` plus top-k once S6 traces show hit rates.
