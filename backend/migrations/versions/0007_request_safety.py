"""Durable token-scoped request pacing and circuit breakers."""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("request_safety", sa.Column("credential", sa.String(64), primary_key=True),
                    sa.Column("values", sa.JSON(), nullable=False))


def downgrade() -> None:
    op.drop_table("request_safety")
