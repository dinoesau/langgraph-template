"""Declarative base shared by every app table."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Parent of app models so Alembic autogenerate sees them all."""
