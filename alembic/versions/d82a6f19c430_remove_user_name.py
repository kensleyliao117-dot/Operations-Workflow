"""移除不再使用的用户 name 字段。"""
from alembic import op
import sqlalchemy as sa

revision = "d82a6f19c430"
down_revision = "c71f8e4a2b90"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("users", "name")


def downgrade():
    # 旧 name 数据不可恢复；回退时以 username 或历史用户 ID 填充。
    op.add_column("users", sa.Column("name", sa.String(100), nullable=True))
    op.execute(sa.text("UPDATE users SET name = COALESCE(username, 'user-' || CAST(id AS TEXT))"))
    op.alter_column("users", "name", existing_type=sa.String(100), nullable=False)
