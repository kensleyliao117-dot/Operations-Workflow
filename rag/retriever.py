"""产品资料入库和检索入口，供接口和 Agent 复用。

使用认证后的请求专属 Session，在远程 Embedding 前结束只读事务，
避免长时间占用数据库连接。写入前重新校验归属，在同一事务里替换向量。
"""
from contextlib import nullcontext
from dataclasses import dataclass
import logging
import math

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import config
from models.document_chunk import DocumentChunk
from models.product import Product, ProductDocument
from services import product as product_service
from services.common import current_user_id, get_record, write_transaction
from services.exceptions import InvalidInputError, NotFoundError, ServiceUnavailableError
from .embeddings import QwenEmbeddings, embed_chunks
from .text_splitter import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE, split_document
from .vector_store import save_document_vectors, search_vectors

logger = logging.getLogger(__name__)


def _account_embedder(db):
    from services.resource_models import select_profile
    from integrations.llm.resource_clients import ResourceEmbeddings
    profile = select_profile(db, 'embedding')
    return ResourceEmbeddings(profile) if profile is not None else None


@dataclass(frozen=True, slots=True)
class IndexResult:
    product_id: int
    document_id: int
    filename: str
    status: str
    chunk_count: int
    model: str
    dimensions: int
    document_hash: str


@dataclass(frozen=True, slots=True)
class RetrievedSource:
    citation: str
    chunk_id: str
    document_id: int
    filename: str
    chunk_index: int
    similarity: float
    text: str
    truncated: bool
    metadata: dict


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    product_id: int
    query: str
    model: str
    dimensions: int
    context: str
    sources: list[RetrievedSource]


def _split_options(chunk_size, chunk_overlap):
    # 即使一个字符占 4 个 UTF-8 字节，也不会超过现有 Embedding 8000 字节保护值。
    if (type(chunk_size) is not int or not 1 <= chunk_size <= 2000
            or type(chunk_overlap) is not int or not 0 <= chunk_overlap < chunk_size):
        raise InvalidInputError("chunk_size 必须在 1 到 2000 之间，chunk_overlap 必须 >= 0 且小于 chunk_size")


def _mark_failed(db, product_id, document_id):
    """首次入库失败记状态；已有可用快照保留 indexed，删除中的文档不复活。"""
    try:
        with write_transaction(db):
            get_record(db, Product, product_id, lock=True)
            document = get_record(db, ProductDocument, document_id, lock=True)
            if document.product_id != product_id or document.status == "deleting":
                return
            existing = db.scalar(select(DocumentChunk.id).where(
                DocumentChunk.user_id == current_user_id(db), DocumentChunk.product_id == product_id,
                DocumentChunk.document_id == document_id,
            ).limit(1))
            document.status = "indexed" if existing is not None else "index_failed"
    except NotFoundError:
        pass  # 并发删除已完成。
    except SQLAlchemyError as exc:
        db.rollback()
        logger.warning("Cannot save indexing status: %s", type(exc).__name__)


