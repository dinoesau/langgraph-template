from langchain_core.messages import HumanMessage

from src.agent.graph import graph
from src.agent.nodes import chat


def test_chat_node() -> None:
    result = chat({"messages": [HumanMessage(content="hello")]})
    assert len(result["messages"]) == 1
    assert result["messages"][0].content == "hello"


def test_chat_node_empty_history() -> None:
    result = chat({"messages": []})
    assert len(result["messages"]) == 1
    assert result["messages"][0].content == ""


def test_graph_invoke() -> None:
    result = graph.invoke({"messages": [HumanMessage(content="hello")]})
    messages = result["messages"]
    assert len(messages) == 2
    assert messages[-1].content == "hello"
