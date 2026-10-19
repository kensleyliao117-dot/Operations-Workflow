"""历史分析保留完整的自定义供应商名称。"""
from alembic import op
import sqlalchemy as sa

revision = "f63a8b9c012d"
down_revision = "f52c7d8e901a"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("rednote_analysis", "provider", type_=sa.String(100), existing_type=sa.String(30),
                    existing_nullable=True)


def downgrade():
    # 保留字段容量，避免截断已经保存的供应商名称。
    pass
