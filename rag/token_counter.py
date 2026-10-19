"""按显式提供的模型分词器计数，不下载文件或执行远程模型代码。"""
import hashlib
from pathlib import Path
from typing import Protocol

from tokenizers import Tokenizer

from config import BASE_DIR, ConfigurationError
from services.exceptions import InvalidInputError


class TokenCounter(Protocol):
    model: str
    source: str

    def count_tokens(self, text: str) -> int: ...


class LocalTokenCounter:
    """精确计算所配置 tokenizer 的输出长度；云端一致性取决于文件是否匹配。"""
    def __init__(self, path: str | Path, *, model: str):
        if not model:
            raise ConfigurationError("配置 tokenizer.json 时还必须指定 EMBEDDING_TOKENIZER_MODEL")
        path = Path(path).expanduser()
        if not path.is_absolute():
            path = BASE_DIR / path
        try:
            raw = path.read_bytes()
            self._tokenizer = Tokenizer.from_str(raw.decode("utf-8-sig"))
            # 禁止文件中预置的截断/填充影响真实长度，保留特殊 Token 后处理规则。
            self._tokenizer.no_truncation()
            self._tokenizer.no_padding()
        except Exception:
            raise ConfigurationError("无法加载 Embedding tokenizer.json，请检查路径和文件格式") from None
        self.model = model
        self.source = "tokenizer-sha256:" + hashlib.sha256(raw).hexdigest()

    def count_tokens(self, text: str) -> int:
        if not isinstance(text, str):
            raise InvalidInputError("Token 计数输入必须是字符串")
        return len(self._tokenizer.encode(text, add_special_tokens=True).ids)
