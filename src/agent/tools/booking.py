"""Booking capability: one module behind the shared loop tools list."""

from langchain_core.tools import tool


@tool
def get_availability(date: str) -> str:
    """Return open time slots for one date string like 2026-10-22.

    Args:
        date: Calendar date to check, free text like tomorrow or a date.
    """
    clean = date.strip() or "tomorrow"
    return f"Open slots on {clean}: 09:00, 11:30, 15:00"


@tool
def book_appointment(slot: str) -> str:
    """Book one time slot and return a confirmation id.

    Args:
        slot: Chosen slot text, ideally one value from get_availability.
    """
    clean = slot.strip()
    if not clean:
        return "No slot given, booking not created"
    return f"Booked {clean} with confirmation BK-1234"


@tool
def get_order_status(order_id: str) -> str:
    """Return the shipping status for one order id.

    Args:
        order_id: Order identifier from the user message.
    """
    clean = order_id.strip() or "unknown"
    return f"Order {clean} is in transit, arriving in 2 days"
