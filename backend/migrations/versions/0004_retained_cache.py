"""Retain exact cache identities and explicit operations without changing old media."""
import sqlalchemy as sa
from alembic import op

revision = "retained_cache"
down_revision = "file_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("execution_mode", sa.String(), server_default="download", nullable=False))
    op.add_column("jobs", sa.Column("cache_id", sa.String(), nullable=True))
    op.add_column("jobs", sa.Column("storage_failure", sa.JSON(), nullable=True))
    op.create_table("retained_caches",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("bot_id", sa.Integer(), nullable=False), sa.Column("file_id", sa.String(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False), sa.Column("generation", sa.String(), nullable=False),
        sa.Column("acquisition_id", sa.String(), nullable=True), sa.Column("credential", sa.String(), nullable=False),
        sa.Column("path_encrypted", sa.String(), nullable=True), sa.Column("path_digest", sa.String(), nullable=True),
        sa.Column("completeness", sa.String(), nullable=False), sa.Column("state", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True), sa.Column("allocated_bytes", sa.Integer(), nullable=True),
        sa.Column("fingerprint", sa.JSON(), nullable=True), sa.Column("native_identity", sa.String(), nullable=True),
        sa.Column("resumable", sa.Boolean(), nullable=False), sa.Column("resume_reason", sa.String(), nullable=True),
        sa.Column("updated_at", sa.Double(), nullable=False))
    op.create_index("ix_retained_caches_job_id", "retained_caches", ["job_id"])
    op.create_index("ix_retained_caches_path_digest", "retained_caches", ["path_digest"])
    op.create_table("cache_actions", sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("cache_id", sa.String(), sa.ForeignKey("retained_caches.id"), nullable=True),
        sa.Column("kind", sa.String(), nullable=False), sa.Column("state", sa.String(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False), sa.Column("generation", sa.String(), nullable=False),
        sa.Column("credential", sa.String(), nullable=False), sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("message", sa.String(), nullable=True), sa.Column("created_at", sa.Double(), nullable=False))
    op.create_index("ix_cache_actions_job_id", "cache_actions", ["job_id"])
    op.create_index("ix_cache_actions_state", "cache_actions", ["state"])


def downgrade() -> None:
    op.drop_table("cache_actions")
    op.drop_table("retained_caches")
    op.drop_column("jobs", "storage_failure")
    op.drop_column("jobs", "cache_id")
    op.drop_column("jobs", "execution_mode")
