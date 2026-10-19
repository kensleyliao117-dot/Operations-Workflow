"""当前用户的分析模型配置，与已保存分析版本解耦。"""
from alembic import op
import sqlalchemy as sa

revision = "f316a42d7b80"
down_revision = "f205c91b8d4a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("user_analysis_models",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "name", name="uq_analysis_models_owner_name"))
    op.create_index("ix_user_analysis_models_user_id", "user_analysis_models", ["user_id"])


def downgrade():
    op.drop_table("user_analysis_models")

