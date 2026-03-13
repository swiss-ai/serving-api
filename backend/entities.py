import os
import secrets
from datetime import datetime
from sqlmodel import SQLModel, Field, Session, create_engine, select


class APIKey(SQLModel, table=True):
    key: str = Field(primary_key=True)
    budget: int = Field(default=1000)
    created_at: datetime = Field(default=datetime.now())
    updated_at: datetime = Field(default=datetime.now())
    owner_email: str = Field(default="")


def get_or_create_apikey(engine, owner_email: str) -> APIKey:
    with Session(engine) as session:
        api_key = session.exec(
            select(APIKey).where(APIKey.owner_email == owner_email)
        ).first()
        if api_key is None:
            key = f"sk-rc-{secrets.token_urlsafe(16)}"
            api_key = APIKey(key=key, owner_email=owner_email)
            session.add(api_key)
            session.commit()
            session.refresh(api_key)
        return api_key


def rotate_key(engine, key: str) -> APIKey:
    with Session(engine) as session:
        api_key = session.exec(select(APIKey).where(APIKey.key == key)).first()
        if api_key is None:
            raise ValueError("Invalid key")
        api_key.key = f"sk-rc-{secrets.token_urlsafe(16)}"
        session.add(api_key)
        session.commit()
        session.refresh(api_key)
        return api_key


if __name__ == "__main__":
    pg_host = os.environ.get("PG_HOST", "localhost")
    engine = create_engine(pg_host, echo=True)
    SQLModel.metadata.create_all(engine)
