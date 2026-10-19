"""用户指定的 Chat Completions 兼容接口；限制出站地址并固定 DNS 解析结果。"""
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

from .deepseek import DeepSeekClient, DeepSeekError
from services.exceptions import InvalidInputError, ServiceUnavailableError


def base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    try:
        url = urlsplit(value)
        hostname = url.hostname or ""
        valid = (url.scheme == "https" and bool(hostname) and url.username is None
                 and url.password is None and not url.query and not url.fragment
                 and not any(c.isspace() or ord(c) < 32 for c in value)
                 and "\\" not in value and not url.path.endswith("/chat/completions"))
        url.port
        if hostname.casefold().rstrip(".") in {"localhost", "localhost.localdomain"}:
            valid = False
        try:
            valid = valid and public_ip(ipaddress.ip_address(hostname))
        except ValueError:
            pass
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("API URL 需为公网 HTTPS 根地址，不含密钥、查询参数或 /chat/completions 后缀")
    return value


def public_ip(address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return public_ip(address.ipv4_mapped)
    if isinstance(address, ipaddress.IPv6Address) and (address.sixtofour or address.teredo or address.is_site_local):
        return False
    return address.is_global and not address.is_multicast and not address.is_reserved


class PublicHTTPSTransport(httpx.BaseTransport):
    """校验所有解析地址，连接固定 IP，并保留原始 Host、TLS SNI 及证书校验。"""
    def __init__(self):
        self._transport = httpx.HTTPTransport(trust_env=False, retries=0)

    def handle_request(self, request):
        if request.url.scheme != "https":
            raise InvalidInputError("模型接口仅支持公网 HTTPS 地址")
        try:
            addresses = socket.getaddrinfo(request.url.host, request.url.port or 443,
                                           type=socket.SOCK_STREAM)
            ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
        except (OSError, ValueError):
            raise ServiceUnavailableError("模型接口域名解析失败，请检查 API URL") from None
        if not ips or any(not public_ip(address) for address in ips):
            raise InvalidInputError("模型接口不能指向本机、内网或保留网络地址")
        pinned = httpx.Request(request.method, request.url.copy_with(host=str(ips[0])),
            headers=request.headers, stream=request.stream,
            extensions={**request.extensions, "sni_hostname": request.url.host})
        return self._transport.handle_request(pinned)

    def close(self):
        self._transport.close()


def generate_text(*, endpoint: str, api_key: str, model: str,
                  system_prompt: str, user_prompt: str) -> str:
    for value in (system_prompt, user_prompt):
        if not isinstance(value, str) or not value.strip():
            raise InvalidInputError("模型输入必须是非空文本")
    try:
        endpoint = base_url(endpoint)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from None
    try:
        with httpx.Client(transport=PublicHTTPSTransport(), trust_env=False,
                          follow_redirects=False, timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            with client.stream("POST", f"{endpoint}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}], "stream": False}) as response:
                if not 200 <= response.status_code < 300:
                    reasons = {401: "API Key 认证失败", 403: "无访问权限", 404: "接口或模型不存在",
                               429: "调用过于频繁或额度不足"}
                    reason = reasons.get(response.status_code, "调用失败，请检查接口地址和模型 ID")
                    raise ServiceUnavailableError(f"模型接口{reason}（HTTP {response.status_code}）")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 2 * 1024 * 1024:
                        raise ServiceUnavailableError("模型响应过大，请减少输出长度")
                    chunks.append(chunk)
                content = b"".join(chunks)
    except httpx.TimeoutException:
        raise ServiceUnavailableError("模型请求超时，请稍后重试") from None
    except httpx.HTTPError:
        raise ServiceUnavailableError("模型连接失败，请检查接口地址和网络") from None
    try:
        import json
        return DeepSeekClient._parse_text(json.loads(content))
    except (ValueError, DeepSeekError):
        raise ServiceUnavailableError("模型未返回完整有效的文字，请确认支持 Chat Completions 协议") from None
