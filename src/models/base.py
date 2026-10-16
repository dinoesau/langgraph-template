from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for app tables only.

    Loop persistence tables (checkpoints, checkpoint_blobs, checkpoint_writes)
    belong to LangGraph and memory vector tables belong to the memory layer,
    so no model here may map them. Alembic excludes them too.
    """


def created_at_column() -> Mapped[datetime]:
    """Timezone-aware creation timestamp shared by app tables."""
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
