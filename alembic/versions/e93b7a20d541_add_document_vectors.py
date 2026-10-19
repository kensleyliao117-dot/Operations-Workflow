"""产品资料 pgvector 向量存储。"""
from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa

revision = "e93b7a20d541"
down_revision = "d82a6f19c430"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
            RAISE EXCEPTION 'pgvector server extension is not installed. Install pgvector for this PostgreSQL server, then rerun alembic upgrade head.';
        END IF;
    END; $$""")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("product_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_id", sa.String(64), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("document_hash", sa.String(64), nullable=False),
        sa.Column("embedding_model", sa.String(100), nullable=False),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column("chunk_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("document_id", "embedding_model", "embedding_dimensions", "chunk_index",
                            name="uq_document_chunks_position"),
        sa.CheckConstraint("embedding_dimensions > 0", name="ck_document_chunks_dimensions"),
        sa.CheckConstraint("chunk_index >= 0 AND start_char >= 0 AND end_char > start_char", name="ck_document_chunks_offsets"),
        sa.CheckConstraint("vector_dims(embedding) = embedding_dimensions", name="ck_document_chunks_vector_dimensions"),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index("ix_document_chunks_scope", "document_chunks",
                    ["user_id", "product_id", "embedding_model", "embedding_dimensions"])


def downgrade():
    op.drop_table("document_chunks")
    # 扩展可能被其他表使用，不随本业务表删除。
