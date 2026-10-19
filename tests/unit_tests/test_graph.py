"""Unit tests for the echo loop.

No network and no containers: the loop answers from fixed input only.
"""

from typing import cast

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.graph import graph
from agent.state import GraphState


def test_echo_replies_with_fixed_greeting() -> None:
    result = graph.invoke({"messages": [HumanMessage(content="hello")]})
    messages = result["messages"]
    assert len(messages) == 2
    assert isinstance(messages[-1], AIMessage)
    assert messages[-1].content == "Echo: hello"


@pytest.mark.parametrize(
    "state",
    [{"messages": []}, cast(GraphState, {}), {"messages": [HumanMessage(content="   ")]}],
)
def test_echo_rejects_bad_input(state: GraphState) -> None:
    with pytest.raises(ValueError):
        graph.invoke(state)
