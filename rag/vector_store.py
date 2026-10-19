"""保存和检索产品资料向量，从认证 Session 获取当前用户身份。

写操作使用请求专属 Session，统一提交或回滚。
采用 pgvector 余弦检索，按用户、产品、模型和维度隔离资料。
"""
from dataclasses import dataclass
import math
import re
from typing import Sequence

from pgvector import Vector
from sqlalchemy import case, delete, select
from sqlalchemy.orm import Session

import config
from models.document_chunk import DocumentChunk
from models.product import Product, ProductDocument
from services.common import current_user_id, get_record, write_transaction
from services.exceptions import InvalidInputError, NotFoundError, ServiceUnavailableError
from services.product import get_product
from .embeddings import EmbeddedChunk
from .text_splitter import MAX_CHUNKS


@dataclass(frozen=True, slots=True)
class SearchResult:
    chunk_id: str
    text: str
    user_id: int
    product_id: int
    document_id: int
    filename: str
    chunk_index: int
    similarity: float
    metadata: dict


def _positive_id(value):
    if type(value) is not int or value <= 0:
        raise InvalidInputError("ID 必须是大于 0 的整数")


def _model_options(model, dimensions, embedding_space='legacy'):
    if embedding_space != 'legacy':
        if (not isinstance(embedding_space, str) or not re.fullmatch(r'[0-9a-f]{64}', embedding_space)
                or not isinstance(model, str) or not 1 <= len(model) <= 100
                or type(dimensions) is not int or not 1 <= dimensions <= 4096):
            raise InvalidInputError('自定义向量模型、维度或空间标识无效')
        return model, dimensions
    try:
        return config.embedding_model_options(model, dimensions)
    except config.ConfigurationError:
        raise InvalidInputError("向量模型或维度不受支持，请与 Embedding 配置保持一致") from None


def _vector(values, dimensions):
    if not isinstance(values, (list, tuple)) or len(values) != dimensions:
        raise InvalidInputError("向量长度与声明维度不一致")
    try:
        if any(type(v) not in {int, float} or not math.isfinite(v) or abs(v) > 3.4028234663852886e38
               for v in values):
            raise ValueError
        # pgvector 使用 float32；量化后再次检查，排除下溢成零的向量。
        result = Vector(values).to_list()
    except (ValueError, TypeError, OverflowError):
        raise InvalidInputError("向量必须由有效的有限 float32 数值组成") from None
    if not any(result):
        raise InvalidInputError("零向量无法用于余弦检索")
    return result


def _document(db, product_id, document_id, *, lock=False):
    _positive_id(product_id)
    _positive_id(document_id)
    # 与上传、删除资料共用产品行锁，避免校验后资料在另一请求中被删除。
    get_record(db, Product, product_id, lock=lock)
    document = get_record(db, ProductDocument, document_id, lock=lock)
    if document.product_id != product_id or document.status == "deleting":
        raise NotFoundError("资料不存在")
    return document


def _scope(user_id, product_id, document_id, model, dimensions, embedding_space='legacy'):
    return (
        DocumentChunk.user_id == user_id,
        DocumentChunk.product_id == product_id,
        DocumentChunk.document_id == document_id,
        DocumentChunk.embedding_model == model,
        DocumentChunk.embedding_dimensions == dimensions,
        DocumentChunk.embedding_space == embedding_space,
    )


