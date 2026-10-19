"""维护账号独立的向量和图片连接，模型选择不修改共享环境变量。"""
from dataclasses import dataclass, field
import hashlib
import json
from sqlalchemy import func, select, update
from pydantic import SecretStr
from models.resource_model import UserResourceModel
from models.user import Users
from .common import current_user_id, get_record, owned_query, write_transaction
from .exceptions import ConflictError, InvalidInputError
from .model_secrets import decrypt_key, encrypt_key


@dataclass(frozen=True)
class ResourceProfile:
    id: int
    kind: str
    provider: str
    model: str
    protocol: str
    base_url: str
    dimensions: int | None
    send_dimensions: bool
    image_sizes: tuple[str, ...]
    api_key: SecretStr = field(repr=False)

    @property
    def embedding_space(self):
        value = [self.protocol, self.base_url, self.model, self.dimensions]
        return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()

    def snapshot(self):
        return {key: getattr(self, key) for key in ('id', 'kind', 'provider', 'model', 'protocol', 'base_url', 'dimensions')}


def _read(row):
    names = ('id', 'kind', 'name', 'provider', 'model', 'protocol', 'base_url', 'dimensions',
             'send_dimensions', 'image_sizes', 'is_active', 'is_default')
    return {**{key: getattr(row, key) for key in names}, 'ready': row.is_active and bool(row.encrypted_api_key)}


def list_models(db, kind=None):
    statement = owned_query(db, UserResourceModel)
    if kind:
        statement = statement.where(UserResourceModel.kind == kind)
    return [_read(row) for row in db.scalars(statement.order_by(UserResourceModel.id))]


def save_model(db, data, record_id=None):
    with write_transaction(db):
        get_record(db, Users, current_user_id(db), lock=True)
        if record_id is None:
            count = db.scalar(select(func.count()).select_from(owned_query(db, UserResourceModel).subquery()))
            if count >= 40:
                raise ConflictError('最多保存 40 个向量和生图模型连接')
            row = UserResourceModel(user_id=current_user_id(db))
        else:
            row = get_record(db, UserResourceModel, record_id, lock=True)
            if row.kind != data.kind:
                raise InvalidInputError('不能修改已有连接的模型类型，请新建连接')
        if (not row.encrypted_api_key or row.base_url != data.base_url) and data.api_key is None:
            raise InvalidInputError('新建连接或更换 API URL 时必须填写 API Key')
        if data.api_key is not None:
            row.encrypted_api_key = encrypt_key(data.api_key.get_secret_value(), current_user_id(db))
        if data.is_default:
            statement = update(UserResourceModel).where(UserResourceModel.user_id == current_user_id(db), UserResourceModel.kind == data.kind)
            if record_id is not None:
                statement = statement.where(UserResourceModel.id != record_id)
            db.execute(statement.values(is_default=False))
            db.flush()
        for key, value in data.model_dump(exclude={'api_key'}).items():
            setattr(row, key, value)
        db.add(row)
        db.flush()
        result = _read(row)
    return result


def delete_model(db, record_id):
    with write_transaction(db):
        get_record(db, Users, current_user_id(db), lock=True)
        db.delete(get_record(db, UserResourceModel, record_id, lock=True))


def select_profile(db, kind, record_id=None):
    row = get_record(db, UserResourceModel, record_id) if record_id else db.scalar(
        owned_query(db, UserResourceModel).where(UserResourceModel.kind == kind,
            UserResourceModel.is_default.is_(True)))
    if row is None:
        return None
    if row.kind != kind or not row.is_active:
        raise InvalidInputError('模型类型不匹配或连接已停用，请重新选择')
    return ResourceProfile(row.id, row.kind, row.provider, row.model, row.protocol, row.base_url,
        row.dimensions, row.send_dimensions, tuple(row.image_sizes),
        SecretStr(decrypt_key(row.encrypted_api_key, current_user_id(db))))


def test_embedding(db, record_id):
    from integrations.llm.resource_clients import ResourceEmbeddings
    profile = select_profile(db, 'embedding', record_id)
    db.rollback()
    with ResourceEmbeddings(profile) as client:
        client.embed_query('Connection test')
    return {'ok': True, 'model': profile.model, 'message': 'Embedding 接口与向量维度验证成功'}
