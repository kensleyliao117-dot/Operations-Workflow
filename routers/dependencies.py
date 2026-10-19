from typing import Annotated
from fastapi import Depends, Request
from ipaddress import ip_address
from urllib.parse import urlsplit
from config import CORS_ORIGINS
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from database import get_db
from models.user import Users
from services.auth import authenticate
from services.exceptions import AuthenticationError, PermissionDeniedError

bearer = HTTPBearer(auto_error=False)


def get_current_user(db: Annotated[Session, Depends(get_db)],
                     credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> Users:
    if credentials is None:
        raise AuthenticationError("请先登录")
    return authenticate(db, credentials.credentials)


CurrentUser = Annotated[Users, Depends(get_current_user)]


def _loopback(host: str | None) -> bool:
    try:
        address = ip_address(host or "")
        return address.is_loopback or bool(getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback)
    except ValueError:
        return False


def require_local_collection(request: Request):
    """本机采集入口拒绝远程来源和转发请求，不信任客户端提交的代理头。"""
    headers = request.headers
    forwarded = any(key == "forwarded" or key == "x-real-ip" or key.startswith("x-forwarded-") for key in headers)
    host = request.url.hostname
    if (request.client is None or not _loopback(request.client.host) or forwarded
            or not (host == "localhost" or _loopback(host))):
        raise PermissionDeniedError("采集和自动分析接口仅允许本机直接访问")
    origin = headers.get("origin")
    if origin is not None:
        try:
            parsed = urlsplit(origin)
        except ValueError:
            raise PermissionDeniedError("网页来源地址无效") from None
        if origin not in CORS_ORIGINS or not (parsed.hostname == "localhost" or _loopback(parsed.hostname)):
            raise PermissionDeniedError("不允许该网页来源调用本地采集或自动分析接口")


def require_local_publishing(request: Request):
    try:
        require_local_collection(request)
    except PermissionDeniedError:
        raise PermissionDeniedError("本地发布及结果核实仅允许本机直接访问，不接受代理转发或未许可的网页来源") from None
