"""发布可见范围持久化并进入发布快照。"""
from alembic import op
import sqlalchemy as sa

revision = "f205c91b8d4a"
down_revision = "f190b3a2c047"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("publish_records", sa.Column("visibility", sa.String(20), nullable=False, server_default="public"))


def downgrade():
    op.drop_column("publish_records", "visibility")
