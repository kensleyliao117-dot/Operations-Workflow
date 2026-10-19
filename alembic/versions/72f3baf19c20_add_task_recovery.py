"""保存任务进度与失败现场。"""
from alembic import op
import sqlalchemy as sa

revision = "72f3baf19c20"
down_revision = "550657d360cf"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("checkpoint", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("failed_from_status", sa.String(100), nullable=True))
    op.add_column("tasks", sa.Column("error_message", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("tasks", "error_message")
    op.drop_column("tasks", "failed_from_status")
    op.drop_column("tasks", "checkpoint")
