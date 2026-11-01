"""Naive timestamp columns must commit on SQLModel >= 0.0.45.

Prod stores these as timestamp without time zone. A plain datetime
annotation is mapped to UTCDateTime, which rejects datetime.now()
inside commit(). NaiveDatetime keeps DateTime(timezone=False).
"""

from datetime import date

from sqlmodel import Session, SQLModel, create_engine

from backend.models.entities import APIKey, UsageDaily


def test_naive_timestamp_rows_commit():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        key = APIKey(key="sk-rc-naive-dt", owner_email="a@b.ch")
        usage = UsageDaily(
            day=date(2026, 9, 24),
            owner_email="a@b.ch",
            model="swiss-ai/apertus-8b",
        )
        session.add(key)
        session.add(usage)
        session.commit()
        session.refresh(key)
        session.refresh(usage)

    assert key.created_at.tzinfo is None
    assert key.updated_at.tzinfo is None
    assert usage.updated_at.tzinfo is None
