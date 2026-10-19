"""发布成功后的经验库同步事件。"""
from alembic import op
import sqlalchemy as sa

revision = "f190b3a2c047"
down_revision = "f082c51b903e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("memory_sync_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("publish_record_id", sa.Integer(), sa.ForeignKey("publish_records.id"), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("synced_at", sa.DateTime()))
    op.create_index("ix_memory_sync_events_user_id", "memory_sync_events", ["user_id"])
    op.execute("INSERT INTO memory_sync_events(user_id, publish_record_id) SELECT t.user_id, p.id "
               "FROM publish_records p JOIN tasks t ON t.id = p.task_id WHERE p.publish_status = 'published'")


def downgrade():
    op.drop_table("memory_sync_events")
