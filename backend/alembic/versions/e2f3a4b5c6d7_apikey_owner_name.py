"""apikey.owner_name — display name for the public model catalogue

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-09-07

/v1/models* is readable anonymously, and SML now stamps launcher and
allowlist EMAILS on every peer. The catalogue therefore credits people by
display name instead, resolved server-side; this column is where a real
name lands (from the IdP `name` claim, on profile load) so it does not have
to be guessed from the address' local part.

Backfills to "" — existing rows pick up their name the next time their
owner loads the profile page, and read as a derived name until then.
"""

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "apikey",
        sa.Column(
            "owner_name",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("apikey", "owner_name")
