import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class ConfigurationError(RuntimeError):
    """启动配置无效；错误信息不能包含密钥或完整数据库地址。"""


def validate_database_url(value: str | None) -> str:
    if not value or not value.strip():
        raise ConfigurationError("缺少 DATABASE_URL，请在项目根目录的 .env 或环境变量中设置 PostgreSQL 连接地址")
    value = value.strip()
    try:
        url = make_url(value)
        valid = url.get_backend_name() == "postgresql" and bool(url.database)
        # 访问 port，使非法端口在启动时被发现。
        url.port
    except (ArgumentError, ValueError, TypeError):
        valid = False
    if not valid:
        raise ConfigurationError("DATABASE_URL 格式无效：请使用包含数据库名的 PostgreSQL 连接地址") from None
    return value


def load_cors_origins() -> list[str]:
    value = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    origins = []
    for item in value.split(","):
        origin = item.strip().rstrip("/")
        if not origin:
            continue
        try:
            parsed = urlsplit(origin)
            valid = (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                     and not parsed.path and not parsed.query and not parsed.fragment
                     and parsed.username is None and parsed.password is None)
            parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise ConfigurationError("CORS_ORIGINS 需为逗号分隔的完整前端地址，例如 http://localhost:5173")
        if origin not in origins:
            origins.append(origin)
    return origins


def require_jwt_secret() -> str:
    if len(JWT_SECRET_KEY.encode("utf-8")) < 32:
        raise ConfigurationError("请在 .env 设置随机生成的 JWT_SECRET_KEY（至少 32 字节）")
    return JWT_SECRET_KEY


def load_upload_dir() -> Path:
    value = os.getenv("UPLOAD_DIR", "").strip()
    path = Path(value).expanduser() if value else BASE_DIR / "storage" / "uploads"
    if not path.is_absolute():
        path = BASE_DIR / path
    path = path.resolve()
    if path.exists() and not path.is_dir():
        raise ConfigurationError("UPLOAD_DIR 必须指向目录，不能指向文件")
    return path


def positive_integer_setting(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ConfigurationError(f"{name} 必须是正整数") from None
    if value <= 0:
        raise ConfigurationError(f"{name} 必须是正整数")
    return value


def chrome_cdp_url(value: str) -> str:
    """CDP HTTP 根地址或浏览器 WebSocket 地址；错误不回显地址或凭据。"""
    if not isinstance(value, str):
        raise ConfigurationError("CHROME_CDP_URL 必须是 Chrome CDP 地址")
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        valid = (bool(parsed.hostname) and parsed.username is None and parsed.password is None
                 and not parsed.query and not parsed.fragment
                 and not any(c.isspace() for c in value))
        if parsed.scheme in {"http", "https"}:
            valid = valid and not parsed.path
        elif parsed.scheme in {"ws", "wss"}:
            valid = valid and parsed.path.startswith("/devtools/browser/") and bool(parsed.path.removeprefix("/devtools/browser/"))
        else:
            valid = False
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ConfigurationError("CHROME_CDP_URL 需为 HTTP 根地址或 /devtools/browser/ WebSocket 地址，不能包含凭据或查询参数")
    return value


EMBEDDING_MODEL_DIMENSIONS = {
    "text-embedding-v3": {64, 128, 256, 512, 768, 1024},
    "text-embedding-v4": {64, 128, 256, 512, 768, 1024, 1536, 2048},
}


def embedding_model_options(model: str | None = None, dimensions: int | None = None) -> tuple[str, int]:
    """Embedding 客户端、入库和检索共用同一套模型默认值与维度校验。"""
    model = EMBEDDING_MODEL if model is None else model
    dimensions = EMBEDDING_DIMENSIONS if dimensions is None else dimensions
    if not isinstance(model, str) or model not in EMBEDDING_MODEL_DIMENSIONS:
        raise ConfigurationError("EMBEDDING_MODEL 必须是 text-embedding-v3 或 text-embedding-v4")
    if type(dimensions) is not int or dimensions not in EMBEDDING_MODEL_DIMENSIONS[model]:
        raise ConfigurationError("EMBEDDING_DIMENSIONS 不在当前向量模型支持范围内")
    return model, dimensions


def embedding_base_url(value: str) -> str:
    """校验兼容接口根地址；错误信息不回显地址中的凭据。"""
    if not isinstance(value, str):
        raise ConfigurationError("EMBEDDING_BASE_URL 必须是 HTTPS 兼容接口根地址")
    value = value.strip().rstrip("/")
    try:
        url = urlsplit(value)
        valid = (url.scheme == "https" and bool(url.hostname) and url.username is None
                 and url.password is None and not url.query and not url.fragment
                 and not url.path.endswith("/embeddings"))
        url.port
    except ValueError:
        valid = False
    if not valid:
        raise ConfigurationError("EMBEDDING_BASE_URL 应是 HTTPS 兼容接口根地址，不要包含密钥或 /embeddings 后缀")
    return value

#deepseek api
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
URL_DEEPSEEK = os.getenv("DEEPSEEK_URL", "").strip() or "https://api.deepseek.com"
MODEL_DEEPSEEK = os.getenv("DEEPSEEK_MODEL", "").strip() or "deepseek-flash"

#千问api
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
MODEL_DASHSCOPE=os.getenv("DASHSCOPE_MODEL")
DASHSCOPE_URL=os.getenv("DASHSCOPE_URL")
# 系统模型兼容旧部署配置，用户模型在数据库中独立保存。
MODEL_CREDENTIALS_KEY = os.getenv("MODEL_CREDENTIALS_KEY", "").strip()
ANALYSIS_MODELS_JSON = os.getenv("ANALYSIS_MODELS_JSON", "").strip()
QWEN_CHAT_MODEL = os.getenv("QWEN_CHAT_MODEL", "").strip()
QWEN_CHAT_BASE_URL = os.getenv("QWEN_CHAT_BASE_URL", "").strip() or "https://dashscope.aliyuncs.com/compatible-mode/v1"
# 向量配置独立于聊天模型，确保入库和检索使用同一套向量模型。
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY") or DASHSCOPE_API_KEY
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "").strip() or "text-embedding-v3"
EMBEDDING_BASE_URL = embedding_base_url(os.getenv("EMBEDDING_BASE_URL", "").strip()
                                      or "https://dashscope.aliyuncs.com/compatible-mode/v1")
