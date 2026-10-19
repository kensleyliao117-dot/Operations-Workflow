"""仅供业务层校验当前用户；注册和登录见 services.auth。"""
from models.user import Users
from .common import get_record


def get_user(db, user_id):
    return get_record(db, Users, user_id)
