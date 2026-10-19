"""千问同步文生图与临时图片下载；不包含数据库或本地持久化操作。"""
from dataclasses import dataclass
from io import BytesIO
import re
from urllib.parse import urlsplit
import warnings

import httpx
from PIL import Image, UnidentifiedImageError

import config
from services.exceptions import InvalidInputError, ServiceUnavailableError


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 2048 * 2048
SIZES = {"1728*2368", "2048*2048", "1536*2688"}
MODELS = {"qwen-image-2.0", "qwen-image-2.0-pro", "qwen-image-2.0-2026-03-03",
          "qwen-image-2.0-pro-2026-03-03", "qwen-image-2.0-pro-2026-04-22", "qwen-image-2.0-pro-2026-06-22"}


class QwenImageError(ServiceUnavailableError):
    """供应商错误不包含密钥、签名链接或原始响应。"""


@dataclass(frozen=True, slots=True)
class ImageResult:
    model: str
    request_id: str | None
    urls: tuple[str, ...]


def validate_image_url(value):
    """仅下载百炼返回的官方结果桶，不转发 API Authorization。"""
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.username is None and parsed.password is None
                 and parsed.port in {None, 443} and not parsed.fragment
                 and re.fullmatch(r"dashscope-[a-z0-9-]+\.oss(?:-accelerate|-[a-z0-9-]+)?\.aliyuncs\.com",
                                  parsed.hostname or "") is not None)
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise QwenImageError("生图结果包含不受支持的图片下载地址")
    return value


def validate_png(data: bytes) -> tuple[int, int]:
    try:
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("invalid image size")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                width, height = image.size
                if image.format != "PNG" or width * height > MAX_PIXELS or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("unsupported image")
                image.verify()
            with Image.open(BytesIO(data)) as image:
                image.load()
        return width, height
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise QwenImageError("下载结果不是有效的 PNG 图片或超过大小限制") from None


class QwenImageClient:
    def __init__(self, *, http_client: httpx.Client | None = None):
        key = config.QWEN_IMAGE_API_KEY
        if not isinstance(key, str) or not key.strip():
            raise config.ConfigurationError("请在 .env 配置 QWEN_IMAGE_API_KEY 或 DASHSCOPE_API_KEY")
        self.model = config.QWEN_IMAGE_MODEL
        if self.model not in MODELS:
            raise config.ConfigurationError("QWEN_IMAGE_MODEL 必须为已支持的 qwen-image-2.0 系列同步模型")
        try:
            base = config.QWEN_IMAGE_BASE_URL.strip().rstrip("/")
            parsed = urlsplit(base)
            valid_host = parsed.hostname in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"} or re.fullmatch(
                r"[a-z0-9-]+\.(?:cn-beijing|ap-southeast-1)\.maas\.aliyuncs\.com", parsed.hostname or "")
            valid = (valid_host and parsed.scheme == "https" and not parsed.path and not parsed.query
                     and not parsed.fragment and parsed.username is None and parsed.password is None
                     and parsed.port in {None, 443})
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise config.ConfigurationError("QWEN_IMAGE_BASE_URL 必须为阿里云百炼官方 HTTPS 根地址")
        self.endpoint = base + "/api/v1/services/aigc/multimodal-generation/generation"
        self._headers = {"Authorization": f"Bearer {key.strip()}"}
        self._timeout = httpx.Timeout(config.QWEN_IMAGE_TIMEOUT_SECONDS, connect=10.0)
        self._client = http_client if http_client is not None else httpx.Client(follow_redirects=False, trust_env=False)
        self._owns_client = http_client is None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self._owns_client:
            self._client.close()

    def generate(self, prompt: str, negative_prompt: str, *, count: int = 1, size: str = "1728*2368") -> ImageResult:
        if (not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 600 or "\x00" in prompt
                or not isinstance(negative_prompt, str) or not negative_prompt.strip() or len(negative_prompt) > 500
                or type(count) is not int or not 1 <= count <= 4 or size not in SIZES):
            raise InvalidInputError("配图 Prompt、图片数量或尺寸无效")
        try:
            response = self._client.post(self.endpoint, headers=self._headers,
                json={"model": self.model, "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
                      "parameters": {"n": count, "size": size, "negative_prompt": negative_prompt,
                                     "prompt_extend": False, "watermark": True}},
                timeout=self._timeout, follow_redirects=False)
        except httpx.TimeoutException:
            raise QwenImageError("千问生图请求超时，未自动重试；可稍后确认账户用量后重试") from None
        except httpx.RequestError:
            raise QwenImageError("千问生图网络连接失败，请稍后重试") from None
        if not 200 <= response.status_code < 300:
            raise QwenImageError(f"千问生图请求失败（HTTP {response.status_code}），请检查模型权限、余额及地域配置")
        try:
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("code") or "error" in payload:
                raise ValueError("API error")
            choices = payload["output"]["choices"]
            if not isinstance(choices, list) or not choices:
                raise ValueError("missing choices")
            urls = []
            for choice in choices:
                if choice.get("finish_reason") not in {None, "stop"}:
                    raise ValueError("incomplete images")
                for item in choice["message"]["content"]:
                    if "image" in item:
                        urls.append(validate_image_url(item["image"]))
            if len(urls) != count or len(set(urls)) != count:
                raise ValueError("incorrect image count")
            request_id = payload.get("request_id")
            if request_id is not None and (not isinstance(request_id, str) or len(request_id) > 200):
                raise ValueError("invalid request ID")
            return ImageResult(self.model, request_id, tuple(urls))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise QwenImageError("千问未返回完整、有效的生图结果") from None

    def download(self, url: str, *, size: str) -> bytes:
        validate_image_url(url)
        try:
            # 独立请求不携带生图接口密钥，不跟随重定向，也不允许客户端级认证外发。
            with httpx.Client(follow_redirects=False, trust_env=False) as downloader:
                return self._download(downloader, url, size)
        except httpx.TimeoutException:
            raise QwenImageError("生成图片下载超时，请稍后重试") from None
        except httpx.RequestError:
            raise QwenImageError("生成图片下载失败，请稍后重试") from None

    @staticmethod
    def _download(downloader, url, size):
        with downloader.stream("GET", url, timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=False) as response:
            if response.status_code != 200:
                raise QwenImageError("生成图片下载失败或链接已失效")
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    raise QwenImageError("生成图片超过 20 MiB 大小限制")
        result = bytes(data)
        if validate_png(result) != tuple(map(int, size.split("*"))):
            raise QwenImageError("生成图片尺寸与请求不一致")
        return result
