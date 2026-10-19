"""合并系统模型与当前用户的模型，保留分析版本中的模型快照。"""
from sqlalchemy import func, select, update
from pydantic import SecretStr

from integrations.llm import analysis_models as adapters
from models.analysis_model import UserAnalysisModel
from models.user import Users
from .common import current_user_id, get_record, list_records, owned_query, write_transaction
from .exceptions import ConflictError, InvalidInputError
from .model_secrets import decrypt_key, encrypt_key


def _read(profile, *, name=None, config_id=None, is_active=True, is_default=False, has_key=False):
    return {"id": profile.id, "provider": profile.provider, "model": profile.model,
            "ready": is_active and (has_key if profile.base_url else profile.ready), "name": name or profile.model,
            "source": "user" if config_id else "system", "config_id": config_id,
            "is_active": is_active, "is_default": is_default, "base_url": profile.base_url,
            "credential_source": "user" if profile.base_url else "system"}


def _profile(row, *, credentials=False):
    key = (SecretStr(decrypt_key(row.encrypted_api_key, row.user_id))
           if credentials and row.encrypted_api_key else None)
    return adapters.AnalysisModel(f"user:{row.id}", row.provider, row.model, row.base_url, key)


def _row_read(row):
    return _read(_profile(row), name=row.name, config_id=row.id, is_active=row.is_active,
                 is_default=row.is_default, has_key=bool(row.encrypted_api_key))


def _system_models(db):
    user = get_record(db, Users, current_user_id(db))
    hidden = set(user.hidden_system_models or [])
    return [profile for profile in adapters.model_profiles() if profile.id not in hidden]


def list_models(db):
    profiles = [_read(profile) for profile in _system_models(db)]
    rows = list_records(db, UserAnalysisModel, limit=100)
    return profiles + [_row_read(row) for row in rows]


def select_model(db, model_id):
    if not model_id:
        default = db.scalar(owned_query(db, UserAnalysisModel).where(
            UserAnalysisModel.is_default.is_(True), UserAnalysisModel.is_active.is_(True)))
        if default is not None:
            return _profile(default, credentials=True)
    if model_id and model_id.startswith("user:"):
        try:
            record_id = int(model_id.removeprefix("user:"))
        except ValueError:
            raise InvalidInputError("分析模型标识无效") from None
        row = get_record(db, UserAnalysisModel, record_id)
        if not row.is_active:
            raise InvalidInputError("该分析模型已停用，请选择其他模型")
        return _profile(row, credentials=True)
    profiles = _system_models(db)
    profile = next((value for value in profiles if value.id == model_id), None) if model_id else next(iter(profiles), None)
    if profile is None:
        raise InvalidInputError("模型已移除或未配置，请在模型连接中添加并选择可用模型")
    return profile


def save_model(db, data, record_id=None):
    with write_transaction(db):
        # 按用户串行处理配置写入，避免并发请求绕过数量上限。
        db.scalar(select(Users).where(Users.id == current_user_id(db)).with_for_update())
        values = data.model_dump(exclude={"api_key"})
        if values["is_default"] and not values["is_active"]:
            raise InvalidInputError("默认模型必须保持启用")
        if record_id is None:
            count = db.scalar(select(func.count()).select_from(
                owned_query(db, UserAnalysisModel).subquery()))
            if count >= 20:
                raise ConflictError("最多保存 20 个自定义分析模型，可编辑或删除已有配置")
            row = UserAnalysisModel(user_id=current_user_id(db))
        else:
            row = get_record(db, UserAnalysisModel, record_id, lock=True)
        if not values["base_url"] and (data.api_key is not None or values["provider"] not in {"deepseek", "qwen"}):
            raise InvalidInputError("自定义供应商需要填写 API URL 和 API Key")
        if row.base_url and not values["base_url"]:
            raise InvalidInputError("已有独立凭据的配置不能切换为系统凭据，请填写 API URL")
        if row.base_url and row.base_url != values["base_url"] and data.api_key is None:
            raise InvalidInputError("更换 API URL 时必须重新填写 API Key")
        if values["base_url"] and not (data.api_key is not None or row.encrypted_api_key):
            raise InvalidInputError("请填写模型 API Key")
        if data.api_key is not None:
            row.encrypted_api_key = encrypt_key(data.api_key.get_secret_value(), current_user_id(db))
        if values["is_default"]:
            statement = update(UserAnalysisModel).where(UserAnalysisModel.user_id == current_user_id(db))
            if record_id is not None:
                statement = statement.where(UserAnalysisModel.id != record_id)
            db.execute(statement.values(is_default=False))
            db.flush()
        for key, value in values.items():
            setattr(row, key, value)
        db.add(row)
        db.flush()
        result = _row_read(row)
    return result


def delete_model(db, record_id):
    with write_transaction(db):
        get_record(db, Users, current_user_id(db), lock=True)
        db.delete(get_record(db, UserAnalysisModel, record_id, lock=True))


def remove_system_model(db, model_id):
    if model_id not in {profile.id for profile in adapters.model_profiles()}:
        raise InvalidInputError("系统模型不存在")
    with write_transaction(db):
        user = get_record(db, Users, current_user_id(db), lock=True)
        user.hidden_system_models = sorted(set(user.hidden_system_models or []) | {model_id})


def restore_system_models(db):
    with write_transaction(db):
        user = get_record(db, Users, current_user_id(db), lock=True)
        user.hidden_system_models = []


def test_connection(db, record_id):
    row = get_record(db, UserAnalysisModel, record_id)
    profile = select_model(db, f"user:{row.id}")
    db.rollback()
    adapters.generate_text(profile, system_prompt="Reply with OK only.", user_prompt="Connection test.")
    return {"ok": True, "model": profile.model}

