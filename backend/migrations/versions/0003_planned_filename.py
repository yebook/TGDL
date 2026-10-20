"""Freeze file naming when presenting a confirmation card."""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media_items", sa.Column("planned_filename", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("media_items", "planned_filename")
