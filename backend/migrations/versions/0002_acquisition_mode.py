"""Persist transport ownership so recovery cannot follow a changed global setting."""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("acquisition_mode", sa.String(), nullable=False, server_default="local"))


def downgrade() -> None:
    op.drop_column("jobs", "acquisition_mode")
