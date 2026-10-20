"""Hide removed task records while preserving download and deduplication history."""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("requests", sa.Column("deleted_at", sa.Double(), nullable=True))


def downgrade() -> None:
    op.drop_column("requests", "deleted_at")
