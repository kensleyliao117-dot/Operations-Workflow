"""JWT 签名、Argon2 密码哈希和可撤销的持久会话。"""
from datetime import datetime, timedelta, timezone
import hashlib
import secrets

import jwt
from pwdlib import PasswordHash
from sqlalchemy import select

from config import ACCESS_TOKEN_SECONDS, JWT_AUDIENCE, JWT_ISSUER, require_jwt_secret
from models.auth import LoginSession
from models.user import Users
from schemas.auth import LoginRequest, RegisterRequest
from .common import write_transaction
from .exceptions import AuthenticationError, ConflictError

password_hasher = PasswordHash.recommended()
dummy_password_hash = password_hasher.hash(secrets.token_urlsafe(32))


def register(db, data: RegisterRequest) -> Users:
    username = data.username.casefold()
    with write_transaction(db):
        if db.scalar(select(Users.id).where(Users.username == username)) is not None:
            raise ConflictError("用户名或邮箱已被使用")
        if data.email and db.scalar(select(Users.id).where(Users.email == data.email)) is not None:
            raise ConflictError("用户名或邮箱已被使用")
        user = Users(username=username, email=data.email,
                     password_hash=password_hasher.hash(data.password))
        db.add(user)
    return user


def login(db, data: LoginRequest) -> dict:
    key = require_jwt_secret()
    user = db.scalar(select(Users).where(Users.username == data.username.casefold()))
    valid = password_hasher.verify(data.password, user.password_hash if user and user.password_hash else dummy_password_hash)
    if not valid or user is None or not user.password_hash or not user.is_active:
        raise AuthenticationError("用户名或密码错误")
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=ACCESS_TOKEN_SECONDS)
    jti = secrets.token_urlsafe(32)
    token = jwt.encode({"sub": str(user.id), "jti": jti, "iat": now, "exp": expires,
                        "iss": JWT_ISSUER, "aud": JWT_AUDIENCE, "type": "access"}, key, algorithm="HS256")
    with write_transaction(db):
        db.add(LoginSession(id=hashlib.sha256(jti.encode()).hexdigest(), user_id=user.id, expires_at=expires))
    return {"access_token": token, "token_type": "bearer", "expires_in": ACCESS_TOKEN_SECONDS}


def authenticate(db, token: str) -> Users:
    try:
        claims = jwt.decode(token, require_jwt_secret(), algorithms=["HS256"],
                            issuer=JWT_ISSUER, audience=JWT_AUDIENCE,
                            options={"require": ["sub", "jti", "iat", "exp", "iss", "aud", "type"]})
        if claims["type"] != "access" or not isinstance(claims["jti"], str):
            raise ValueError("invalid claims")
        user_id = int(claims["sub"])
        session_id = hashlib.sha256(claims["jti"].encode()).hexdigest()
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise AuthenticationError("登录凭证无效或已过期") from None
    session = db.get(LoginSession, session_id)
    now = datetime.now(timezone.utc)
    expires_at = None if session is None else session.expires_at
    if expires_at is not None:
        expires_at = (expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None
                      else expires_at.astimezone(timezone.utc))
    if (session is None or session.user_id != user_id or session.revoked_at is not None
            or expires_at <= now):
        raise AuthenticationError("登录凭证无效或已退出")
    user = db.get(Users, user_id)
    if user is None or not user.is_active or not user.username or not user.password_hash:
        raise AuthenticationError("账户不可用")
    db.info.update(user_id=user.id, session_id=session.id, is_admin=user.is_admin)
    return user


def logout(db):
    with write_transaction(db):
        session = db.get(LoginSession, db.info["session_id"])
        session.revoked_at = datetime.now(timezone.utc)
