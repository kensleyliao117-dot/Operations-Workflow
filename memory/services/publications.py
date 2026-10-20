"""用持久事件把发布结果幂等同步到独立经验库。"""
from datetime import datetime, timezone
import logging

from sqlalchemy import select, or_
from sqlalchemy.exc import SQLAlchemyError

from models.content import Content
from models.memory_sync import MemorySyncEvent
from models.product import Product
from models.publish_record import PublishRecord
from models.task import Task
from memory.models import PublishedPost
from services.common import current_user_id, get_record, write_transaction
from services.common import owned_query
from services.validation import validate_pagination
from services.exceptions import ConflictError, InvalidInputError, ServiceError, NotFoundError
from services.platform_account import require_account
from .common import fingerprint, memory_transaction, owned


logger = logging.getLogger(__name__)


def queue_sync(db, record):
    event = db.scalar(select(MemorySyncEvent).where(MemorySyncEvent.publish_record_id == record.id))
    if event is None:
        event = MemorySyncEvent(user_id=current_user_id(db), publish_record_id=record.id, status="pending")
        db.add(event)
    return event


def publication_source(db, record_id):
    record = get_record(db, PublishRecord, record_id)
    if record.feedback_deleted:
        raise NotFoundError('该作品的数据反馈已删除')
    if record.publish_status != "published":
        raise ConflictError("只有发布成功的作品可以进入运营经验库")
    # 发布记录已校验所属用户；历史归档保留软删除任务的产品与版本关联。
    task = record.task
    if not task.product_id:
        raise InvalidInputError("发布任务未关联产品，不能积累该产品的运营经验")
    product = get_record(db, Product, task.product_id)
    content = get_record(db, Content, record.content_id)
    if content.task_id != task.id or (record.content_version is not None and record.content_version != content.version):
        raise ConflictError("发布记录与内容版本不一致")
    account = require_account(db, record.account_id, record.platform, active=False)
    snapshot = {"product_name": product.name, "title": content.title, "body": content.body,
                "tags": content.tags or [], "image_prompt": content.image_prompt,
                "image_count": len(content.image_assets or []), "visibility": record.visibility}
    if record.publication_details:
        snapshot.update(record.publication_details.get("final_content") or {})
        snapshot["external_materials_note"] = record.publication_details.get("external_materials_note")
        snapshot["image_count_scope"] = "仅统计工作流内保存的图片；平台补充图片未核实"
    values = {"user_id": current_user_id(db), "product_id": product.id, "task_id": task.id,
              "content_id": content.id, "content_version": content.version, "publish_record_id": record.id,
              "account_id": account.id, "platform": record.platform, "platform_user_id": account.platform_user_id,
              "platform_post_id": record.platform_post_id, "platform_post_url": record.platform_post_url,
              "published_at": record.published_at, "content_snapshot": snapshot}
    # 链接补全不改变文案版本指纹，旧镜像允许补全缺失的定位信息。
    immutable = {k: v for k, v in values.items() if k not in {"platform_post_url", "platform_post_id", "published_at"}}
    immutable["content_snapshot"] = {k: v for k, v in snapshot.items() if k != "product_name"}
    values["source_fingerprint"] = fingerprint(immutable)
    return values


