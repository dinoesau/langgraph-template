"""Booking capability: availability, appointments, and order status.

Deterministic and side-effect free except for the in-memory booking ledger,
so unit tests run with no model calls and no network.
"""

from langchain_core.tools import tool

_OPEN_SLOTS = ("09:00", "10:00", "11:00", "14:00", "15:00")
_ORDER_STATUS = {
    "ord-1": "shipped",
    "ord-2": "in preparation",
    "ord-3": "delivered",
}

_bookings: dict[str, str] = {}


@tool
def get_availability(date: str) -> str:
    """List open booking slots for a date in YYYY-MM-DD format."""
    taken = {key.rsplit(" ", 1)[1] for key in _bookings if key.rsplit(" ", 1)[0] == date}
    free = [slot for slot in _OPEN_SLOTS if slot not in taken]
    if not free:
        return f"No availability on {date}."
    return f"Availability on {date}: {', '.join(free)}."


@tool
def book_appointment(date: str, time: str) -> str:
    """Book one open slot for a date in YYYY-MM-DD format."""
    if time not in _OPEN_SLOTS:
        return f"Slot {time} on {date} is unavailable."
    key = f"{date} {time}"
    if key in _bookings:
        return f"Slot {time} on {date} is already booked."
    confirmation = f"bk-{date}-{time}"
    _bookings[key] = confirmation
    return f"Appointment confirmed for {date} at {time} ({confirmation})."


@tool
def get_order_status(order_id: str) -> str:
    """Report the status of a known order id."""
    status = _ORDER_STATUS.get(order_id)
    if status is None:
        return f"Order {order_id} not found."
    return f"Order {order_id} is {status}."


tools = [get_availability, book_appointment, get_order_status]
