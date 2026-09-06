"""model_access_override table for post-launch access changes

OpenTela labels are immutable for the life of a job, so `--authorization`
was a decision that could not be revisited without relaunching. A row here
replaces the peer's `authorization` label as the enforced policy; deleting
it ("Reset" in the UI) returns authority to the label, which is never
rewritten.

Keyed by the launch's UUID label rather than by model name, so an override
can never outlive its job and re-attach to a different launch that later
takes the same name.

Revision ID: d1e2f3a4b5c6
Revises: b7c8d9e0f1a2
Create Date: 2026-09-05 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "model_access_override",
        sa.Column("launch_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("model_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("owner_email", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("policy", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("launch_id"),
    )
    op.create_index(
        "ix_model_access_override_model_id",
        "model_access_override",
        ["model_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_model_access_override_model_id", table_name="model_access_override"
    )
    op.drop_table("model_access_override")
