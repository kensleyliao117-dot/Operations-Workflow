"""移除 Instagram 专用表，V1.0 仅开发小红书。"""
from alembic import op
import sqlalchemy as sa

revision = "9ad3c87f1b42"
down_revision = "8cb521e67a90"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table("instagram_analysis")
    op.drop_table("instagram_cases")


def downgrade():
    # 仅重建结构，恢复数据需要用户备份。
    op.create_table(
        "instagram_cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("link", sa.Text(), nullable=False, unique=True),
    )
    op.create_table(
        "instagram_analysis",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("cases_id", sa.Integer(), sa.ForeignKey("instagram_cases.id"), nullable=False),
    )
