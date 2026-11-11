"""Retain first-line naming input without rewriting frozen tasks or captions."""
import sqlalchemy as sa
from alembic import op

revision = "caption_first_line"
down_revision = "cancel_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media_items", sa.Column("caption_first_line", sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column("media_items", "caption_first_line")
