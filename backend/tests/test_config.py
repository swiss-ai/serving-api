import pytest
from sqlmodel import create_engine

from backend.config import Settings


@pytest.mark.parametrize(
    "url, expected",
    [
        ("postgresql://u:p@db/x", "postgresql+psycopg2://u:p@db/x"),
        ("postgresql+psycopg2://u:p@db/x", "postgresql+psycopg2://u:p@db/x"),
        ("", ""),
    ],
)
def test_database_url_names_the_driver(url, expected):
    assert Settings(database_url=url).database_url == expected


def test_plain_postgres_url_builds_an_engine_with_the_installed_driver():
    """On SQLAlchemy 2.1 a bare postgresql:// URL loads psycopg, which isn't
    installed, so the app died at startup."""
    url = Settings(database_url="postgresql://u:p@db/x").database_url
    assert create_engine(url).dialect.driver == "psycopg2"
