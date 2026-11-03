"""Persist cancellation file cleanup without changing existing task history."""
import sqlalchemy as sa
from alembic import op

revision = "cancel_cleanup"
down_revision = "retained_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("cancel_cleanup",
        sa.Column("request_id", sa.String(), sa.ForeignKey("requests.id"), primary_key=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("entries", sa.JSON(), nullable=False),
        sa.Column("file_operation_id", sa.String(), nullable=True),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.Double(), nullable=False),
        sa.Column("updated_at", sa.Double(), nullable=False))
    op.create_index("ix_cancel_cleanup_state", "cancel_cleanup", ["state"])


def downgrade() -> None:
    op.drop_table("cancel_cleanup")
