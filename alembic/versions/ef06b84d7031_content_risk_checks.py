"""规则配置与版本化风险检查记录。"""
from alembic import op
import sqlalchemy as sa

revision = "ef06b84d7031"
down_revision = "de95a73c6f20"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("platform_rules", "custom_rules"):
        for column in (
            sa.Column("rule_type", sa.String(20), nullable=False, server_default="ai"),
            sa.Column("keywords", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("max_length", sa.Integer(), nullable=True),
            sa.Column("targets", sa.JSON(), nullable=False,
                      server_default=sa.text("'[\"title\",\"body\",\"tags\",\"image_prompt\",\"video_script\"]'")),
            sa.Column("severity", sa.String(20), nullable=False, server_default="medium"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("source_url", sa.String(2048), nullable=True),
        ):
            op.add_column(table, column)
    op.create_table("content_rule_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("content_id", sa.Integer(), sa.ForeignKey("contents.id"), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("platform_name", sa.String(100), nullable=False),
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        sa.Column("rules_fingerprint", sa.String(64), nullable=False),
        sa.Column("rules_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("risks", sa.JSON(), nullable=False),
        sa.Column("coverage_warnings", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    for name in ("user_id", "task_id", "content_id"):
        op.create_index(f"ix_content_rule_checks_{name}", "content_rule_checks", [name])


def downgrade():
    op.drop_table("content_rule_checks")
    for table in ("platform_rules", "custom_rules"):
        for name in ("source_url", "version", "is_active", "severity", "targets", "max_length", "keywords", "rule_type"):
            op.drop_column(table, name)
