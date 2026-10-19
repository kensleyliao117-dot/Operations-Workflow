"""经验库的用户隔离查询和事务边界。"""
from contextlib import contextmanager
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from services.common import current_user_id
from services.exceptions import ConflictError, NotFoundError


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def owned(db, memory_db, model):
    return select(model).where(model.user_id == current_user_id(db))


def get_owned(db, memory_db, model, record_id, *, lock=False):
    query = owned(db, memory_db, model).where(model.id == record_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    value = memory_db.scalar(query)
    if value is None:
        raise NotFoundError("经验库记录不存在")
    return value


@contextmanager
def memory_transaction(db):
    try:
        yield
        db.flush()
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ConflictError("经验记录发生并发冲突，请查询已有结果或使用同一请求键重试") from None
    except Exception:
        db.rollback()
        raise