def index_document(
    db: Session, product_id: int, document_id: int, *,
    chunk_size: int = DEFAULT_CHUNK_SIZE, chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    embedder: QwenEmbeddings | None = None,
) -> IndexResult:
    """已上传资料 → 读取/切分 → 千问 Embedding → 原子入库；可重复调用。"""
    _split_options(chunk_size, chunk_overlap)
    document = product_service.get_document(db, product_id, document_id)
    filename = document.filename
    try:
        chunks = split_document(db, product_id, document_id, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        if embedder is None:
            embedder = _account_embedder(db)
        db.rollback()  # 仅结束本入口的只读事务，不持有连接等待远程模型。
        with (nullcontext(embedder) if embedder is not None else QwenEmbeddings()) as client:
            vectors = embed_chunks(chunks, embedder=client)
            count = save_document_vectors(db, product_id, document_id, vectors)
            return IndexResult(product_id, document_id, filename, "indexed", count,
                               client.model, client.dimensions, chunks[0].document_hash)
    except config.ConfigurationError as exc:
        db.rollback()
        _mark_failed(db, product_id, document_id)
        raise ServiceUnavailableError(str(exc)) from None
    except (InvalidInputError, ServiceUnavailableError):
        db.rollback()
        _mark_failed(db, product_id, document_id)
        raise


def upload_and_index_document(
    db: Session, product_id: int, upload, *,
    chunk_size: int = DEFAULT_CHUNK_SIZE, chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    embedder: QwenEmbeddings | None = None,
) -> IndexResult:
    """一键上传和入库；入库失败保留已上传文档，在错误中提供重试用文档 ID。"""
    _split_options(chunk_size, chunk_overlap)
    document_id = product_service.upload_document(db, product_id, upload)
    try:
        return index_document(db, product_id, document_id, chunk_size=chunk_size,
                              chunk_overlap=chunk_overlap, embedder=embedder)
    except (InvalidInputError, ServiceUnavailableError) as exc:
        error_type = InvalidInputError if isinstance(exc, InvalidInputError) else ServiceUnavailableError
        raise error_type(
            f"资料已上传（文档 ID：{document_id}），入库失败：{exc}。"
            f"可调用 /products/{product_id}/rag/documents/{document_id}/index 重试，无需重复上传"
        ) from None


def _has_vectors(db, product_id, document_id, model, dimensions, embedding_space='legacy'):
    statement = select(DocumentChunk.id).join(ProductDocument, ProductDocument.id == DocumentChunk.document_id).where(
        DocumentChunk.user_id == current_user_id(db), DocumentChunk.product_id == product_id,
        ProductDocument.product_id == product_id, ProductDocument.status != "deleting",
        DocumentChunk.embedding_model == model, DocumentChunk.embedding_dimensions == dimensions,
        DocumentChunk.embedding_space == embedding_space,
    )
    if document_id is not None:
        statement = statement.where(DocumentChunk.document_id == document_id)
    return db.scalar(statement.limit(1)) is not None


def retrieve(
    db: Session, product_id: int, query: str, *, top_k: int = 5,
    min_similarity: float | None = None, document_id: int | None = None,
    max_context_chars: int = 12000, embedder: QwenEmbeddings | None = None,
) -> RetrievalResult:
    """自然语言 → 同模型查询向量 → 当前用户/产品检索 → 带引用的上下文。

    没有匹配模型的资料或没有命中时返回空 context/sources，不编造产品信息。
    context 的长度限制包含引用标题和分隔符，以 Python 字符数计。
    """
    current_user_id(db)
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 2000:
        raise InvalidInputError("query 去掉首尾空白后必须为 1 到 2000 个字符")
    query = query.strip()
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise InvalidInputError("top_k 必须在 1 到 20 之间")
    if type(max_context_chars) is not int or not 256 <= max_context_chars <= 50000:
        raise InvalidInputError("max_context_chars 必须在 256 到 50000 之间")
    if min_similarity is not None and (type(min_similarity) not in {int, float}
            or not math.isfinite(min_similarity) or not -1 <= min_similarity <= 1):
        raise InvalidInputError("min_similarity 必须在 -1 到 1 之间")
    product_service.get_product(db, product_id)
    if document_id is not None:
        product_service.get_document(db, product_id, document_id)
    if embedder is None:
        embedder = _account_embedder(db)
    space = embedder.embedding_space if embedder is not None else 'legacy'
    try:
        from .vector_store import _model_options
        model, dimensions = _model_options(
            embedder.model if embedder is not None else None,
            embedder.dimensions if embedder is not None else None,
            space,
        )
    except config.ConfigurationError as exc:
        raise ServiceUnavailableError(str(exc)) from None
    has_vectors = _has_vectors(db, product_id, document_id, model, dimensions, space)
    if not has_vectors and space != 'legacy':
        old_vectors = db.scalar(select(DocumentChunk.id).where(DocumentChunk.user_id == current_user_id(db),
            DocumentChunk.product_id == product_id,
            *([DocumentChunk.document_id == document_id] if document_id is not None else [])).limit(1))
        if old_vectors is not None:
            db.rollback()
            raise InvalidInputError('当前 Embedding 连接与已入库向量不一致，请在产品资料页面重新入库')
    db.rollback()
    if not has_vectors:
        return RetrievalResult(product_id, query, model, dimensions, "", [])
    try:
        with (nullcontext(embedder) if embedder is not None else QwenEmbeddings()) as client:
            if (client.model, client.dimensions) != (model, dimensions):
                raise config.ConfigurationError("查询 Embedding 客户端与检索所用模型或维度不一致，请检查向量配置")
            vector = client.embed_query(query)
            matches = search_vectors(db, product_id, vector, model=client.model, dimensions=client.dimensions,
                                     top_k=top_k, min_similarity=min_similarity, document_id=document_id,
                                     **({'embedding_space': space} if space != 'legacy' else {}))
    except config.ConfigurationError as exc:
        raise ServiceUnavailableError(str(exc)) from None

    parts, sources, used = [], [], 0
    for match in matches:
        citation = f"[{len(sources) + 1}]"
        # 标题只含服务器生成的引用和数值 ID；文件名放结构化 sources，正文视为不可信资料。
        header = f"{citation} 文档 ID：{match.document_id}；片段：{match.chunk_index}\n"
        separator = "\n\n" if parts else ""
        remaining = max_context_chars - used - len(separator) - len(header)
        if remaining <= 0:
            break
        text = match.text[:remaining]
        part = separator + header + text
        parts.append(part)
        used += len(part)
        sources.append(RetrievedSource(citation, match.chunk_id, match.document_id, match.filename,
            match.chunk_index, match.similarity, text, len(text) < len(match.text), dict(match.metadata)))
        if len(text) < len(match.text):
            break
    return RetrievalResult(product_id, query, model, dimensions, "".join(parts), sources)
