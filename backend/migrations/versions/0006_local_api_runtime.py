"""Persistent managed API transition barrier."""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("local_api_runtime", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("values", sa.JSON(), nullable=False))


def downgrade() -> None:
    op.drop_table("local_api_runtime")
