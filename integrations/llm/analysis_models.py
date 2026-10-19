"""案例分析模型注册表与通义千问聊天适配，不执行数据库操作。"""
from dataclasses import dataclass, field
import json

import httpx

import config
from pydantic import SecretStr
from . import deepseek, compatible
from services.exceptions import InvalidInputError, ServiceUnavailableError


@dataclass(frozen=True)
class AnalysisModel:
    id: str
    provider: str
    model: str
    base_url: str | None = None
    api_key: SecretStr | None = field(default=None, repr=False, compare=False)

    def snapshot(self) -> dict:
        return {"id": self.id, "provider": self.provider, "model": self.model, "base_url": self.base_url}

    @property
    def ready(self) -> bool:
        if self.base_url:
            return self.api_key is not None and bool(self.api_key.get_secret_value())
        key = config.DEEPSEEK_API_KEY if self.provider == "deepseek" else config.DASHSCOPE_API_KEY
        return isinstance(key, str) and bool(key.strip())


def model_profiles() -> list[AnalysisModel]:
    profiles = [AnalysisModel("deepseek", "deepseek", config.MODEL_DEEPSEEK)]
    if config.QWEN_CHAT_MODEL:
        profiles.append(AnalysisModel("qwen", "qwen", config.QWEN_CHAT_MODEL))
    if config.ANALYSIS_MODELS_JSON:
        try:
            items = json.loads(config.ANALYSIS_MODELS_JSON)
            if not isinstance(items, list) or not 1 <= len(items) <= 20:
                raise ValueError
            profiles = []
            for item in items:
                if not isinstance(item, dict) or set(item) != {"id", "provider", "model"}:
                    raise ValueError
                if any(not isinstance(v, str) or not v.strip() or len(v) > 100 for v in item.values()):
                    raise ValueError
                if item["provider"] not in {"deepseek", "qwen"}:
                    raise ValueError
                if item["id"].strip().startswith("user:"):
                    raise ValueError
                if item["model"].strip().startswith("text-embedding-"):
                    raise ValueError
                profiles.append(AnalysisModel(**{k: v.strip() for k, v in item.items()}))
            if len({p.id for p in profiles}) != len(profiles):
                raise ValueError
        except (ValueError, TypeError):
            raise ServiceUnavailableError("ANALYSIS_MODELS_JSON 配置无效，请检查模型白名单") from None
    if any(not isinstance(p.model, str) or not p.model.strip() or len(p.model) > 100
           or p.model.startswith("text-embedding-") for p in profiles):
        raise ServiceUnavailableError("分析模型必须配置聊天模型，不能使用 Embedding 模型")
    return profiles


def select_model(model_id: str | None) -> AnalysisModel:
    profiles = model_profiles()
    profile = next((p for p in profiles if p.id == model_id), None) if model_id else profiles[0]
    if profile is None:
        raise InvalidInputError("分析模型未配置，请从可用分析模型列表选择")
    return profile


def generate_text(profile: AnalysisModel, *, system_prompt: str, user_prompt: str) -> str:
    if profile.base_url:
        if not profile.ready:
            raise ServiceUnavailableError("请在模型设置中保存 API Key")
        return compatible.generate_text(endpoint=profile.base_url, api_key=profile.api_key.get_secret_value(),
            model=profile.model, system_prompt=system_prompt, user_prompt=user_prompt)
    if profile.provider == "deepseek":
        options = {} if profile.model == config.MODEL_DEEPSEEK else {"model": profile.model}
        return deepseek.generate_text(system_prompt=system_prompt, user_prompt=user_prompt, **options)
    if not profile.ready:
        raise ServiceUnavailableError("请在 .env 中配置 DASHSCOPE_API_KEY")
    try:
        base_url = deepseek.DeepSeekClient._base_url(config.QWEN_CHAT_BASE_URL)
    except config.ConfigurationError:
        raise ServiceUnavailableError("QWEN_CHAT_BASE_URL 必须是有效的 HTTPS 兼容接口根地址") from None
    try:
        with httpx.Client(follow_redirects=False) as client:
            response = client.post(f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {config.DASHSCOPE_API_KEY.strip()}"},
                json={"model": profile.model, "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ], "stream": False, "enable_thinking": False},
                timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=False)
    except httpx.TimeoutException:
        raise ServiceUnavailableError("通义千问分析请求超时，请稍后重试") from None
    except httpx.RequestError:
        raise ServiceUnavailableError("通义千问分析网络连接失败，请稍后重试") from None
    if not 200 <= response.status_code < 300:
        raise ServiceUnavailableError(f"通义千问分析请求失败（HTTP {response.status_code}）")
    try:
        return deepseek.DeepSeekClient._parse_text(response.json())
    except (ValueError, deepseek.DeepSeekError):
        raise ServiceUnavailableError("通义千问未返回完整有效的文字响应") from None
