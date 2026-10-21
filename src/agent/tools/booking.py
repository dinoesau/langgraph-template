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
