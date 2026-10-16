from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from src.models import Base, Session, User


@pytest.fixture()
def orm_session() -> Iterator[OrmSession]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def test_user_and_session_roundtrip(orm_session: OrmSession) -> None:
    user = User(email="ada@example.com", username="ada", hashed_password="hash-1")
    orm_session.add(user)
    orm_session.flush()
    chat = Session(user_id=user.id, name="first chat", username=user.username)
    orm_session.add(chat)
    orm_session.commit()

    loaded = orm_session.scalar(select(Session).where(Session.id == chat.id))
    assert loaded is not None
    assert loaded.username == "ada"
    assert loaded.user.email == "ada@example.com"
    assert loaded.created_at is not None
    assert user.created_at is not None


def test_email_unique(orm_session: OrmSession) -> None:
    orm_session.add(User(email="dup@example.com", hashed_password="hash-1"))
    orm_session.commit()
    orm_session.add(User(email="dup@example.com", hashed_password="hash-2"))
    with pytest.raises(IntegrityError):
        orm_session.commit()


def test_app_schema_excludes_loop_tables() -> None:
    tables = set(Base.metadata.tables)
    assert {"users", "sessions"} <= tables
    assert tables.isdisjoint(
        {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "memory_embeddings"}
    )