EMBEDDING_DIMENSIONS = positive_integer_setting("EMBEDDING_DIMENSIONS", 1024)
EMBEDDING_BATCH_SIZE = positive_integer_setting("EMBEDDING_BATCH_SIZE", 10)
embedding_model_options()
if EMBEDDING_BATCH_SIZE > 10:
    raise ConfigurationError("EMBEDDING_BATCH_SIZE 必须在 1 到 10 之间")
# 必须是与实际 Embedding 模型配套的 tokenizer.json，不自动套用聊天模型分词器。
EMBEDDING_TOKENIZER_PATH = os.getenv("EMBEDDING_TOKENIZER_PATH", "").strip()
EMBEDDING_TOKENIZER_MODEL = os.getenv("EMBEDDING_TOKENIZER_MODEL", "").strip()
#数据库
DATABASE_URL = validate_database_url(os.getenv("DATABASE_URL"))
# 经验库使用独立数据库；空配置沿用同一 PostgreSQL 服务器和凭据。
_memory_url = os.getenv("MEMORY_DATABASE_URL", "").strip()
MEMORY_DATABASE_URL = validate_database_url(_memory_url) if _memory_url else make_url(DATABASE_URL).set(
    database="OperationsMemory").render_as_string(hide_password=False)
if make_url(MEMORY_DATABASE_URL).database.casefold() == make_url(DATABASE_URL).database.casefold():
    raise ConfigurationError("MEMORY_DATABASE_URL 必须使用独立于业务库的数据库名称")
CORS_ORIGINS = load_cors_origins()
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
JWT_ISSUER = "operations-workflow"
JWT_AUDIENCE = "operations-workflow-api"
ACCESS_TOKEN_SECONDS = 3600
STORAGE_DIR = BASE_DIR / "storage"
GENERATED_IMAGE_DIR = STORAGE_DIR / "generated_images"
# 生图配置独立于聊天和 Embedding；按需校验，不影响仅使用文本功能的启动。
QWEN_IMAGE_API_KEY = os.getenv("QWEN_IMAGE_API_KEY") or DASHSCOPE_API_KEY
QWEN_IMAGE_MODEL = os.getenv("QWEN_IMAGE_MODEL", "").strip() or "qwen-image-2.0"
QWEN_IMAGE_BASE_URL = os.getenv("QWEN_IMAGE_BASE_URL", "").strip() or "https://dashscope.aliyuncs.com"
QWEN_IMAGE_TIMEOUT_SECONDS = positive_integer_setting("QWEN_IMAGE_TIMEOUT_SECONDS", 180)
UPLOAD_DIR = load_upload_dir()
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# 已启动 Chrome 的连接配置，浏览器启动和安装单独处理。
CHROME_CDP_URL = chrome_cdp_url(os.getenv("CHROME_CDP_URL", "").strip() or "http://127.0.0.1:9222")
CHROME_CDP_TIMEOUT_MS = positive_integer_setting("CHROME_CDP_TIMEOUT_MS", 10000)
CHROME_PAGE_TIMEOUT_MS = positive_integer_setting("CHROME_PAGE_TIMEOUT_MS", 30000)
