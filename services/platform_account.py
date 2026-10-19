"""平台账号登记，发布和反馈使用固定账号关联。"""
from models.platform_account import PlatformAccount
from .common import current_user_id, get_record, list_records, owned_query, write_transaction
from .exceptions import ConflictError


def create_account(db, data):
    def existing_account():
        return db.scalar(owned_query(db, PlatformAccount).where(
            PlatformAccount.platform == data.platform,
            PlatformAccount.platform_user_id == data.platform_user_id))

    duplicate_message = "该平台账号已登记，请直接在发布账号列表中选择，无需重复登记"
    try:
        with write_transaction(db):
            if existing_account() is not None:
                raise ConflictError(duplicate_message)
            account = PlatformAccount(**data.model_dump(), user_id=current_user_id(db))
            db.add(account)
    except ConflictError as exc:
        # 并发登记仍由唯一约束保证；回滚后仅检查当前用户的账号。
        if existing_account() is not None:
            raise ConflictError(duplicate_message) from exc
        raise
    return account


def require_account(db, account_id, platform, *, active=True):
    if account_id is None:
        raise ConflictError("发布记录缺少实际平台账号，不能发布、重试或登记反馈")
    account = get_record(db, PlatformAccount, account_id)
    if account.platform != platform or (active and not account.is_active):
        raise ConflictError("平台账号不可用或与发布平台不一致")
    return account


def list_accounts(db, *, offset=0, limit=20):
    return list_records(db, PlatformAccount, offset=offset, limit=limit)
