# Examples

This file specs the five example agents to build in this repo.
Each one demonstrates a core LangGraph workflow pattern.
Example 1 (ReAct tool agent) plus examples 2-5 are live; build in order, router last.
All examples live under `src/agent/:1`.
Extra specialists join as subgraph nodes sharing `GraphState` in `src/agent/state.py:1`.

## 1. ReAct tool agent

Answers "book me tomorrow 10am" by looping `chat -> tools -> chat`.
Tools live in `src/agent/tools/booking.py:1` as `get_availability`, `book_appointment`.
The `chat` node binds the `tools` list with `bind_tools`.
The service rebinds them on fallback inside `src/services/llm/service.py:1`.
The loop ends when the LLM reply has no `tool_calls`.
Done when the agent books a slot and confirms time back to the user.

## 2. RAG assistant

Answers "what is your refund policy" via `retrieve -> generate` nodes in `src/agent/retrieval.py:1`.
`retrieve` refreshes the `passages` list in `src/agent/state.py:1` from the shared `("passages", "refund-policy")` namespace on every turn.
Long term memory stays per-user recall in `docs` as extra context only and never overrides grounding.
`generate` cites `passages` for facts and answers unknown when nothing supports an answer.
Done when an off-policy question gets "I don't know" instead of a hallucinated answer.

## 3. Router / triage

Entry `triage_route` in `src/agent/routes.py:1` sends each request to RAG assistant, booking, or chat.
Rule: refund or status wording goes to retrieval, date or time wording goes to booking, everything else goes to chat.
Both families matched goes to the `triage_assist` node in `src/agent/triage.py:1`, which stores a validated `TriageLabel` and falls back to chat when unparseable.
The booking branch reuses the chat plus tools loop sharing state and persistence.
Done when all three trigger queries route to the right branch plus one ambiguous query resolves via `triage_assist`.

## 4. Parallel workers

Handles "summarize this ticket" with three static workers plus one aggregator in `src/agent/workers.py:1`.
All workers read the same ticket text and write disjoint keys (`sentiment`, `urgency`, `category`), so no reducer is needed.
One `aggregator` node merges the three fields plus the ticket id into a single triage card.
A worker failure degrades its field to unknown instead of dropping the card.
Done when the card contains all three fields plus the ticket id.

## 5. Evaluator-optimizer

A `drafter` node drafts the reply and a `grader` node checks it via the model fallback with validated structured output (`GradeVerdict`) in `src/agent/grader.py:1`.
The pure `grade_route` in `src/agent/routes.py:1` sends rejections back with feedback and ends when feedback clears.
Guard: at 2 completed rejections the grader accepts the last draft without another model call.
Done when a rude first draft converges to an approved reply within the guard.

## Shared wiring

The main chat graph uses `make_the_agent(checkpointer, *, store, cache, cache_ttl_seconds, llm)` with `AsyncPostgresSaver` per `thread_id` per `docs/structure.md:140`.
Ticket summarization and draft grading are standalone demo graphs compiled by `build_ticket_graph` and `build_grader_graph`; they share `GraphState` but run outside the chat loop.
Checkpointer tables `checkpoints`, `checkpoint_blobs`, and `checkpoint_writes` belong to LangGraph per `docs/structure.md:192`.
Long term memory lives in `src/services/memory.py:1` with cache in `src/core/cache.py:1`.
