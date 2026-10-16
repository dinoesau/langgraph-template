# Examples

This file specs the five example agents to build in this repo.
Each one demonstrates a core LangGraph workflow pattern.
None is implemented yet; build in order, router last.
All examples live under `ws/src/agent/:1`.
Extra specialists join as subgraph nodes sharing `GraphState` in `ws/src/agent/state.py:1`.

## 1. ReAct tool agent

Status: implemented.
Answers "book me tomorrow 10am" by looping `chat -> tool_call -> chat`.
Tools live in `ws/src/agent/tools/booking.py:1` as `get_availability`, `book_appointment`, `get_order_status`.
The `chat` node binds the `tools` list with `bind_tools`.
The service rebinds them on fallback inside `ws/src/services/llm/service.py:1`.
The loop ends when the LLM reply has no `tool_calls`.
Done when the agent books a slot and confirms time back to the user.

## 2. RAG assistant

Status: implemented.
Answers "what is your refund policy" via `retrieve -> generate` nodes in `ws/src/agent/rag.py:1`.
`GraphState` in `ws/src/agent/state.py:1` uses `MessagesState` with `messages` plus a small `docs` list.
The retriever in `ws/src/services/retrieval.py:1` returns top-k chunks for the last user message only.
The generator cites `docs` for facts, says "I don't know" when unsupported, and uses long term memory only as extra context from `ws/src/services/memory.py:1`.
Memory never overrides retrieved grounding: the prompt labels passages authoritative and memory background only.
Done when an off-policy question gets "I don't know" instead of a hallucinated answer.

## 3. Router / triage

Status: implemented.
Entry `intention_route` in `ws/src/agent/routes.py:1` sends each request to RAG assistant, booking, or chat.
Rule first: refund/status/policy goes to the RAG assistant, date/time/booking words or date/time patterns go to booking, the rest falls to model assist.
Model assist calls `llm_service.call` in `ws/src/services/llm/service.py:1` with `response_format=IntentDecision` for ambiguous requests only.
The verdict shape is `IntentDecision` in `ws/src/agent/routes.py:1` with `intent` of rag, booking, or chat, defaulting to chat on failure.
The booking node is a subgraph reusing the ReAct loop from example 1 sharing `GraphState` in `ws/src/agent/state.py:1`.
The subgraph compiles without a checkpointer so it inherits the parent one with no extra persistence setup.
Wiring uses `add_conditional_edges` from `START` via `intention_route` in `ws/src/agent/graph.py:1` with retrieve, booking, and chat targets.
The RAG branch reuses `retrieve` plus `generate` from `ws/src/agent/rag.py:1` and booking tools stay in `ws/src/agent/tools/booking.py:1`.
The router compiles via `make_router_agent(checkpointer)` sharing `GraphState`.
Done when all three trigger families route to the right branch end to end.

## 4. Parallel workers

Status: implemented.
Handles "summarize this ticket" by fanning out with `Send` to `sentiment`, `urgency`, and `category` workers.
Fan-out lives in `ws/src/agent/triage.py:1` via `fan_out` returning one `Send` per worker with the same ticket text.
All workers read the same ticket text and write to a shared key in parallel.
Workers call `llm_service.call` in `ws/src/services/llm/service.py:1` and stay pure plus deterministic.
The shared `results` key uses `Annotated[list, operator.add]` so parallel writes append without clobber.
One `aggregator` node merges the three outputs into a single triage card.
The card carries `ticket_id` plus `sentiment` plus `urgency` plus `category` via `make_triage_graph(checkpointer)`.
Done when the card contains all three fields plus the ticket id.

## 5. Evaluator-optimizer

Status: implemented.
A `generator` node drafts the reply and a `grader` node checks it via `llm_service.call` with `response_format`.
The service applies `with_structured_output` internally per `ws/src/services/llm/service.py:1`.
The verdict shape is `GradeDecision` in `ws/src/agent/nodes.py:1` with `approved` plus `feedback`.
On reject, the grader feedback routes back to the generator for another draft.
Guard iterations below 3 in `ws/src/agent/routes.py:1`, else accept the last draft to avoid infinite loops.
The loop compiles via `make_evaluator_agent` in `ws/src/agent/graph.py:1` sharing `GraphState`.
Done when a rude first draft converges to an approved reply within the guard.

## Shared wiring

All graphs use `make_the_agent(checkpointer)` with `AsyncPostgresSaver` per `thread_id` per `ws/docs/structure.md:132`.
Checkpointer tables `checkpoints`, `checkpoint_blobs`, and `checkpoint_writes` belong to LangGraph per `ws/docs/structure.md:184`.
Long term memory lives in `ws/src/services/memory.py:1` with cache in `ws/src/core/cache.py:1`.
