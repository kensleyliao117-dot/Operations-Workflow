"""调用通义千问生成文本向量，保留片段的来源元数据。

通过 httpx 请求兼容的 /embeddings 接口，向量保存由 vector_store 处理。
这里使用同步客户端，与业务层的同步调用保持一致。
"""
from dataclasses import dataclass
import math
import time
from typing import Sequence

import httpx
from sqlalchemy.orm import Session

import config
from .text_splitter import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE, TextChunk, split_document
from .token_counter import LocalTokenCounter, TokenCounter
from services.exceptions import InvalidInputError, ServiceUnavailableError

MODEL_DIMENSIONS = config.EMBEDDING_MODEL_DIMENSIONS
MAX_INPUT_BYTES = 8000  # 保守限制 UTF-8 字节量，不冒充模型的精确 Token 计数。
MODEL_MAX_TOKENS = {"text-embedding-v3": 8192, "text-embedding-v4": 8192}
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


class EmbeddingError(ServiceUnavailableError):
    """上游调用或返回数据异常，不回显 API Key、请求文本和响应正文。"""


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    chunk: TextChunk
    vector: list[float]
    model: str
    dimensions: int
    token_count: int | None = None
    token_count_source: str | None = None
    embedding_space: str = 'legacy'

    @property
    def chunk_id(self) -> str:
        return self.chunk.chunk_id

    @property
    def text(self) -> str:
        return self.chunk.text

    @property
    def metadata(self) -> dict[str, str | int]:
        metadata = {**self.chunk.metadata, "embedding_model": self.model,
                    "embedding_dimensions": self.dimensions, "embedding_space": self.embedding_space}
        if self.token_count is not None:
            metadata.update(token_count=self.token_count, token_count_source=self.token_count_source)
        return metadata


@dataclass(frozen=True, slots=True)
class EmbeddingTokenUsage:
    input_count: int
    # 只有启用分词器才有逐片段计数；不能把服务端批次总数平分给各片段。
    input_token_counts: tuple[int, ...] | None
    token_count_source: str | None
    provider_total_tokens: int | None


