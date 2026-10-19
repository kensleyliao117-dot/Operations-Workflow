"""共享 Chrome 发布会话的跨进程互斥，不持有业务事务等待浏览器。"""
from contextlib import contextmanager
from threading import Lock

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from services.exceptions import ConflictError, ServiceUnavailableError


CHROME_LOCK_ID = 1942703001
_test_lock = Lock()


@contextmanager
def publishing_lock(db):
    engine = db.get_bind()
    if engine.dialect.name != "postgresql":
        # SQLite 仅用于隔离测试，生产环境使用 PostgreSQL 会话级锁。
        if not _test_lock.acquire(blocking=False):
            raise ConflictError("Chrome 发布会话正在使用，请稍后重试")
        try:
            yield
        finally:
            _test_lock.release()
        return
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": CHROME_LOCK_ID})
            if not acquired:
                raise ConflictError("Chrome 发布会话正在使用，请稍后重试")
            try:
                yield
            finally:
                # 连接归还池前释放会话锁，避免连接池遗留锁。
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": CHROME_LOCK_ID})
    except SQLAlchemyError:
        raise ServiceUnavailableError("发布互斥存储暂时不可用，请先查询已有发布记录") from None
