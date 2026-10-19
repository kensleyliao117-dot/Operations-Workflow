"""为发布记录增加作品链接，为内容版本增加图片存储引用。"""
from alembic import op
import sqlalchemy as sa

revision = "b6e18a2c904f"
down_revision = "9ad3c87f1b42"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("publish_records", sa.Column("platform_post_url", sa.Text(), nullable=True))
    op.add_column("contents", sa.Column("image_assets", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))


def downgrade():
    op.drop_column("contents", "image_assets")
    op.drop_column("publish_records", "platform_post_url")
