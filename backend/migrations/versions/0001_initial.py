"""Initial durable application schema. First-release baseline."""
import sqlalchemy as sa
from alembic import op

revision = "initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('admins',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=80), nullable=False),
    sa.Column('password_hash', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username')
    )
    op.create_table('bot_identities',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('ingest_updates',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('bot_id', sa.Integer(), nullable=False),
    sa.Column('update_id', sa.Integer(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('processed', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('bot_id', 'update_id')
    )
    op.create_table('poll_cursor',
    sa.Column('bot_id', sa.Integer(), nullable=False),
    sa.Column('next_offset', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('bot_id')
    )
    op.create_table('requests',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('bot_id', sa.Integer(), nullable=False),
    sa.Column('chat_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('group_key', sa.String(), nullable=True),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('name_override', sa.String(length=80), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('created_at', sa.Double(), nullable=False),
    sa.Column('updated_at', sa.Double(), nullable=False),
    sa.Column('collect_until', sa.Double(), nullable=False),
    sa.Column('nonce', sa.String(), nullable=False),
    sa.Column('expires_at', sa.Double(), nullable=True),
    sa.Column('card_message_id', sa.Integer(), nullable=True),
    sa.Column('config_revision', sa.Integer(), nullable=False),
    sa.Column('destination', sa.String(), nullable=False),
    sa.Column('root_id', sa.String(), nullable=False),
    sa.Column('deleted_at', sa.Double(), nullable=True),
    sa.Column('result_revision', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('pending_media_names',
    sa.Column('bot_id', sa.Integer(), nullable=False),
    sa.Column('chat_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('message_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=True),
    sa.Column('expires_at', sa.Double(), nullable=False),
    sa.PrimaryKeyConstraint('bot_id', 'chat_id', 'user_id')
    )
    op.create_table('sessions',
    sa.Column('token_hash', sa.String(), nullable=False),
    sa.Column('csrf_token', sa.String(), nullable=False),
    sa.Column('expires_at', sa.Double(), nullable=False),
    sa.PrimaryKeyConstraint('token_hash')
    )
    op.create_table('settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('schema_version', sa.Integer(), nullable=False),
    sa.Column('values', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('media_items',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('request_id', sa.String(), nullable=False),
    sa.Column('bot_id', sa.Integer(), nullable=False),
    sa.Column('chat_id', sa.Integer(), nullable=False),
    sa.Column('message_id', sa.Integer(), nullable=False),
    sa.Column('file_id', sa.String(), nullable=False),
    sa.Column('file_unique_id', sa.String(), nullable=True),
    sa.Column('media_type', sa.String(), nullable=False),
    sa.Column('planned_filename', sa.String(), nullable=True),
    sa.Column('caption_snippet', sa.String(length=80), nullable=True),
    sa.Column('original_name', sa.String(), nullable=True),
    sa.Column('size_bytes', sa.Integer(), nullable=True),
    sa.Column('duration_seconds', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['request_id'], ['requests.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('bot_id', 'chat_id', 'message_id')
    )
    op.create_table('notification_outbox',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('event_key', sa.String(), nullable=False),
    sa.Column('request_id', sa.String(), nullable=True),
    sa.Column('chat_id', sa.Integer(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('state', sa.String(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('next_run_at', sa.Double(), nullable=False),
    sa.Column('message_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['request_id'], ['requests.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_key')
    )
    op.create_table('jobs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('item_id', sa.String(), nullable=False),
    sa.Column('request_id', sa.String(), nullable=False),
    sa.Column('state', sa.String(), nullable=False),
    sa.Column('acquisition_mode', sa.String(), nullable=False, server_default='cloud'),
    sa.Column('phase', sa.String(), nullable=False),
    sa.Column('root_id', sa.String(), nullable=False),
    sa.Column('relative_path', sa.String(), nullable=False),
    sa.Column('attempt_sequence', sa.Integer(), nullable=False),
    sa.Column('auto_retries', sa.Integer(), nullable=False),
    sa.Column('max_auto_retries', sa.Integer(), nullable=False),
    sa.Column('next_run_at', sa.Double(), nullable=False),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.Column('boot_id', sa.String(), nullable=True),
    sa.Column('completed_bytes', sa.Integer(), nullable=False),
    sa.Column('speed_bps', sa.Double(), nullable=True),
    sa.Column('started_at', sa.Double(), nullable=True),
    sa.Column('finished_at', sa.Double(), nullable=True),
    sa.Column('error_code', sa.String(), nullable=True),
    sa.Column('error_message', sa.String(), nullable=True),
    sa.Column('linked_job_id', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['item_id'], ['media_items.id'], ),
    sa.ForeignKeyConstraint(['request_id'], ['requests.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('item_id')
    )
    op.create_index(op.f('ix_jobs_request_id'), 'jobs', ['request_id'], unique=False)
    op.create_index(op.f('ix_jobs_state'), 'jobs', ['state'], unique=False)
    op.create_table('attempts',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('job_id', sa.String(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.Double(), nullable=False),
    sa.Column('finished_at', sa.Double(), nullable=True),
    sa.Column('error_code', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_id', 'sequence')
    )
    op.create_table('file_reservations',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('bot_id', sa.Integer(), nullable=False),
    sa.Column('file_unique_id', sa.String(), nullable=False),
    sa.Column('job_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('bot_id', 'file_unique_id'),
    sa.UniqueConstraint('job_id')
    )
    op.create_table('publish_journal',
    sa.Column('job_id', sa.String(), nullable=False),
    sa.Column('temporary_path', sa.String(), nullable=False),
    sa.Column('final_path', sa.String(), nullable=False),
    sa.Column('stage', sa.String(), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=True),
    sa.Column('sha256', sa.String(), nullable=True),
    sa.Column('api_source_path_encrypted', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.PrimaryKeyConstraint('job_id')
    )
    op.create_table('stored_files',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('bot_id', sa.Integer(), nullable=False),
    sa.Column('file_unique_id', sa.String(), nullable=True),
    sa.Column('job_id', sa.String(), nullable=False),
    sa.Column('root_id', sa.String(), nullable=False),
    sa.Column('relative_path', sa.String(), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(), nullable=False),
    sa.Column('saved_at', sa.Double(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('bot_id', 'file_unique_id')
    )
    op.create_table('local_api_runtime', sa.Column('id', sa.Integer(), primary_key=True),
                    sa.Column('values', sa.JSON(), nullable=False))
    op.create_table('request_safety', sa.Column('credential', sa.String(64), primary_key=True),
                    sa.Column('values', sa.JSON(), nullable=False))
    op.create_table('cache_cleanup',
                    sa.Column('job_id', sa.String(), sa.ForeignKey('jobs.id'), primary_key=True),
                    sa.Column('bot_id', sa.Integer(), nullable=False),
                    sa.Column('file_id', sa.String(), nullable=False),
                    sa.Column('source_path_encrypted', sa.String(), nullable=False),
                    sa.Column('root_id', sa.String(), nullable=False),
                    sa.Column('relative_path', sa.String(), nullable=False),
                    sa.Column('size_bytes', sa.Integer(), nullable=False),
                    sa.Column('sha256', sa.String(), nullable=False),
                    sa.Column('attempts', sa.Integer(), nullable=False),
                    sa.Column('next_run_at', sa.Double(), nullable=False),
                    sa.Column('error_code', sa.String(), nullable=True))
    op.create_index('ix_cache_cleanup_next_run_at', 'cache_cleanup', ['next_run_at'])


def downgrade() -> None:
    op.drop_index('ix_cache_cleanup_next_run_at', table_name='cache_cleanup')
    op.drop_table('cache_cleanup')
    op.drop_table('request_safety')
    op.drop_table('local_api_runtime')
    op.drop_table('stored_files')
    op.drop_table('publish_journal')
    op.drop_table('file_reservations')
    op.drop_table('attempts')
    op.drop_index(op.f('ix_jobs_state'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_request_id'), table_name='jobs')
    op.drop_table('jobs')
    op.drop_table('notification_outbox')
    op.drop_table('media_items')
    op.drop_table('settings')
    op.drop_table('sessions')
    op.drop_table('pending_media_names')
    op.drop_table('requests')
    op.drop_table('poll_cursor')
    op.drop_table('ingest_updates')
    op.drop_table('bot_identities')
    op.drop_table('admins')
