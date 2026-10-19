"""可维护资源、账号独立的平台规则与最终发布素材说明。"""
from alembic import op
import sqlalchemy as sa

revision = "f74b9c0d123e"
down_revision = "f63a8b9c012d"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("rednote_cases", "prompts", "custom_rules"):
        op.add_column(table, sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("platform_rule_overrides", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("platform_rules", sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True))
    op.create_index("ix_platform_rules_user_id", "platform_rules", ["user_id"])
    op.add_column("publish_records", sa.Column("publication_details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade():
    op.drop_column("publish_records", "publication_details")
    op.drop_index("ix_platform_rules_user_id", table_name="platform_rules")
    op.drop_column("platform_rules", "user_id")
    op.drop_column("users", "platform_rule_overrides")
    for table in ("custom_rules", "prompts", "rednote_cases"):
        op.drop_column(table, "is_deleted")
