# EXAMPLES

This file specs the five example agents to build in this repo.
Each one demonstrates a core LangGraph workflow pattern.
None is implemented yet; build in order, router last.
All examples live under `src/agent/` until a second agent forces the `src/agents/<name>/` split.

## 1. ReAct tool agent

Answers "book me tomorrow 10am" by looping `chat -> ToolNode -> chat`.
Tools live in `tools.py`: `get_availability`, `book_appointment`, `get_order_status`.
The `chat` node binds all three tools with `bind_tools`.
The loop ends when the LLM reply has no `tool_calls`.
Done when the agent books a slot and confirms time back to the user.

## 2. RAG assistant

Answers "what is your refund policy" via `retrieve -> generate` nodes.
`state.py` holds `messages` plus retrieved `docs`.
The retriever returns top-k chunks for the last user message only.
The generator answers with citations from `docs` only, never from memory.
Done when an off-policy question gets "I don't know" instead of a hallucinated answer.

## 3. Router / triage

Entry `routes.py:intention_route` sends each request to `conversation` vs `booking` subgraph.
Rule: refund/status questions go to the RAG assistant, date/time requests go to booking, everything else goes to chat.
The booking node is a subgraph with its own ReAct loop from example 1.
Done when all three trigger queries route to the right subgraph.

## 4. Parallel workers

Handles "summarize this ticket" by fanning out with `Send` to `sentiment`, `urgency`, and `category` workers.
All workers read the same ticket text and write to a shared key in parallel.
One `aggregator` node merges the three outputs into a single triage card.
Done when the card contains all three fields plus the ticket id.

## 5. Evaluator-optimizer

A `generator` node drafts the reply, a `grader` node checks tone plus policy with `with_structured_output`.
On reject, the grader feedback routes back to the generator for another draft.
Guard iterations below 3, else accept the last draft to avoid infinite loops.
Done when a rude first draft converges to an approved reply within the guard.
