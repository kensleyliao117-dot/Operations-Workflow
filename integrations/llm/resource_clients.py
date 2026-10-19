"""账号配置的 Embedding 和同步生图适配；出站请求复用公网地址校验。"""
import base64
from contextlib import nullcontext
import json
from urllib.parse import urlsplit
import httpx

from .compatible import PublicHTTPSTransport
from .image import ImageResult, MAX_IMAGE_BYTES, QwenImageError, validate_png, validate_image_url
from rag.embeddings import QwenEmbeddings
from services.exceptions import InvalidInputError, ServiceUnavailableError


def request_json(url, api_key, payload, *, timeout=120, max_bytes=8 * 1024 * 1024, client=None):
    try:
        manager = nullcontext(client) if client is not None else httpx.Client(
            transport=PublicHTTPSTransport(), trust_env=False, follow_redirects=False)
        with manager as connection:
            with connection.stream('POST', url, headers={'Authorization': f'Bearer {api_key}'},
                json=payload, timeout=httpx.Timeout(timeout, connect=10), follow_redirects=False) as response:
                if not 200 <= response.status_code < 300:
                    raise ServiceUnavailableError(f'模型接口请求失败（HTTP {response.status_code}），请检查接口、密钥及模型参数')
                data = bytearray()
                for part in response.iter_bytes():
                    data.extend(part)
                    if len(data) > max_bytes:
                        raise ServiceUnavailableError('模型接口响应超过大小限制')
        result = json.loads(data)
        if not isinstance(result, dict) or result.get('error') or result.get('code'):
            raise ValueError('invalid response')
        return result
    except httpx.TimeoutException:
        raise ServiceUnavailableError('模型请求超时，未自动重试，请确认调用结果后重试') from None
    except httpx.HTTPError:
        raise ServiceUnavailableError('模型连接失败，请检查接口地址和网络') from None
    except (ValueError, TypeError):
        raise ServiceUnavailableError('模型接口未返回有效结果') from None


class ResourceEmbeddings(QwenEmbeddings):
    """复用片段验证、用量和向量解析，独立设置账号的接口与向量空间。"""
    def __init__(self, profile, *, http_client=None):
        self.profile = profile
        self.model = profile.model
        self.dimensions = profile.dimensions
        self.embedding_space = profile.embedding_space
        self.batch_size = 10
        self._token_counter = None
        self.last_usage = None
        self._http_client = http_client

    def close(self):
        pass

    def _request(self, texts):
        payload = {'model': self.model, 'input': texts, 'encoding_format': 'float'}
        if self.profile.send_dimensions:
            payload['dimensions'] = self.dimensions
        return request_json(self.profile.base_url + '/embeddings', self.profile.api_key.get_secret_value(),
                            payload, client=self._http_client)


class ResourceImageClient:
    """支持 Images 兼容接口和千问同步接口，不对付费生图请求自动重试。"""
    def __init__(self, profile, *, http_client=None):
        self.profile = profile
        self.model = profile.model
        self._http_client = http_client
        self._inline = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._inline.clear()

    def generate(self, prompt, negative_prompt, *, count=1, size='1024*1024'):
        if size not in self.profile.image_sizes or type(count) is not int or not 1 <= count <= 4:
            raise InvalidInputError('图片尺寸或数量与当前生图连接不匹配，请调整生成参数')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 600:
            raise InvalidInputError('图片 Prompt 无效')
        if not isinstance(negative_prompt, str) or len(negative_prompt) > 500:
            raise InvalidInputError('图片负面 Prompt 无效')
        if self.profile.protocol == 'qwen_image':
            endpoint = self.profile.base_url + '/api/v1/services/aigc/multimodal-generation/generation'
            payload = {'model': self.model, 'input': {'messages': [{'role': 'user', 'content': [{'text': prompt}]}]},
                'parameters': {'n': count, 'size': size, 'negative_prompt': negative_prompt,
                               'prompt_extend': False, 'watermark': True}}
        else:
            endpoint = self.profile.base_url + '/images/generations'
            payload = {'model': self.model, 'prompt': prompt + '\nAvoid: ' + negative_prompt,
                       'n': count, 'size': size.replace('*', 'x')}
        result = request_json(endpoint, self.profile.api_key.get_secret_value(), payload,
                              timeout=180, max_bytes=32 * 1024 * 1024, client=self._http_client)
        try:
            if self.profile.protocol == 'qwen_image':
                choices = result['output']['choices']
                if any(choice.get('finish_reason') not in {None, 'stop'} for choice in choices):
                    raise ValueError('incomplete images')
                items = [{'url': validate_image_url(item['image'])} for choice in choices
                         for item in choice['message']['content'] if 'image' in item]
            else:
                items = result['data']
            if not isinstance(items, list) or len(items) != count:
                raise ValueError('incorrect image count')
            self._inline.clear()
            urls = []
            for index, item in enumerate(items):
                if item.get('b64_json'):
                    value = item['b64_json']
                    if not isinstance(value, str) or len(value) > MAX_IMAGE_BYTES * 4 // 3 + 4:
                        raise ValueError('image too large')
                    url = f'inline:{index}'
                    self._inline[url] = value
                else:
                    url = self._image_url(item['url'])
                urls.append(url)
            if len(set(urls)) != count:
                raise ValueError('duplicate images')
            request_id = result.get('request_id')
            if request_id is not None and (not isinstance(request_id, str) or len(request_id) > 200):
                raise ValueError('invalid request id')
            return ImageResult(self.model, request_id, tuple(urls))
        except (KeyError, ValueError, TypeError, AttributeError):
            raise QwenImageError('生图接口未返回完整有效的图片结果，请确认接口协议') from None

    @staticmethod
    def _image_url(value):
        if not isinstance(value, str) or len(value) > 8192:
            raise ValueError('invalid image url')
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.fragment or parsed.port not in {None, 443}):
            raise ValueError('invalid image url')
        return value

    def download(self, url, *, size):
        try:
            if url in self._inline:
                data = base64.b64decode(self._inline.pop(url), validate=True)
            else:
                self._image_url(url)
                # 图片地址可能是独立存储服务，不携带模型接口的 Authorization。
                with httpx.Client(transport=PublicHTTPSTransport(), trust_env=False, follow_redirects=False) as client:
                    with client.stream('GET', url, timeout=60, follow_redirects=False) as response:
                        if response.status_code != 200:
                            raise ValueError('image download failed')
                        parts = bytearray()
                        for part in response.iter_bytes():
                            parts.extend(part)
                            if len(parts) > MAX_IMAGE_BYTES:
                                raise ValueError('image too large')
                        data = bytes(parts)
            if validate_png(data) != tuple(map(int, size.split('*'))):
                raise ValueError('incorrect image dimensions')
            return data
        except (ValueError, httpx.HTTPError):
            raise QwenImageError('生成图片下载失败、格式无效或尺寸不匹配，原文案已保留') from None
