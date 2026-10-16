# Testing

This repo has three test layers with different isolation and cost.
Unit is the default gate, integration runs on purpose, E2E needs the full stack.
All layers run through `uv`, never pip.

## 1. Unit tests: no Docker, no real database

Unit tests run entirely in-memory under pytest in `tests/unit_tests/`.
There is no database and no `docker compose up`.
Database calls, LLM calls, and external APIs are mocked or stubbed with fakes.
Fake chat models and stub tools cover nodes, routing, and reducers deterministically.
This keeps unit tests blindingly fast because nothing waits on network or disk.
Run them with `make test` or a single file with `make test TEST_FILE=tests/unit_tests/test_graph.py`.

## 2. Integration tests: ephemeral Docker database

Integration tests talk to real dependencies to prove queries and wiring actually work.
They live in `tests/integration_tests/` and run via `make integration_tests`.
Each run spins an ephemeral postgres container on a random host port with a dedicated test database.
That container is never the development database and is stopped after the suite.
Isolate cases by wiping tables, wrapping each test in a rolled-back transaction, or reseeding per test.
These tests may call real models, so they cost time and money and never run by default.

## 3. End-to-end tests: the full stack

API journeys prove a real user against the full running stack over HTTP.
The suite lives in `tests/integration_tests/test_api_journeys.py:1` and runs via `make integration_tests`.
Each run uses an ephemeral pgvector Postgres on a random host port with a dedicated test database.
Set `TEST_POSTGRES_URI` to reuse a compose override store instead of spinning a container.
The lifespan wires the real checkpointer, memory, and cache against that store only.
Each test seeds predictable users with fresh session threads through register plus login.
Journeys cover auth lifecycle, chat with thread resume across restart, history isolation across users, and 401 plus 403 plus 404 plus 422 cases.
The loop answers through a fake echo, so the suite stays network-free apart from the ephemeral store.
The container stops after the module, so local development data is never polluted.
Drive the stack over HTTP against `POST /api/v1/chat` with a session token.
See `docs/authentication.md:1` for the auth flow.
There is no frontend in this repo, so E2E means API-level journeys, not browser automation.
