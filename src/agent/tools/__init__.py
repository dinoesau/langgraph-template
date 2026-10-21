"""Shared loop tools: one module per capability behind one list."""

from agent.tools.booking import book_appointment, get_availability

tools = [get_availability, book_appointment]

__all__ = ["tools"]
