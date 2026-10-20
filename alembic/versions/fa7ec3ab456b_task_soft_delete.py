"""任务软删除，保留历史内容、审核、发布及检查点。"""
from alembic import op
import sqlalchemy as sa

revision = "fa7ec3ab456b"
down_revision = "f96db2ef345a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade():
    op.drop_column("tasks", "is_deleted")
