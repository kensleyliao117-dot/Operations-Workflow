"""DeepSeek HTTP 调用封装，配置统一从 config.py 读取。

这里使用同步 HTTP 请求获取完整正文，数据库操作交给业务层处理。
"""
import math
from urllib.parse import urlsplit

import httpx

import config
from services.exceptions import InvalidInputError, ServiceUnavailableError


class DeepSeekError(ServiceUnavailableError):
    """上游请求或响应异常；错误不回显密钥、Prompt 或供应商响应正文。"""


class DeepSeekTimeoutError(DeepSeekError):
    """连接、读取、写入或连接池等待超时。"""


class DeepSeekClient:
    def __init__(self, *, timeout: float = 60.0, http_client: httpx.Client | None = None,
                 model: str | None = None):
        key = config.DEEPSEEK_API_KEY
        if not isinstance(key, str) or not key.strip():
            raise config.ConfigurationError("请在 .env 中配置 DEEPSEEK_API_KEY")
        if type(timeout) not in {int, float} or not math.isfinite(timeout) or timeout <= 0:
            raise config.ConfigurationError("DeepSeek timeout 必须是有限正数")
        self.model = config.MODEL_DEEPSEEK if model is None else model
        if not isinstance(self.model, str) or not self.model.strip():
            raise config.ConfigurationError("请在 .env 中配置 DEEPSEEK_MODEL")
        self.model = self.model.strip()
        self.base_url = self._base_url(config.URL_DEEPSEEK)
        self._headers = {"Authorization": f"Bearer {key.strip()}"}
        self._timeout = httpx.Timeout(timeout, connect=min(10.0, timeout))
        self._client = http_client if http_client is not None else httpx.Client(follow_redirects=False)
        self._owns_client = http_client is None

    @staticmethod
    def _base_url(value: str) -> str:
        if not isinstance(value, str):
            raise config.ConfigurationError("DEEPSEEK_URL 必须是 HTTPS 接口根地址")
        value = value.strip().rstrip("/")
        try:
            parsed = urlsplit(value)
            valid = (parsed.scheme == "https" and bool(parsed.hostname)
                     and parsed.username is None and parsed.password is None
                     and not parsed.query and not parsed.fragment
                     and not parsed.path.endswith("/chat/completions"))
            parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise config.ConfigurationError("DEEPSEEK_URL 应是 HTTPS 根地址，不包含凭据、查询参数或 /chat/completions 后缀")
        return value

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        for name, value in (("system_prompt", system_prompt), ("user_prompt", user_prompt)):
            if not isinstance(value, str) or not value.strip():
                raise InvalidInputError(f"{name} 必须是非空字符串")
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers,
                json={"model": self.model, "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ], "stream": False},
                timeout=self._timeout, follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise DeepSeekTimeoutError("DeepSeek 请求超时，请稍后重试") from None
        except httpx.RequestError:
            raise DeepSeekError("DeepSeek 网络连接失败，请稍后重试") from None
        if not 200 <= response.status_code < 300:
            reasons = {
                400: "请求格式或模型名称无效", 401: "API Key 认证失败", 402: "账户余额不足",
                403: "没有接口或模型访问权限", 404: "接口或模型不存在，请检查 DEEPSEEK_MODEL 和 DEEPSEEK_URL",
                422: "请求参数无效", 429: "请求过于频繁，请稍后重试",
                500: "服务内部错误，请稍后重试", 503: "服务繁忙，请稍后重试",
            }
            reason = reasons.get(response.status_code, "服务请求失败")
            raise DeepSeekError(f"DeepSeek {reason}（HTTP {response.status_code}）")
        if not response.content.strip():
            raise DeepSeekError("DeepSeek 返回了空响应")
        try:
            payload = response.json()
        except ValueError:
            raise DeepSeekError("DeepSeek 返回了无效 JSON") from None
        return self._parse_text(payload)

    @staticmethod
    def _parse_text(payload) -> str:
        if not isinstance(payload, dict) or "error" in payload:
            raise DeepSeekError("DeepSeek 返回的响应格式无效或包含 API 错误")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise DeepSeekError("DeepSeek 未返回生成结果")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise DeepSeekError("DeepSeek 返回的生成结果格式无效")
        finish_reason = choice.get("finish_reason")
        if finish_reason == "length":
            raise DeepSeekError("DeepSeek 输出达到长度上限，生成内容不完整")
        if finish_reason is not None and finish_reason != "stop":
            raise DeepSeekError("DeepSeek 未正常完成文字生成，请检查内容或稍后重试")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise DeepSeekError("DeepSeek 返回的消息格式无效")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise DeepSeekError("DeepSeek 未返回有效的文字内容")
        return content.strip()


def generate_text(system_prompt: str, user_prompt: str, *, model: str | None = None) -> str:
    """单次调用并自动关闭 HTTP 客户端；连续调用可复用 DeepSeekClient。"""
    with DeepSeekClient(**({"model": model} if model is not None else {})) as client:
        return client.generate_text(system_prompt, user_prompt)
