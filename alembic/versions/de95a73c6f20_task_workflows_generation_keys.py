"""工作流运行索引与内容生成幂等标识。"""
from alembic import op
import sqlalchemy as sa

revision = "de95a73c6f20"
down_revision = "cd84f62b5e19"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("contents", sa.Column("generation_key", sa.String(100), nullable=True))
    op.create_unique_constraint("uq_contents_generation_key", "contents", ["generation_key"])
    op.add_column("contents", sa.Column("generation_metadata", sa.JSON(), nullable=True))
    op.create_table("task_workflows",
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False, unique=True),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_task_workflows_user_id", "task_workflows", ["user_id"])


def downgrade():
    op.drop_table("task_workflows")
    op.drop_column("contents", "generation_metadata")
    op.drop_constraint("uq_contents_generation_key", "contents", type_="unique")
    op.drop_column("contents", "generation_key")
