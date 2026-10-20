"""Keep bounded media captions for deterministic task and group naming."""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media_items", sa.Column("caption_snippet", sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column("media_items", "caption_snippet")
