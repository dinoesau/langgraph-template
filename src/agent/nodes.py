from langchain_core.messages import AIMessage, HumanMessage

from src.agent.state import GraphState


def chat(state: GraphState) -> dict[str, list[AIMessage]]:
    """Echo the last human message with no LLM or I/O."""
    last_text = ""
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            content = message.content
            last_text = content if isinstance(content, str) else ""
            break
    return {"messages": [AIMessage(content=last_text)]}