def sync_publication(db, memory_db, record_id, *, restore_deleted=False):
    with write_transaction(db):
        record = get_record(db, PublishRecord, record_id, lock=True)
        if restore_deleted and record.feedback_deleted:
            event = db.scalar(select(MemorySyncEvent).where(MemorySyncEvent.publish_record_id == record_id))
            if event is not None and event.status == 'deleting':
                raise ConflictError('反馈删除尚未完成，请先重试删除')
            record.feedback_deleted = False
        values = publication_source(db, record_id)
        event = queue_sync(db, record)
        event.attempts = (event.attempts or 0) + 1
        db.flush()
        event_id = event.id
    try:
        # 归档写入期间持有业务行锁，避免删除后又被在途同步重建。
        get_record(db, Task, values['task_id'], lock=True)
        get_record(db, PublishRecord, record_id, lock=True)
        publication_source(db, record_id)
        with memory_transaction(memory_db):
            post = memory_db.scalar(owned(db, memory_db, PublishedPost).where(
                PublishedPost.publish_record_id == record_id).with_for_update())
            if post is None:
                post = PublishedPost(**values)
                memory_db.add(post)
            else:
                if post.source_fingerprint != values["source_fingerprint"]:
                    raise ConflictError("经验库作品快照与发布版本不一致，拒绝覆盖历史")
                for name in ("platform_post_id", "platform_post_url"):
                    old, new = getattr(post, name), values[name]
                    if old and new and old != new:
                        raise ConflictError("已归档作品标识发生变化，拒绝覆盖历史")
                    if not old:
                        setattr(post, name, new)
                if post.published_at is None and values["published_at"] is not None:
                    post.published_at = values["published_at"]
            memory_db.flush()
        with write_transaction(db):
            event = get_record(db, MemorySyncEvent, event_id, lock=True)
            event.status, event.error_message = "synced", None
            event.synced_at = datetime.now(timezone.utc).replace(tzinfo=None)
        return post
    except Exception as exc:
        memory_db.rollback()
        db.rollback()
        try:
            with write_transaction(db):
                event = db.scalar(owned_query(db, MemorySyncEvent).where(MemorySyncEvent.id == event_id).with_for_update())
                source = db.get(PublishRecord, record_id)
                parent = db.get(Task, source.task_id) if source is not None else None
                if event is not None and source is not None and parent is not None and not source.feedback_deleted and not parent.is_deleted:
                    event.status = "failed"
                    event.error_message = str(exc) if isinstance(exc, ServiceError) else "经验库同步失败，发布结果已保留，可重新同步"
        except SQLAlchemyError:
            db.rollback()
            logger.error("Cannot record memory sync failure")
        raise


def try_sync_queued(db, record_id):
    factory = db.info.get("memory_session_factory")
    if factory is None:
        return
    try:
        with factory() as memory_db:
            sync_publication(db, memory_db, record_id)
    except Exception as exc:
        db.rollback()
        logger.warning("Publication saved; memory sync pending: %s", type(exc).__name__)


def find_publication(db, memory_db, record_id):
    # 业务库是归属与发布状态的权威来源，不能仅凭经验库旧镜像授权。
    source = publication_source(db, record_id)
    post = memory_db.scalar(owned(db, memory_db, PublishedPost).where(PublishedPost.publish_record_id == record_id))
    if post is None or (post.published_at is None and source["published_at"] is not None):
        return sync_publication(db, memory_db, record_id)
    return post


def list_for_feedback(db, *, platform=None, product_id=None, offset=0, limit=20):
    validate_pagination(offset, limit)
    if product_id is not None:
        get_record(db, Product, product_id)
    query = owned_query(db, PublishRecord).add_columns(Product.id, Product.name, Content.title).join(
        Task, Task.id == PublishRecord.task_id).join(Product, Product.id == Task.product_id).join(
        Content, Content.id == PublishRecord.content_id).where(
        PublishRecord.publish_status == "published", or_(PublishRecord.feedback_deleted.is_(False),
            select(MemorySyncEvent.id).where(MemorySyncEvent.publish_record_id == PublishRecord.id,
                MemorySyncEvent.status == 'deleting').exists()),
        Product.user_id == current_user_id(db), Content.task_id == Task.id)
    if platform is not None:
        query = query.where(PublishRecord.platform == platform)
    if product_id is not None:
        query = query.where(Product.id == product_id)
    rows = db.execute(query.order_by(PublishRecord.id.desc()).offset(offset).limit(limit)).all()
    return [{**{name: getattr(record, name) for name in (
        "id", "task_id", "content_id", "content_version", "platform", "account_id", "visibility",
        "publish_status", "published_at", "platform_post_id", "platform_post_url")},
        "product_id": pid, "product_name": name, "title": title}
        for record, pid, name, title in rows]
