"""案例媒体指标、结构化分析及发布账号归属。"""
from alembic import op
import sqlalchemy as sa

revision = "bc73e51a4d08"
down_revision = "a62d9b4e3107"
branch_labels = None
depends_on = None


def upgrade():
    for name, default in (("image_urls", "'[]'"), ("metrics", "'{}'"), ("metrics_raw", "'{}'")):
        op.add_column("rednote_cases", sa.Column(name, sa.JSON(), nullable=False, server_default=sa.text(default)))
    op.add_column("rednote_cases", sa.Column("author_platform_id", sa.String(100), nullable=True))
    op.add_column("rednote_cases", sa.Column("collected_at", sa.DateTime(), nullable=True))
    op.add_column("rednote_analysis", sa.Column("result", sa.JSON(), nullable=True))
    op.add_column("rednote_analysis", sa.Column("model", sa.String(100), nullable=True))
    op.add_column("rednote_analysis", sa.Column("source_fingerprint", sa.String(64), nullable=True))
    op.add_column("rednote_analysis", sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.create_table("platform_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("platform", sa.String(100), nullable=False),
        sa.Column("platform_user_id", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("user_id", "platform", "platform_user_id", name="uq_platform_account_owner"))
    op.create_index("ix_platform_accounts_user_id", "platform_accounts", ["user_id"])
    op.add_column("publish_records", sa.Column("account_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_publish_records_account", "publish_records", "platform_accounts", ["account_id"], ["id"])
    op.create_index("ix_publish_records_account_id", "publish_records", ["account_id"])
    op.create_unique_constraint("uq_publish_record_account", "publish_records", ["id", "account_id"])
    op.create_table("publish_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("publish_record_id", sa.Integer(), sa.ForeignKey("publish_records.id"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("platform_accounts.id"), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["publish_record_id", "account_id"], ["publish_records.id", "publish_records.account_id"],
                                name="fk_publish_metrics_record_account"))
    for name in ("user_id", "publish_record_id"):
        op.create_index(f"ix_publish_metrics_{name}", "publish_metrics", [name])


def downgrade():
    op.drop_table("publish_metrics")
    op.drop_constraint("uq_publish_record_account", "publish_records", type_="unique")
    op.drop_index("ix_publish_records_account_id", table_name="publish_records")
    op.drop_constraint("fk_publish_records_account", "publish_records", type_="foreignkey")
    op.drop_column("publish_records", "account_id")
    op.drop_table("platform_accounts")
    for name in ("created_at", "source_fingerprint", "model", "result"):
        op.drop_column("rednote_analysis", name)
    for name in ("collected_at", "author_platform_id", "metrics_raw", "metrics", "image_urls"):
        op.drop_column("rednote_cases", name)
