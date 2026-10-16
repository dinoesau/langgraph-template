"""Slice 1 seam: booking tools behind one shared list."""

from src.agent.tools import tools
from src.agent.tools.booking import book_appointment, get_availability, get_order_status


def test_shared_tools_list_exposes_three_capabilities() -> None:
    assert [tool.name for tool in tools] == [
        "get_availability",
        "book_appointment",
        "get_order_status",
    ]


def test_get_availability_lists_open_slots() -> None:
    out = get_availability.invoke({"date": "2026-10-20"})
    assert "2026-10-20" in out
    assert "10:00" in out


def test_book_appointment_confirms_then_blocks_slot() -> None:
    first = book_appointment.invoke({"date": "2026-10-21", "time": "10:00"})
    assert "confirmed" in first.lower()
    assert "2026-10-21" in first
    second = book_appointment.invoke({"date": "2026-10-21", "time": "10:00"})
    assert "already booked" in second.lower()
    avail = get_availability.invoke({"date": "2026-10-21"})
    assert "10:00" not in avail


def test_book_appointment_rejects_unknown_slot() -> None:
    out = book_appointment.invoke({"date": "2026-10-22", "time": "25:00"})
    assert "unavailable" in out.lower()


def test_get_order_status_known_and_unknown() -> None:
    assert "shipped" in get_order_status.invoke({"order_id": "ord-1"}).lower()
    assert "not found" in get_order_status.invoke({"order_id": "ord-nope"}).lower()
