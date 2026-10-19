"""按文本边界切分产品资料，保留检索和引用需要的来源信息。

这里按 Python 字符数计算长度和重叠量，Token 限制由 Embedding 模块检查。
切分、向量生成和入库分别处理，便于独立调整和验证。
"""
from dataclasses import dataclass
import hashlib
import json

from sqlalchemy.orm import Session

from .document_loader import load_document
from services.common import current_user_id
from services.exceptions import InvalidInputError

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100
MAX_CHUNKS = 10_000
SPLITTER_VERSION = "char-boundary-v1"
SEPARATORS = ("\n\n", "\n", "。", "！", "？", ". ", "! ", "? ", "；", "; ", "，", ", ", " ")


@dataclass(frozen=True, slots=True)
class TextChunk:
    chunk_id: str
    text: str
    user_id: int
    product_id: int
    document_id: int
    chunk_index: int
    start_char: int
    end_char: int
    document_hash: str

    @property
    def metadata(self) -> dict[str, str | int]:
        """适合随向量保存的扁平元数据；文本独立放在 text。"""
        return {
            "chunk_id": self.chunk_id,
            "user_id": self.user_id,
            "product_id": self.product_id,
            "document_id": self.document_id,
            "chunk_index": self.chunk_index,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "document_hash": self.document_hash,
            "splitter_version": SPLITTER_VERSION,
        }


def split_document(
    db: Session, product_id: int, document_id: int, *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[TextChunk]:
    """读取并切分当前用户的资料；调用方传入已认证的数据库 Session。"""
    _validate_options(chunk_size, chunk_overlap)
    user_id = current_user_id(db)
    text = load_document(db, product_id, document_id)
    return split_text(text, user_id=user_id, product_id=product_id, document_id=document_id,
                      chunk_size=chunk_size, chunk_overlap=chunk_overlap)


def split_text(
    text: str, *, user_id: int, product_id: int, document_id: int,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[TextChunk]:
    """切分可信内部文本。面向上传资料时请使用会校验归属的 split_document。

    chunk_index 从 0 开始；[start_char:end_char] 对应传入 text 的精确切片。
    去掉每段首尾空白，但不改写文字和段内换行。空白文本返回空列表。
    """
    if not isinstance(text, str):
        raise InvalidInputError("待切分资料必须是字符串")
    _validate_options(chunk_size, chunk_overlap)
    for name, value in (("user_id", user_id), ("product_id", product_id), ("document_id", document_id)):
        if type(value) is not int or value <= 0:
            raise InvalidInputError(f"{name} 必须是大于 0 的整数")
    if not text.strip():
        return []

    document_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    chunks = []
    start = 0
    last_content_end = 0
    while start < len(text):
        end = _find_end(text, start, chunk_size, chunk_overlap)
        content_start, content_end = start, end
        while content_start < content_end and text[content_start].isspace():
            content_start += 1
        while content_end > content_start and text[content_end - 1].isspace():
            content_end -= 1
        # 不生成空白段，也不为只有重叠文本的尾部再生成一个片段。
        if content_start < content_end and content_end > last_content_end:
            if len(chunks) >= MAX_CHUNKS:
                raise InvalidInputError("资料片段超过 10000 个，请增大 chunk_size 或减小 chunk_overlap")
            identity = [SPLITTER_VERSION, user_id, product_id, document_id, document_hash,
                        chunk_size, chunk_overlap, content_start, content_end]
            chunk_id = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
            chunks.append(TextChunk(
                chunk_id=chunk_id, text=text[content_start:content_end], user_id=user_id,
                product_id=product_id, document_id=document_id, chunk_index=len(chunks),
                start_char=content_start, end_char=content_end, document_hash=document_hash,
            ))
            last_content_end = content_end
        if end == len(text):
            break
        # _find_end 确保段长大于重叠量，下一轮一定向前推进。
        start = end - chunk_overlap
    return chunks


def _validate_options(chunk_size: int, chunk_overlap: int) -> None:
    if type(chunk_size) is not int or chunk_size <= 0:
        raise InvalidInputError("chunk_size 必须是大于 0 的整数")
    if type(chunk_overlap) is not int or not 0 <= chunk_overlap < chunk_size:
        raise InvalidInputError("chunk_overlap 必须是整数，且满足 0 <= chunk_overlap < chunk_size")


def _find_end(text: str, start: int, chunk_size: int, chunk_overlap: int) -> int:
    end = min(start + chunk_size, len(text))
    if end == len(text):
        return end
    # 在窗口后半段寻找边界，防止过早遇到换行造成大量过短片段。
    lower_bound = start + max(chunk_size // 2, chunk_overlap + 1)
    for separator in SEPARATORS:
        position = text.rfind(separator, lower_bound, end)
        if position >= 0:
            return position + len(separator)
    return end
