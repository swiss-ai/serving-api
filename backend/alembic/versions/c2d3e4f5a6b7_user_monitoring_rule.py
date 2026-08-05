"""user_monitoring_rule table + apikey.is_admin for per-user trace collection

Admin- or self-created rules that turn on Langfuse tracing for a single
user's requests. Levels: metadata (latency/tokens only) or full (prompts +
completions). Every rule expires — TTLs are fixed presets enforced at the
API layer, so monitoring can never be left on forever. Admins are flagged
directly on their apikey row (is_admin); bootstrap the first admin via SQL:
UPDATE apikey SET is_admin = true WHERE owner_email = '<email>';

Revision ID: c2d3e4f5a6b7
Revises: 6d8e1c6ed7b5
Create Date: 2026-08-05 17:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "6d8e1c6ed7b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "user_monitoring_rule",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_email", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("level", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("note", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_email", "source", name="uq_monitoring_owner_source"),
    )
    op.create_index(
        "ix_user_monitoring_rule_owner_email",
        "user_monitoring_rule",
        ["owner_email"],
    )
    op.add_column(
        "apikey",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("apikey", "is_admin")
    op.drop_index(
        "ix_user_monitoring_rule_owner_email", table_name="user_monitoring_rule"
    )
    op.drop_table("user_monitoring_rule")
