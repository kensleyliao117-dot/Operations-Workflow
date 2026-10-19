"""为自定义规则增加所属用户。"""
from alembic import op
import sqlalchemy as sa

revision = "8cb521e67a90"
down_revision = "72f3baf19c20"
branch_labels = None
depends_on = None


def upgrade():
    # 不能为已有规则猜测归属用户；当前项目该表为空。
    if op.get_bind().execute(sa.text("SELECT count(*) FROM custom_rules")).scalar():
        raise RuntimeError("custom_rules 已有数据，请先制定用户归属回填方案再执行此迁移")
    op.add_column("custom_rules", sa.Column("user_id", sa.Integer(), nullable=False))
    op.create_foreign_key("fk_custom_rules_user_id_users", "custom_rules", "users", ["user_id"], ["id"])


def downgrade():
    op.drop_constraint("fk_custom_rules_user_id_users", "custom_rules", type_="foreignkey")
    op.drop_column("custom_rules", "user_id")
