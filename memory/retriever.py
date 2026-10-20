"""按用户、产品、平台检索最新有效经验，首版使用有界词项相关度排序。"""
import json
import logging
import re

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from pydantic import ValidationError

from memory.models import OptimizationMemory, PublishedPost
from models.publish_record import PublishRecord
from models.task import Task
from memory.schemas import MemoryReference
from models.product import Product
from services.common import current_user_id, get_record
from services.exceptions import InvalidInputError, ServiceError
from services.platform_account import require_account


logger = logging.getLogger(__name__)


def _tokens(value):
    words = set(re.findall(r"[a-z0-9_]+", value.lower()))
    for text in re.findall(r"[\u4e00-\u9fff]+", value):
        words.update(text[i:i + 2] for i in range(max(1, len(text) - 1)))
    return words


def retrieve(db, memory_db, product_id, query, *, platform="rednote", limit=5, account_id=None):
    get_record(db, Product, product_id)
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 2000 or type(limit) is not int or not 1 <= limit <= 5:
        raise InvalidInputError("经验检索需提供非空查询，长度不超过2000字符，limit 在1到5之间")
    user_id = current_user_id(db)
    if account_id is not None:
        require_account(db, account_id, platform, active=False)
    latest = select(OptimizationMemory.post_id, func.max(OptimizationMemory.version).label("version")).where(
        OptimizationMemory.user_id == user_id, OptimizationMemory.product_id == product_id,
        OptimizationMemory.platform == platform).group_by(OptimizationMemory.post_id).subquery()
    statement = select(OptimizationMemory).join(latest,
        (OptimizationMemory.post_id == latest.c.post_id) & (OptimizationMemory.version == latest.c.version)).where(
        OptimizationMemory.user_id == user_id, OptimizationMemory.product_id == product_id,
        OptimizationMemory.platform == platform, OptimizationMemory.is_active.is_(True))
    if account_id is not None:
        statement = statement.where(OptimizationMemory.account_id == account_id)
    candidates = memory_db.scalars(statement.order_by(OptimizationMemory.id.desc()).limit(200)).all()
    posts = dict(memory_db.execute(select(PublishedPost.id, PublishedPost.publish_record_id).where(
        PublishedPost.user_id == user_id, PublishedPost.id.in_([row.post_id for row in candidates]))).all())
    active_records = set(db.scalars(select(PublishRecord.id).join(Task, Task.id == PublishRecord.task_id).where(
        PublishRecord.id.in_(list(posts.values())), PublishRecord.feedback_deleted.is_(False),
        Task.user_id == user_id, Task.is_deleted.is_(False))))
    candidates = [row for row in candidates if posts.get(row.post_id) in active_records]
    tokens = _tokens(query)
    ranked = []
    for item in candidates:
        try:
            value = MemoryReference.model_validate(item)
        except ValidationError:
            continue
        words = _tokens(json.dumps(value.result.model_dump(), ensure_ascii=False))
        overlap = len(tokens & words)
        if tokens and not overlap:
            continue
        ranked.append((overlap / max(1, len(tokens | words)), item.id, value))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    result, size = [], 0
    for _, _, value in ranked:
        length = len(value.model_dump_json())
        if size + length <= 12000:
            result.append(value)
            size += length
        if len(result) == limit:
            break
    return result


def for_generation(db, product_id, query, *, platform="rednote", limit=5, enabled=True):
    if not enabled:
        return [], "disabled"
    factory = db.info.get("memory_session_factory")
    if factory is None:
        return [], "unavailable"
    try:
        with factory() as memory_db:
            values = retrieve(db, memory_db, product_id, query, platform=platform, limit=limit)
            return values, "used" if values else "no_matches"
    except (SQLAlchemyError, ServiceError) as exc:
        logger.warning("Content generation continues without memory: %s", type(exc).__name__)
        db.rollback()
        return [], "unavailable"
