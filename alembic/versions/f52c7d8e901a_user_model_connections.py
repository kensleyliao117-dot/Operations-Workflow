"""用户独立模型地址、加密凭据和默认模型。"""
from alembic import op
import sqlalchemy as sa

revision = "f52c7d8e901a"
down_revision = "f316a42d7b80"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("user_analysis_models", "provider", type_=sa.String(100), existing_type=sa.String(30))
    op.add_column("user_analysis_models", sa.Column("base_url", sa.String(2048), nullable=True))
    op.add_column("user_analysis_models", sa.Column("encrypted_api_key", sa.Text(), nullable=True))
    op.add_column("user_analysis_models", sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("uq_analysis_models_default_owner", "user_analysis_models", ["user_id"],
                    unique=True, postgresql_where=sa.text("is_default"))


def downgrade():
    op.drop_index("uq_analysis_models_default_owner", table_name="user_analysis_models")
    op.drop_column("user_analysis_models", "is_default")
    op.drop_column("user_analysis_models", "encrypted_api_key")
    op.drop_column("user_analysis_models", "base_url")
    # 保留供应商字段容量，避免截断自定义名称。
