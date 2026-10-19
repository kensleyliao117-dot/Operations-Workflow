"""发布版本快照、请求幂等和结果待确认状态。"""
from alembic import op
import sqlalchemy as sa

revision = "f082c51b903e"
down_revision = "ef06b84d7031"
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        sa.Column("publisher_backend", sa.String(30), nullable=False, server_default="manual"),
        sa.Column("idempotency_key", sa.String(100), nullable=True),
        sa.Column("content_version", sa.Integer(), nullable=True),
        sa.Column("review_id", sa.Integer(), nullable=True),
        sa.Column("content_snapshot", sa.JSON(), nullable=True),
        sa.Column("content_fingerprint", sa.String(64), nullable=True),
        sa.Column("delivery_phase", sa.String(30), nullable=False, server_default="prepared"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
    ):
        op.add_column("publish_records", column)
    op.create_foreign_key("fk_publish_review", "publish_records", "reviews", ["review_id"], ["id"])
    op.create_unique_constraint("uq_publish_task_request", "publish_records", ["task_id", "idempotency_key"])
    op.create_index("uq_publish_active_task", "publish_records", ["task_id"], unique=True,
                    postgresql_where=sa.text("publish_status IN ('publishing', 'published', 'unknown')"))


def downgrade():
    op.drop_index("uq_publish_active_task", table_name="publish_records")
    op.drop_constraint("uq_publish_task_request", "publish_records", type_="unique")
    op.drop_constraint("fk_publish_review", "publish_records", type_="foreignkey")
    for name in ("submitted_at", "started_at", "delivery_phase", "content_fingerprint", "content_snapshot",
                 "review_id", "content_version", "idempotency_key", "publisher_backend"):
        op.drop_column("publish_records", name)
