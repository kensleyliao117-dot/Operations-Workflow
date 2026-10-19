"""按内容版本保存话题标签，历史内容默认为空数组。"""
from alembic import op
import sqlalchemy as sa

revision = "f41a93c8b602"
down_revision = "e93b7a20d541"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("contents", sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))


def downgrade():
    op.drop_column("contents", "tags")