def save_document_vectors(
    db: Session, product_id: int, document_id: int, chunks: Sequence[EmbeddedChunk],
) -> int:
    """原子替换该文档、该模型及维度的完整片段快照，返回保存数量。

    传入 embed_document 的完整结果，不能只传一部分；不同模型的向量独立保留。
    重试不追加重复数据，重新切分后不残留旧片段。清空请用 delete_document_vectors。
    """
    with write_transaction(db):
        user_id = current_user_id(db)
        document = _document(db, product_id, document_id, lock=True)
        chunks = list(chunks)
        if not 1 <= len(chunks) <= MAX_CHUNKS or any(not isinstance(c, EmbeddedChunk) for c in chunks):
            raise InvalidInputError("请传入非空的完整 EmbeddedChunk 列表（最多 10000 段）")
        space = chunks[0].embedding_space
        model, dimensions = _model_options(chunks[0].model, chunks[0].dimensions, space)
        document_hash = chunks[0].chunk.document_hash
        rows, ids = [], set()
        for index, item in enumerate(chunks):
            chunk = item.chunk
            if (chunk.user_id != user_id or chunk.product_id != product_id
                    or chunk.document_id != document_id):
                raise InvalidInputError("片段用户、产品或文档与当前操作不一致")
            if item.model != model or item.dimensions != dimensions or item.embedding_space != space or chunk.document_hash != document_hash:
                raise InvalidInputError("一次入库必须来自同一份文档快照、模型和维度")
            if (type(chunk.chunk_index) is not int or chunk.chunk_index != index
                    or not isinstance(chunk.text, str) or not chunk.text.strip()
                    or type(chunk.start_char) is not int or chunk.start_char < 0
                    or type(chunk.end_char) is not int or chunk.end_char - chunk.start_char != len(chunk.text)
                    or not isinstance(chunk.chunk_id, str) or not re.fullmatch(r"[0-9a-f]{64}", chunk.chunk_id)
                    or not isinstance(document_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", document_hash)
                    or chunk.chunk_id in ids):
                raise InvalidInputError("片段序号、文本、偏移或来源标识无效")
            ids.add(chunk.chunk_id)
            rows.append(DocumentChunk(
                user_id=user_id, product_id=product_id, document_id=document_id,
                chunk_id=chunk.chunk_id, chunk_index=index, text=chunk.text,
                start_char=chunk.start_char, end_char=chunk.end_char, document_hash=document_hash,
                embedding_model=model, embedding_dimensions=dimensions, embedding_space=space,
                embedding=_vector(item.vector, dimensions), chunk_metadata=item.metadata,
            ))
        db.execute(delete(DocumentChunk).where(*_scope(user_id, product_id, document_id, model, dimensions, space)))
        db.add_all(rows)
        document.status = "indexed"
    return len(rows)


def search_vectors(
    db: Session, product_id: int, query_vector: Sequence[float], *,
    model: str | None = None, dimensions: int | None = None,
    top_k: int = 5, min_similarity: float | None = None, document_id: int | None = None,
    embedding_space: str = 'legacy',
) -> list[SearchResult]:
    """仅检索当前用户指定产品的资料；相似度范围 [-1, 1]，越大越相似。"""
    user_id = current_user_id(db)
    _positive_id(product_id)
    get_product(db, product_id)
    if document_id is not None:
        _document(db, product_id, document_id)
    model, dimensions = _model_options(model, dimensions, embedding_space)
    vector = _vector(query_vector, dimensions)
    if type(top_k) is not int or not 1 <= top_k <= 100:
        raise InvalidInputError("top_k 必须在 1 到 100 之间")
    if min_similarity is not None and (
        type(min_similarity) not in {int, float} or not math.isfinite(min_similarity)
        or not -1 <= min_similarity <= 1
    ):
        raise InvalidInputError("min_similarity 必须在 -1 到 1 之间")
    if db.get_bind().dialect.name != "postgresql":
        raise ServiceUnavailableError("向量检索需要 PostgreSQL 和已安装的 pgvector 扩展")

    matching_model = ((DocumentChunk.embedding_model == model)
                      & (DocumentChunk.embedding_dimensions == dimensions)
                      & (DocumentChunk.embedding_space == embedding_space))
    # CASE 避免 SQL 优化器先计算不同维度向量间的距离（尤其是相似度阈值过滤）。
    distance = case((matching_model, DocumentChunk.embedding.cosine_distance(vector))).label("distance")
    statement = select(DocumentChunk, ProductDocument.filename, distance).join(
        ProductDocument, DocumentChunk.document_id == ProductDocument.id,
    ).join(Product, ProductDocument.product_id == Product.id).where(
        DocumentChunk.user_id == user_id, DocumentChunk.product_id == product_id,
        Product.user_id == user_id, Product.id == product_id,
        ProductDocument.product_id == product_id, ProductDocument.status != "deleting",
        matching_model,
    )
    if document_id is not None:
        statement = statement.where(DocumentChunk.document_id == document_id)
    if min_similarity is not None:
        statement = statement.where(distance <= 1 - min_similarity)
    rows = db.execute(statement.order_by(distance, DocumentChunk.id).limit(top_k))
    return [SearchResult(
        chunk_id=chunk.chunk_id, text=chunk.text, user_id=chunk.user_id,
        product_id=chunk.product_id, document_id=chunk.document_id, filename=filename,
        chunk_index=chunk.chunk_index, similarity=max(-1.0, min(1.0, 1.0 - float(value))),
        metadata=dict(chunk.chunk_metadata),
    ) for chunk, filename, value in rows]


def delete_document_vectors(
    db: Session, product_id: int, document_id: int, *,
    model: str | None = None, dimensions: int | None = None,
) -> int:
    """删除当前模型/维度的文档向量，保留原文；删除文档则由外键清理全部向量。"""
    with write_transaction(db):
        user_id = current_user_id(db)
        document = _document(db, product_id, document_id, lock=True)
        model, dimensions = _model_options(model, dimensions)
        result = db.execute(delete(DocumentChunk).where(
            *_scope(user_id, product_id, document_id, model, dimensions),
        ))
        count = result.rowcount
        remaining = db.scalar(select(DocumentChunk.id).where(
            DocumentChunk.user_id == user_id, DocumentChunk.product_id == product_id,
            DocumentChunk.document_id == document_id,
        ).limit(1))
        document.status = "indexed" if remaining is not None else "uploaded"
    return count
