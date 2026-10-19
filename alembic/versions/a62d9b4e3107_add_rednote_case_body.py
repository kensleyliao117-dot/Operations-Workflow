"""增加小红书案例正文列，保留旧案例的标题和链接。"""
from alembic import op
import sqlalchemy as sa

revision = "a62d9b4e3107"
down_revision = "f41a93c8b602"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("rednote_cases", sa.Column("body", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("rednote_cases", "body")
