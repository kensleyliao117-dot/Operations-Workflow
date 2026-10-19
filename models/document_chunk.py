"""产品资料片段及其向量；业务库使用 PostgreSQL 的 pgvector 类型。"""
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "embedding_space", "embedding_model", "embedding_dimensions", "chunk_index",
                         name="uq_document_chunks_position"),
        Index("ix_document_chunks_scope", "user_id", "product_id", "embedding_model", "embedding_dimensions"),
        CheckConstraint("embedding_dimensions > 0", name="ck_document_chunks_dimensions"),
        CheckConstraint("chunk_index >= 0 AND start_char >= 0 AND end_char > start_char",
                        name="ck_document_chunks_offsets"),
        CheckConstraint("vector_dims(embedding) = embedding_dimensions",
                        name="ck_document_chunks_vector_dimensions").ddl_if(dialect="postgresql"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    document_id: Mapped[int] = mapped_column(ForeignKey("product_documents.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[str] = mapped_column(String(64))
    chunk_index: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    start_char: Mapped[int]
    end_char: Mapped[int]
    document_hash: Mapped[str] = mapped_column(String(64))
    embedding_model: Mapped[str] = mapped_column(String(100))
    embedding_space: Mapped[str] = mapped_column(String(64), default='legacy', server_default='legacy')
    embedding_dimensions: Mapped[int]
    # 不固定维度，兼容 v3/v4；检索必须同时限定模型及维度。
    # SQLite JSON 仅用于现有离线测试，生产检索只支持 PostgreSQL。
    embedding: Mapped[list[float]] = mapped_column(Vector().with_variant(JSON(), "sqlite"))
    chunk_metadata: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
