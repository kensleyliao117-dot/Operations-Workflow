"""API Key 使用独立部署密钥加密，密文绑定所属用户。"""
import json
import os
import threading

from cryptography.fernet import Fernet, InvalidToken

import config
from .exceptions import ServiceUnavailableError

_key_lock = threading.Lock()


def _cipher(*, create=False):
    try:
        key = config.MODEL_CREDENTIALS_KEY
        if not key:
            path = config.STORAGE_DIR / "secrets" / "model-credentials.key"
            with _key_lock:
                if not path.exists() and create:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    except FileExistsError:
                        pass
                    else:
                        with os.fdopen(descriptor, "wb") as handle:
                            handle.write(Fernet.generate_key())
                key = path.read_bytes()
        return Fernet(key)
    except (OSError, ValueError, TypeError):
        raise ServiceUnavailableError("模型凭据加密密钥不可用，请恢复部署密钥，或由管理员检查密钥配置") from None


def encrypt_key(value: str, user_id: int) -> str:
    payload = json.dumps({"user_id": user_id, "api_key": value}).encode()
    return _cipher(create=True).encrypt(payload).decode("ascii")


def decrypt_key(value: str, user_id: int) -> str:
    try:
        payload = json.loads(_cipher().decrypt(value.encode("ascii")))
        if payload["user_id"] != user_id or not isinstance(payload["api_key"], str):
            raise ValueError
        return payload["api_key"]
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise ServiceUnavailableError("模型 API Key 无法解密，请恢复原部署密钥或重新保存 API Key") from None
