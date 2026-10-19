"""向量、生图连接配置；密钥只写入，不回显。"""
from typing import Annotated, Literal
from pydantic import Field, StringConstraints, field_validator, model_validator
from .analysis_model import AnalysisModelWrite
from .base import Name100, ResponseSchema

IMAGE_SIZES = {'1728*2368', '2048*2048', '1536*2688', '1024*1024', '1024*1536',
               '1536*1024', '1024*1792', '1792*1024'}
ImageSize = Literal['1728*2368', '2048*2048', '1536*2688', '1024*1024', '1024*1536',
                    '1536*1024', '1024*1792', '1792*1024']


class ResourceModelWrite(AnalysisModelWrite):
    kind: Literal['embedding', 'image']
    protocol: Literal['compatible', 'qwen_image'] = 'compatible'
    base_url: Annotated[str, StringConstraints(max_length=2048)]
    dimensions: int | None = Field(default=None, ge=1, le=4096)
    send_dimensions: bool = True
    image_sizes: list[str] = Field(default_factory=list, max_length=8)

    @field_validator('model')
    @classmethod
    def chat_model_only(cls, value):
        return value

    @model_validator(mode='after')
    def capability_options(self):
        if self.is_default and not self.is_active:
            raise ValueError('默认模型必须启用')
        if self.kind == 'embedding':
            if self.protocol != 'compatible' or self.dimensions is None or self.image_sizes:
                raise ValueError('Embedding 需要兼容协议和向量维度，不接受图片尺寸')
        else:
            if self.dimensions is not None:
                raise ValueError('生图连接不接受向量维度')
            if not self.image_sizes or any(size not in IMAGE_SIZES for size in self.image_sizes):
                raise ValueError('请填写模型支持的图片尺寸')
            if self.protocol == 'qwen_image' and any(size not in {'1728*2368', '2048*2048', '1536*2688'} for size in self.image_sizes):
                raise ValueError('千问同步接口请选择支持的千问图片尺寸')
        if any(self.base_url.endswith(suffix) for suffix in ('/embeddings', '/images/generations', '/generation')):
            raise ValueError('填写接口根地址，不包含具体调用路径')
        return self


class ResourceModelRead(ResponseSchema):
    id: int
    kind: Literal['embedding', 'image']
    name: str
    provider: str
    model: str
    protocol: str
    base_url: str
    dimensions: int | None
    send_dimensions: bool
    image_sizes: list[str]
    is_active: bool
    is_default: bool
    ready: bool
