"""The models and the alembic migrations must describe the same schema.

Prod's schema is built only by migrations. If a model changes (a column,
a type such as datetime -> tz-aware) without a matching migration, the app
and the database disagree at runtime. This compares the migrated test
database against SQLModel.metadata, like `alembic revision --autogenerate`.
"""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlmodel import SQLModel, create_engine

import backend.models.entities  # noqa: F401  (registers the tables)


def test_models_match_migrations(database_url):
    engine = create_engine(database_url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        diff = compare_metadata(ctx, SQLModel.metadata)
    engine.dispose()
    assert diff == [], (
        "Models and migrations disagree; add a migration with "
        f"`alembic revision --autogenerate`:\n{diff}"
    )
