"""Add stable saved-media deletion intent without changing existing media/history."""
import sqlalchemy as sa
from alembic import op

revision = "saved_media_deletion"
down_revision = "initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('file_deletion_operations',
                    sa.Column('id', sa.String(32), primary_key=True),
                    sa.Column('state', sa.String(), nullable=False),
                    sa.Column('request_ids', sa.JSON(), nullable=False),
                    sa.Column('delete_files', sa.Boolean(), nullable=False),
                    sa.Column('entries', sa.JSON(), nullable=False))
    op.create_index('ix_file_deletion_operations_state', 'file_deletion_operations', ['state'])


def downgrade() -> None:
    op.drop_index('ix_file_deletion_operations_state', table_name='file_deletion_operations')
    op.drop_table('file_deletion_operations')
