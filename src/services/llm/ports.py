"""LLM ports: interfaces the loop depends on, owned by the service side."""

from typing import Protocol

from langchain_core.messages import BaseMessage
from pydantic import BaseModel


class LlmPort(Protocol):
    """One call entry for the loop; implementations handle retries plus fallback."""

    async def acall(
        self,
        messages: list[BaseMessage],
        tools: list[object] | None = None,
        response_format: type[BaseModel] | None = None,
    ) -> object: ...

    def call(
        self,
        messages: list[BaseMessage],
        tools: list[object] | None = None,
        response_format: type[BaseModel] | None = None,
    ) -> object: ...
