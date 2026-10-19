"""独立作品、指标、分析和经验版本表。"""
from alembic import op
import sqlalchemy as sa

revision = "m001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("published_posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        *[sa.Column(name, sa.Integer(), nullable=False) for name in
          ("user_id", "product_id", "task_id", "content_id", "content_version", "publish_record_id", "account_id")],
        sa.Column("platform", sa.String(100), nullable=False),
        sa.Column("platform_user_id", sa.String(100), nullable=False),
        sa.Column("platform_post_id", sa.String(100)), sa.Column("platform_post_url", sa.Text()),
        sa.Column("published_at", sa.DateTime()), sa.Column("content_snapshot", sa.JSON(), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "publish_record_id", name="uq_memory_publication"))
    op.create_table("post_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("published_posts.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False), sa.Column("idempotency_key", sa.String(100), nullable=False),
        *[sa.Column(name, sa.JSON(), nullable=False) for name in ("metrics", "metrics_raw", "precision")],
        sa.Column("source", sa.String(30), nullable=False), sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_memory_metrics_request"))
    op.create_table("feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("published_posts.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False), sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("user_note", sa.Text()), sa.Column("evidence_snapshot", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False), sa.Column("model", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("post_id", "source_fingerprint", name="uq_memory_feedback_source"))
    op.create_table("optimization_memories",
        sa.Column("id", sa.Integer(), primary_key=True),
        *[sa.Column(name, sa.Integer(), nullable=False) for name in ("user_id", "product_id", "account_id", "version")],
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("published_posts.id"), nullable=False),
        sa.Column("feedback_id", sa.Integer(), sa.ForeignKey("feedback.id"), nullable=False),
        sa.Column("platform", sa.String(100), nullable=False), sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("post_id", "version", name="uq_memory_post_version"),
        sa.UniqueConstraint("feedback_id", name="uq_memory_feedback"))
    for table, columns in {
        "published_posts": ("user_id", "product_id", "publish_record_id", "account_id"),
        "post_metrics": ("post_id", "user_id"), "feedback": ("post_id", "user_id"),
        "optimization_memories": ("user_id", "product_id", "account_id", "post_id"),
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    for table in ("optimization_memories", "feedback", "post_metrics", "published_posts"):
        op.drop_table(table)
