"""Index sessions by owner for the S4 auth list endpoint.

Revision ID: 0002_session_user_index
Revises: 0001_create_users_sessions
Create Date: 2026-10-16

App schema only. Loop persistence tables (checkpoints, checkpoint_blobs,
checkpoint_writes) and memory vector tables are excluded by include_object
in env.py and must never appear here.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_session_user_index"
down_revision: str | None = "0001_create_users_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_user_id", table_name="sessions")
