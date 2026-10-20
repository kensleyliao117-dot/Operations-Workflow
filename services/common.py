"""业务模块共用的查询、归属校验和事务处理。"""
from contextlib import contextmanager
import logging

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from .exceptions import AuthenticationError, ConflictError, NotFoundError
from .validation import validate_id, validate_pagination

logger = logging.getLogger(__name__)


def current_user_id(db) -> int:
    user_id = db.info.get("user_id")
    if user_id is None:
        raise AuthenticationError("请先登录")
    return user_id


def owned_query(db, model):
    """私有数据的归属条件；列表、按 ID 查询均使用。"""
    from models.task import Task
    from models.product import Product
    from models.rednote import RednoteCase

    user_id = current_user_id(db)
    statement = select(model)
    if hasattr(model, "is_deleted"):
        statement = statement.where(model.is_deleted.is_(False))
    table = model.__tablename__
    if table in {"tasks", "products", "prompts", "custom_rules", "rednote_cases", "document_chunks",
                 "platform_accounts", "publish_metrics", "rednote_analysis_feedback", "content_rule_checks", "memory_sync_events", "user_analysis_models", "user_resource_models"}:
        return statement.where(model.user_id == user_id)
    if table in {"contents", "reviews", "publish_records"}:
        return statement.where(model.task_id.in_(select(Task.id).where(Task.user_id == user_id, Task.is_deleted.is_(False))))
    if table == "product_documents":
        return statement.where(model.product_id.in_(select(Product.id).where(Product.user_id == user_id)))
    if table == "rednote_analysis":
        return statement.where(model.cases_id.in_(select(RednoteCase.id).where(
            RednoteCase.user_id == user_id, RednoteCase.is_deleted.is_(False))))
    if table == "platform_rules":
        from models.user import Users
        overrides = db.scalar(select(Users.platform_rule_overrides).where(Users.id == user_id)) or {}
        excluded = [int(key) for key, value in overrides.items() if key.isdigit() and value.get("is_deleted")]
        return statement.where(or_(model.user_id == user_id, model.user_id.is_(None)), model.id.not_in(excluded))
    if table == "users":
        return statement.where(model.id == user_id)
    raise RuntimeError(f"未定义 {table} 的数据隔离规则")


@contextmanager
def write_transaction(db):
    """每个对外写操作使用一次；内部业务函数不单独提交。"""
    try:
        yield
        db.flush()
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # 仅记录约束名称，避免 SQL、参数和连接凭据进入日志。
        diag = getattr(exc.orig, "diag", None)
        logger.warning("Database constraint conflict: %s", getattr(diag, "constraint_name", None) or "unknown")
        raise ConflictError("数据与数据库约束冲突，请检查重复值和关联记录") from exc
    except Exception:
        db.rollback()
        raise


def get_record(db, model, record_id, *, lock=False):
    validate_id(record_id)
    statement = owned_query(db, model).where(model.id == record_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    record = db.scalar(statement)
    if record is None:
        raise NotFoundError(f"{model.__tablename__} 中的记录 {record_id} 不存在")
    return record


def list_records(db, model, *, offset=0, limit=20, **filters):
    validate_pagination(offset, limit)
    statement = owned_query(db, model)
    for name, value in filters.items():
        if value is not None:
            statement = statement.where(getattr(model, name) == value)
    return list(db.scalars(statement.order_by(model.id.desc()).offset(offset).limit(limit)))


def remove_record(db, model, record_id):
    """从当前用户的资源列表移除，保留历史工作流与内容引用。"""
    with write_transaction(db):
        record = get_record(db, model, record_id, lock=True)
        record.is_deleted = True
