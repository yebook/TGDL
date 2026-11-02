"""Retain optional attachment MIME hints without rewriting historical media."""
import sqlalchemy as sa
from alembic import op

revision = "file_types"
down_revision = "saved_media_deletion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media_items", sa.Column("mime_type", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("media_items", "mime_type")
