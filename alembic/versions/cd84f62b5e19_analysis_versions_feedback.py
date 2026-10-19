"""案例分析版本、用户反馈与模型来源。"""
from alembic import op
import sqlalchemy as sa

revision = "cd84f62b5e19"
down_revision = "bc73e51a4d08"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("rednote_analysis", sa.Column("version", sa.Integer(), nullable=True))
    op.execute("""WITH numbered AS (
        SELECT id, row_number() OVER (PARTITION BY cases_id ORDER BY id) AS version
        FROM rednote_analysis
    ) UPDATE rednote_analysis AS a SET version = numbered.version FROM numbered WHERE a.id = numbered.id""")
    op.alter_column("rednote_analysis", "version", nullable=False, server_default=sa.text("1"))
    op.create_unique_constraint("uq_rednote_analysis_case_version", "rednote_analysis", ["cases_id", "version"])
    op.create_check_constraint("ck_rednote_analysis_version_positive", "rednote_analysis", "version > 0")
    op.add_column("rednote_analysis", sa.Column("parent_analysis_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_rednote_analysis_parent", "rednote_analysis", "rednote_analysis", ["parent_analysis_id"], ["id"])
    op.add_column("rednote_analysis", sa.Column("revision_type", sa.String(30), nullable=False, server_default="manual"))
    op.add_column("rednote_analysis", sa.Column("provider", sa.String(30), nullable=True))
    op.add_column("rednote_analysis", sa.Column("model_profile", sa.String(100), nullable=True))
    for name in ("feedback_ids", "new_insights"):
        op.add_column("rednote_analysis", sa.Column(name, sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.execute("UPDATE rednote_analysis SET revision_type = 'initial' WHERE model IS NOT NULL")
    op.create_table("rednote_analysis_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_id", sa.Integer(), sa.ForeignKey("rednote_analysis.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    for name in ("analysis_id", "user_id"):
        op.create_index(f"ix_rednote_analysis_feedback_{name}", "rednote_analysis_feedback", [name])


def downgrade():
    op.drop_table("rednote_analysis_feedback")
    op.drop_constraint("fk_rednote_analysis_parent", "rednote_analysis", type_="foreignkey")
    op.drop_constraint("ck_rednote_analysis_version_positive", "rednote_analysis", type_="check")
    op.drop_constraint("uq_rednote_analysis_case_version", "rednote_analysis", type_="unique")
    for name in ("new_insights", "feedback_ids", "model_profile", "provider", "revision_type", "parent_analysis_id", "version"):
        op.drop_column("rednote_analysis", name)
