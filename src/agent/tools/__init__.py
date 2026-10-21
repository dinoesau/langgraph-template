"""Shared loop tools: one module per capability behind one list."""

from agent.tools.booking import book_appointment, get_availability, get_order_status

tools = [get_availability, book_appointment, get_order_status]

tools_by_name = {getattr(item, "name", ""): item for item in tools}

__all__ = ["tools", "tools_by_name"]
