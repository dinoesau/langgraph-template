# Examples

This file specs the five example agents to build in this repo.
Each one demonstrates a core LangGraph workflow pattern.
Example 1 (ReAct tool agent) is live; examples 2-5 are specs, build in order, router last.
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

Answers "what is your refund policy" via `retrieve -> generate` nodes.
`GraphState` in `src/agent/state.py:1` uses `MessagesState` with `messages` plus a small `docs` list.
The retriever returns top-k chunks for the last user message only.
The generator cites `docs` for facts and uses long term memory only as extra context from `src/services/memory.py:1`.
Done when an off-policy question gets "I don't know" instead of a hallucinated answer.

## 3. Router / triage

Entry `intention_route` in `src/agent/routes.py:1` sends each request to RAG assistant, booking, or chat.
Rule: refund/status questions go to the RAG assistant, date/time requests go to booking, everything else goes to chat.
The booking node is a subgraph with its own ReAct loop from example 1.
Done when all three trigger queries route to the right subgraph.

## 4. Parallel workers

Handles "summarize this ticket" by fanning out with `Send` to `sentiment`, `urgency`, and `category` workers.
All workers read the same ticket text and write to a shared key in parallel.
One `aggregator` node merges the three outputs into a single triage card.
Done when the card contains all three fields plus the ticket id.

## 5. Evaluator-optimizer

A `generator` node drafts the reply and a `grader` node checks it via `llm_service.call` with `response_format`.
The service applies `with_structured_output` internally per `src/services/llm/service.py:1`.
On reject, the grader feedback routes back to the generator for another draft.
Guard iterations below 3, else accept the last draft to avoid infinite loops.
Done when a rude first draft converges to an approved reply within the guard.

## Shared wiring

All graphs use `make_the_agent(checkpointer, *, store, cache, cache_ttl_seconds, llm)` with `AsyncPostgresSaver` per `thread_id` per `docs/structure.md:140`.
Checkpointer tables `checkpoints`, `checkpoint_blobs`, and `checkpoint_writes` belong to LangGraph per `docs/structure.md:192`.
Long term memory lives in `src/services/memory.py:1` with cache in `src/core/cache.py:1`.
