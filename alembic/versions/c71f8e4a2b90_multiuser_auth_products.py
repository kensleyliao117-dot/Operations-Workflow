"""多用户认证、可撤销会话、私有产品资料与案例归属。"""
from alembic import op
import sqlalchemy as sa

revision = "c71f8e4a2b90"
down_revision = "b6e18a2c904f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("username", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("password_hash", sa.String(500), nullable=True))
    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_unique_constraint("uq_users_username", "users", ["username"])
    op.create_table("login_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_login_sessions_user_id", "login_sessions", ["user_id"])
    op.create_table("products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_products_user_id", "products", ["user_id"])
    op.create_table("product_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False, unique=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_product_documents_product_id", "product_documents", ["product_id"])
    op.add_column("tasks", sa.Column("product_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_tasks_product_id", "tasks", "products", ["product_id"], ["id"])
    op.create_index("ix_tasks_product_id", "tasks", ["product_id"])
    op.add_column("rednote_cases", sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_rednote_cases_user_id", "rednote_cases", "users", ["user_id"], ["id"])
    op.create_index("ix_rednote_cases_user_id", "rednote_cases", ["user_id"])
    # 旧案例不猜测归属。新用户看不到 owner 为 NULL 的历史记录。
    constraints = sa.inspect(op.get_bind()).get_unique_constraints("rednote_cases")
    for constraint in constraints:
        if constraint["column_names"] == ["link"]:
            op.drop_constraint(constraint["name"], "rednote_cases", type_="unique")
    op.create_unique_constraint("uq_rednote_cases_owner_link", "rednote_cases", ["user_id", "link"])


def downgrade():
    # 用户间可能已存在相同链接，不能静默删除案例来恢复全局唯一约束。
    duplicate = op.get_bind().execute(sa.text(
        "SELECT link FROM rednote_cases GROUP BY link HAVING count(*) > 1 LIMIT 1")).first()
    if duplicate:
        raise RuntimeError("存在跨用户重复案例链接，请先处理后再回退迁移")
    op.drop_constraint("uq_rednote_cases_owner_link", "rednote_cases", type_="unique")
    op.create_unique_constraint("rednote_cases_link_key", "rednote_cases", ["link"])
    op.drop_index("ix_rednote_cases_user_id", table_name="rednote_cases")
    op.drop_constraint("fk_rednote_cases_user_id", "rednote_cases", type_="foreignkey")
    op.drop_column("rednote_cases", "user_id")
    op.drop_index("ix_tasks_product_id", table_name="tasks")
    op.drop_constraint("fk_tasks_product_id", "tasks", type_="foreignkey")
    op.drop_column("tasks", "product_id")
    op.drop_table("product_documents")
    op.drop_table("products")
    op.drop_table("login_sessions")
    op.drop_constraint("uq_users_username", "users", type_="unique")
    for name in ("is_admin", "is_active", "password_hash", "username"):
        op.drop_column("users", name)
