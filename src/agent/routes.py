"""Pure routing: state in, next node name out, no side effects.

Thin wrapper over the prebuilt ``tools_condition`` so the seam stays
documented here while the branching logic stays canonical.
"""

from typing import Any, Literal, cast

from langgraph.prebuilt import tools_condition

from agent.state import GraphState


def should_continue(state: GraphState) -> Literal["tools", "__end__"]:
    """Return the tools branch when the last AI message holds tool calls."""
    return tools_condition(cast(dict[str, Any], state))


def grade_route(state: GraphState) -> Literal["drafter", "end"]:
    """Return the grader-loop branch; feedback present means re-draft."""
    if state.get("grade_feedback"):
        return "drafter"
    return "end"
