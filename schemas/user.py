"""用户结构与注册协议保持一致，实际接口在 routers.auth。"""
from .auth import RegisterRequest, RegisteredUser


class UserCreate(RegisterRequest):
    pass


class UserRead(RegisteredUser):
    pass
