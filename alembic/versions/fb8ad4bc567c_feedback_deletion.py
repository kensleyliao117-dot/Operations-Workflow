"""标记已删除反馈，避免同步或采集自动重建。"""
from alembic import op
import sqlalchemy as sa

revision = 'fb8ad4bc567c'
down_revision = 'fa7ec3ab456b'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('publish_records', sa.Column('feedback_deleted', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('publish_records', 'feedback_deleted')
