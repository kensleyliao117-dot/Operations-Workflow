"""按用户保存系统模型移除设置，保留共享配置和历史数据。"""
from alembic import op
import sqlalchemy as sa

revision = "f85ca1de234f"
down_revision = "f74b9c0d123e"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("hidden_system_models", sa.JSON(), nullable=False,
                                    server_default=sa.text("'[]'")))


def downgrade():
    op.drop_column("users", "hidden_system_models")
