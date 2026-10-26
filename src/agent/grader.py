"""Draft plus grade loop: generator drafts, grader checks, guard accepts last.

The grader calls the model with validated structured output and rejects
route back with feedback. The guard sits inside the grader on entry:
at 2 completed rejections the last draft is accepted without spending
another model call, so the loop always terminates.
"""

import logging
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from agent.nodes import last_human_text
from agent.routes import grade_route
from agent.state import GraphState

logger = logging.getLogger("agent")


class GradeVerdict(BaseModel):
    """Validated grader output: approval plus feedback for the next draft."""

    approved: bool
    feedback: str = ""


DRAFT_DEGRADED = "Thanks for reaching out. I will follow up shortly."

MAX_GRADE_REJECTIONS = 2


def _text(result: Any) -> str:
    """Extract reply text; non-string blocks stringify at the edge."""
    content = result.content if isinstance(result, AIMessage) else result
    if isinstance(content, str):
        return content
    return str(content)


def _normalize_verdict(result: Any) -> GradeVerdict:
    """Coerce structured or plain-text verdicts; unparseable text rejects."""
    if isinstance(result, GradeVerdict):
        return result
    text = _text(result).strip()
    if text.upper().startswith("APPROV"):
        return GradeVerdict(approved=True)
    if text.upper().startswith("REJECT"):
        feedback = text.split(":", 1)[1].strip() if ":" in text else text
        return GradeVerdict(approved=False, feedback=feedback or "needs improvement")
    return GradeVerdict(approved=False, feedback="unparseable verdict")


def _grade_messages(draft: str) -> list[BaseMessage]:
    """Grading prompt; the model replies with approval or actionable feedback.

    The draft travels inside explicit tags so untrusted ticket text can
    never read as instructions.
    """
    return [
        SystemMessage(
            content="You grade customer-support replies. Reply APPROVED when the draft "
            "is polite and resolves the request, else REJECT: plus one line of feedback."
        ),
        HumanMessage(content=f"Draft to grade:\n<draft>\n{draft}\n</draft>"),
    ]


def build_drafter_sync(llm: Any) -> Any:
    """Return the sync drafter; revises using feedback when present."""

    def drafter_sync(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
        text = last_human_text(state.get("messages", []))
        if llm is None:
            return {"messages": [AIMessage(content=f"Echo: {text}")]}
        history: list[BaseMessage] = list(state.get("messages", []))
        feedback = (state.get("grade_feedback") or "").strip()
        if feedback:
            history = history + [HumanMessage(content=f"Reviewer feedback: {feedback}")]
        prompt: list[BaseMessage] = [
            SystemMessage(content="Write a polite customer-support reply.")
        ] + history
        try:
            result = llm.call(prompt)
        except Exception as exc:  # noqa: BLE001 - drafter failure still grades
            logger.warning("drafter degraded: %s", exc)
            return {"messages": [AIMessage(content=DRAFT_DEGRADED)]}
        content = _text(result)
        return {"messages": [AIMessage(content=content)]}

    return drafter_sync


def build_drafter_async(llm: Any) -> Any:
    """Return the async drafter; revises using feedback when present."""

    async def drafter_async(
        state: GraphState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        text = last_human_text(state.get("messages", []))
        if llm is None:
            return {"messages": [AIMessage(content=f"Echo: {text}")]}
        history: list[BaseMessage] = list(state.get("messages", []))
        feedback = (state.get("grade_feedback") or "").strip()
        if feedback:
            history = history + [HumanMessage(content=f"Reviewer feedback: {feedback}")]
        prompt: list[BaseMessage] = [
            SystemMessage(content="Write a polite customer-support reply.")
        ] + history
        try:
            result = await llm.acall(prompt)
        except Exception as exc:  # noqa: BLE001 - drafter failure still grades
            logger.warning("drafter degraded: %s", exc)
            return {"messages": [AIMessage(content=DRAFT_DEGRADED)]}
        content = _text(result)
        return {"messages": [AIMessage(content=content)]}

    return drafter_async


def build_grader_sync(llm: Any) -> Any:
    """Return the sync grader; guard accepts the last draft at 2 rejections."""

    def grader_sync(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
        drafts = [m for m in state.get("messages", []) if isinstance(m, AIMessage)]
        if not drafts:
            return {"messages": []}
        attempts = state.get("attempts") or 0
        if attempts >= MAX_GRADE_REJECTIONS:
            logger.warning("grader guard: accepting last draft after %d rejections", attempts)
            return {"attempts": 0, "grade_feedback": ""}
        if llm is None:
            return {"attempts": 0, "grade_feedback": ""}
        try:
            result = llm.call(_grade_messages(_text(drafts[-1])), response_format=GradeVerdict)
        except Exception as exc:  # noqa: BLE001 - grader failure counts a rejection
            logger.warning("grader degraded: %s", exc)
            return {"attempts": attempts + 1, "grade_feedback": "grading unavailable"}
        verdict = _normalize_verdict(result)
        if verdict.approved:
            return {"attempts": 0, "grade_feedback": ""}
        return {"attempts": attempts + 1, "grade_feedback": verdict.feedback}

    return grader_sync


def build_grader_async(llm: Any) -> Any:
    """Return the async grader; guard accepts the last draft at 2 rejections."""

    async def grader_async(
        state: GraphState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        drafts = [m for m in state.get("messages", []) if isinstance(m, AIMessage)]
        if not drafts:
            return {"messages": []}
        attempts = state.get("attempts") or 0
        if attempts >= MAX_GRADE_REJECTIONS:
            logger.warning("grader guard: accepting last draft after %d rejections", attempts)
            return {"attempts": 0, "grade_feedback": ""}
        if llm is None:
            return {"attempts": 0, "grade_feedback": ""}
        try:
            result = await llm.acall(
                _grade_messages(_text(drafts[-1])), response_format=GradeVerdict
            )
        except Exception as exc:  # noqa: BLE001 - grader failure counts a rejection
            logger.warning("grader degraded: %s", exc)
            return {"attempts": attempts + 1, "grade_feedback": "grading unavailable"}
        verdict = _normalize_verdict(result)
        if verdict.approved:
            return {"attempts": 0, "grade_feedback": ""}
        return {"attempts": attempts + 1, "grade_feedback": verdict.feedback}

    return grader_async


def build_grader_graph(llm: Any) -> Any:
    """Compile the standalone draft plus grade demo with a bounded loop."""
    drafter_sync = build_drafter_sync(llm)
    grader_sync = build_grader_sync(llm)
    drafter_async = build_drafter_async(llm)
    grader_async = build_grader_async(llm)

    builder = StateGraph(GraphState)
    builder.add_node("drafter", RunnableLambda(drafter_sync, afunc=drafter_async))
    builder.add_node("grader", RunnableLambda(grader_sync, afunc=grader_async))
    builder.add_edge(START, "drafter")
    builder.add_edge("drafter", "grader")
    builder.add_conditional_edges("grader", grade_route, {"drafter": "drafter", "end": END})
    return builder.compile()