class QwenEmbeddings:
    embedding_space = 'legacy'
    def __init__(
        self, *, api_key: str | None = None, base_url: str | None = None,
        model: str | None = None, dimensions: int | None = None,
        batch_size: int | None = None, timeout: float = 30.0,
        max_retries: int = 2, http_client: httpx.Client | None = None,
        token_counter: TokenCounter | None = None,
    ):
        self.model, self.dimensions = config.embedding_model_options(model, dimensions)
        self.batch_size = config.EMBEDDING_BATCH_SIZE if batch_size is None else batch_size
        self.base_url = config.embedding_base_url(config.EMBEDDING_BASE_URL if base_url is None else base_url)
        key = config.EMBEDDING_API_KEY if api_key is None else api_key
        if not isinstance(key, str) or not key.strip():
            raise config.ConfigurationError("请配置 EMBEDDING_API_KEY 或 DASHSCOPE_API_KEY")
        if type(self.batch_size) is not int or not 1 <= self.batch_size <= 10:
            raise config.ConfigurationError("EMBEDDING_BATCH_SIZE 必须在 1 到 10 之间")
        if type(max_retries) is not int or not 0 <= max_retries <= 5:
            raise config.ConfigurationError("max_retries 必须在 0 到 5 之间")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise config.ConfigurationError("Embedding timeout 必须是正数")
        if token_counter is None and config.EMBEDDING_TOKENIZER_PATH:
            token_counter = LocalTokenCounter(config.EMBEDDING_TOKENIZER_PATH,
                                             model=config.EMBEDDING_TOKENIZER_MODEL)
        if token_counter is not None and token_counter.model != self.model:
            raise config.ConfigurationError("分词器声明的模型与当前 Embedding 模型不一致")
        self._token_counter = token_counter
        self.last_usage: EmbeddingTokenUsage | None = None
        self._headers = {"Authorization": f"Bearer {key.strip()}"}
        self._timeout = timeout
        self._max_retries = max_retries
        self._client = http_client if http_client is not None else httpx.Client(follow_redirects=False)
        self._owns_client = http_client is None

    def close(self):
        if self._owns_client:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.last_usage = None
        if isinstance(texts, (str, bytes)):
            raise InvalidInputError("embed_texts 需要文本列表，单条查询请用 embed_query")
        texts = list(texts)
        counts = [] if self._token_counter is not None else None
        # 在发出任何请求前检查全部输入，避免前几批已计费后才发现后面的错误。
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise InvalidInputError("向量输入必须是非空字符串")
            if self._token_counter is not None:
                count = self.count_tokens(text)
                if count > MODEL_MAX_TOKENS[self.model]:
                    raise InvalidInputError(f"片段超过 {MODEL_MAX_TOKENS[self.model]} Token，请继续切分后再向量化")
                counts.append(count)
            elif len(text.encode("utf-8")) > MAX_INPUT_BYTES:
                raise InvalidInputError("片段超过 8000 UTF-8 字节，请继续切分后再向量化")
        vectors = []
        provider_counts = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            payload = self._request(batch)
            vectors.extend(self._parse_vectors(payload, len(batch)))
            provider_counts.append(self._provider_tokens(payload))
        self.last_usage = EmbeddingTokenUsage(
            input_count=len(texts), input_token_counts=tuple(counts) if counts is not None else None,
            token_count_source=self._token_counter.source if self._token_counter else None,
            provider_total_tokens=(sum(provider_counts) if texts and all(
                count is not None for count in provider_counts) else None),
        )
        return vectors

    def count_tokens(self, text: str) -> int:
        if self._token_counter is None:
            raise config.ConfigurationError("没有配置匹配的 Embedding 分词器，不能提供精确的请求前 Token 计数")
        count = self._token_counter.count_tokens(text)
        if type(count) is not int or count < 0:
            raise config.ConfigurationError("分词器返回的 Token 数无效")
        return count

    @staticmethod
    def _provider_tokens(payload: dict) -> int | None:
        usage = payload.get("usage")
        if usage is None:
            return None
        if not isinstance(usage, dict):
            raise EmbeddingError("向量服务返回的 Token 用量格式无效")
        count = usage.get("total_tokens")
        if count is None:
            return None
        if type(count) is not int or count < 0:
            raise EmbeddingError("向量服务返回的 Token 用量无效")
        return count

    def embed_query(self, query: str) -> list[float]:
        """检索时使用与文档相同的模型和维度生成查询向量。"""
        return self.embed_texts([query])[0]

    def _request(self, texts: list[str]) -> dict:
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.post(
                    f"{self.base_url}/embeddings", headers=self._headers,
                    json={"model": self.model, "input": texts, "dimensions": self.dimensions,
                          "encoding_format": "float"}, timeout=self._timeout,
                    follow_redirects=False,
                )
            except httpx.RequestError:
                if attempt < self._max_retries:
                    time.sleep(0.5 * 2 ** attempt)
                    continue
                raise EmbeddingError("向量服务连接失败或超时，请稍后重试") from None
            if response.status_code in RETRY_STATUS_CODES and attempt < self._max_retries:
                time.sleep(0.5 * 2 ** attempt)
                continue
            if response.status_code in {401, 403}:
                raise EmbeddingError("向量服务认证失败，请检查 API Key、服务地址及模型访问权限")
            if not 200 <= response.status_code < 300:
                raise EmbeddingError(f"向量服务请求失败（HTTP {response.status_code}），请检查模型、文本长度及服务配置")
            try:
                payload = response.json()
            except ValueError:
                raise EmbeddingError("向量服务返回了无效 JSON") from None
            if not isinstance(payload, dict):
                raise EmbeddingError("向量服务返回格式无效")
            return payload
        raise EmbeddingError("向量服务请求失败")

    def _parse_vectors(self, payload: dict, count: int) -> list[list[float]]:
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != count:
            raise EmbeddingError("向量服务返回的片段数量与输入不一致")
        if payload.get("model", self.model) != self.model:
            raise EmbeddingError("向量服务返回的模型与请求不一致")
        ordered = {}
        for item in data:
            if not isinstance(item, dict):
                raise EmbeddingError("向量服务返回格式无效")
            index = item.get("index")
            vector = item.get("embedding")
            if type(index) is not int or not 0 <= index < count or index in ordered:
                raise EmbeddingError("向量服务返回的片段序号无效")
            if not isinstance(vector, list) or len(vector) != self.dimensions:
                raise EmbeddingError("向量服务返回的维度与配置不一致")
            try:
                if any(type(value) not in {int, float} or not math.isfinite(value) for value in vector):
                    raise ValueError("invalid vector")
                ordered[index] = [float(value) for value in vector]
            except (ValueError, OverflowError):
                raise EmbeddingError("向量服务返回了无效数值") from None
        return [ordered[index] for index in range(count)]


def embed_chunks(chunks: Sequence[TextChunk], *, embedder: QwenEmbeddings | None = None) -> list[EmbeddedChunk]:
    """向量化可信内部片段，返回原文、向量及元数据，不写数据库。"""
    chunks = list(chunks)
    if not chunks:
        return []
    if any(not isinstance(chunk, TextChunk) for chunk in chunks):
        raise InvalidInputError("embed_chunks 需要 TextChunk 列表")
    if embedder is None:
        with QwenEmbeddings() as client:
            return embed_chunks(chunks, embedder=client)
    vectors = embedder.embed_texts([chunk.text for chunk in chunks])
    usage = embedder.last_usage
    return [EmbeddedChunk(chunk=chunk, vector=vector, model=embedder.model, dimensions=embedder.dimensions,
                          token_count=usage.input_token_counts[index] if usage and usage.input_token_counts is not None else None,
                          token_count_source=usage.token_count_source if usage else None,
                          embedding_space=getattr(embedder, 'embedding_space', 'legacy'))
            for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))]


def embed_document(
    db: Session, product_id: int, document_id: int, *,
    chunk_size: int = DEFAULT_CHUNK_SIZE, chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    embedder: QwenEmbeddings | None = None,
) -> list[EmbeddedChunk]:
    """已认证 Session → 校验归属 → 读取 → 切分 → 千问向量。"""
    chunks = split_document(db, product_id, document_id, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return embed_chunks(chunks, embedder=embedder)
