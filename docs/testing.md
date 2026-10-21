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
AC-09 plus AC-10 ride here as artifacts: `test_time_request_converges_to_confirmation` proves the time-slot loop ends in confirmation, and the fallback plus budget tests in `tests/unit_tests/test_llm_service.py:1` prove retries with circular fallback under the total budget.

## 2. Integration tests: ephemeral Docker database

Integration tests talk to real dependencies to prove queries and wiring actually work.
They live in `tests/integration_tests/` and run via `make integration_tests`.
Spin up fresh containers just for testing with Testcontainers or a planned override compose file.
That override file does not exist yet.
Use a separate test database, never the development one.
Isolate cases by wiping tables, wrapping each test in a rolled-back transaction, or reseeding per test.
These tests may call real models, so they cost time and money and never run by default.

## 3. End-to-end tests: the full stack

E2E tests will simulate a real user against the entire running stack.
That full stack is planned and does not exist yet.
Bring it up with a full `docker compose up` once it lands: API server plus real postgres plus planned cache.
Drive the stack over HTTP against `POST /api/v1/chat` with a session token.
See `docs/authentication.md:1` for the auth flow.
Seed the database with a known predictable state before the suite runs.
Tear the stack down after, so local development data is never polluted.
There is no frontend in this repo, so E2E means API-level journeys, not browser automation.
