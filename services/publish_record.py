"""登记发布尝试、审核版本和真实结果，实际执行由发布 Service 负责。"""
from datetime import datetime, timezone
import re
from urllib.parse import urlsplit

from sqlalchemy import select

from models.publish_record import PublishRecord, PublishMetrics
from models.review import Review
from models.task import Task
from schemas.publish_record import PublishRequest, PublishResult
from .common import current_user_id, get_record, list_records, write_transaction
from .platform_account import require_account
from .exceptions import ConflictError
from .review import require_latest_content


def _require_approval(db, task, content_id):
    require_latest_content(db, task.id, content_id)
    review = db.scalar(select(Review).where(
        Review.task_id == task.id, Review.content_id == content_id,
    ).order_by(Review.id.desc()).limit(1))
    if review is None or review.status != "approved":
        raise ConflictError("该内容没有有效的人工审核通过记录")
    return review


def _request_replay(db, data):
    if not data.idempotency_key:
        return None
    existing = db.scalar(select(PublishRecord).where(PublishRecord.task_id == data.task_id,
        PublishRecord.idempotency_key == data.idempotency_key))
    if existing is not None and (existing.content_id != data.content_id or existing.account_id != data.account_id
            or existing.platform != data.platform or existing.publisher_backend != data.publisher_backend
            or existing.visibility != data.visibility):
        raise ConflictError("同一 idempotency_key 不能用于不同的发布内容或账号")
    return existing


def _prepare_record(db, task, data):
    review = _require_approval(db, task, data.content_id)
    account = require_account(db, data.account_id, data.platform)
    content = require_latest_content(db, task.id, data.content_id)
    record = PublishRecord(**data.model_dump(), publish_status="publishing", content_version=content.version,
                           review_id=review.id, delivery_phase="prepared")
    if data.publisher_backend == "chrome":
        if data.platform != "rednote":
            raise ConflictError("Chrome 发布目前只支持 rednote")
        from .rules import require_review_ready
        from .publishing_snapshot import snapshot
        require_review_ready(db, task, content, required=True)
        record.content_snapshot, record.content_fingerprint, _ = snapshot(db, content, account, visibility=data.visibility)
    db.add(record)
    task.status = "publishing"
    return record


def create_publish_record(db, data: PublishRequest) -> PublishRecord:
    with write_transaction(db):
        task = get_record(db, Task, data.task_id, lock=True)
        replay = _request_replay(db, data)
        if replay is not None:
            return replay
        if task.status != "approved":
            raise ConflictError("只有审核通过的任务可以开始发布")
        if task.platform_name and task.platform_name != data.platform:
            raise ConflictError("发布平台与任务平台不一致")
        existing = db.scalar(select(PublishRecord.id).where(
            PublishRecord.task_id == task.id,
            PublishRecord.publish_status.in_(["publishing", "published", "unknown"]),
        ).limit(1))
        if existing is not None:
            raise ConflictError("任务已有进行中或成功的发布记录，不能重复发布")
        record = _prepare_record(db, task, data)
    return record


def record_publish_result(db, record_id: int, data: PublishResult, *, _from_executor=False) -> PublishRecord:
    record = get_record(db, PublishRecord, record_id)
    if record.publisher_backend == "chrome" and not _from_executor:
        from integrations.publishers.locking import publishing_lock
        db.rollback()
        with publishing_lock(db):
            return _record_result(db, record_id, data)
    return _record_result(db, record_id, data, from_executor=_from_executor)


def _rednote_post(data):
    post_id = data.platform_post_id
    post_url = str(data.platform_post_url) if data.platform_post_url else None
    if post_url:
        parsed = urlsplit(post_url)
        match = re.fullmatch(r"/(?:explore|discovery/item)/([0-9a-f]{24})/?", parsed.path)
        if (parsed.scheme != "https" or parsed.hostname not in {"www.xiaohongshu.com", "xiaohongshu.com"}
                or parsed.username is not None or parsed.password is not None or parsed.port not in {None, 443} or match is None):
            raise ConflictError("请提供小红书完整作品详情链接")
        if post_id and post_id != match.group(1):
            raise ConflictError("作品 ID 与作品链接不一致")
        post_id = match.group(1)
    if not isinstance(post_id, str) or re.fullmatch(r"[0-9a-f]{24}", post_id) is None:
        raise ConflictError("请提供有效的小红书作品 ID 或完整作品链接")
    return post_id, post_url or f"https://www.xiaohongshu.com/explore/{post_id}"


def _record_result(db, record_id: int, data: PublishResult, *, from_executor=False) -> PublishRecord:
    post_url = str(data.platform_post_url) if data.platform_post_url is not None else None
    with write_transaction(db):
        record = get_record(db, PublishRecord, record_id)
        task = get_record(db, Task, record.task_id, lock=True)
        record = get_record(db, PublishRecord, record_id, lock=True)
        require_account(db, record.account_id, record.platform, active=False)
        if data.account_id is not None and data.account_id != record.account_id:
            raise ConflictError("实际发布账号与发布记录绑定账号不一致")
        post_id = data.platform_post_id
        if record.publisher_backend == "chrome":
            if not from_executor and data.account_id != record.account_id:
                raise ConflictError("核实 Chrome 发布结果时需明确确认绑定的 account_id")
            if data.publish_status == "published":
                post_id, post_url = _rednote_post(data)
        if record.publish_status in {"published", "failed"}:
            details = {"final_content": data.final_content.model_dump(exclude_none=True) if data.final_content else {},
                       "external_materials_note": data.external_materials_note}
            if (data.final_content is not None or data.external_materials_note is not None) and record.publication_details != details:
                raise ConflictError("不能覆盖已登记的最终文案或补充素材说明")
            if data.published_at is not None and data.published_at.replace(tzinfo=None) != record.published_at:
                raise ConflictError("不能覆盖已经登记的实际发布时间")
            if (record.publish_status != data.publish_status or
                    record.platform_post_id != post_id or
                    record.error_message != data.error_message):
                raise ConflictError("不能覆盖已经登记的发布结果")
            if post_url is not None:
                if record.platform_post_url is not None and record.platform_post_url != post_url:
                    raise ConflictError("不能覆盖已有的作品链接")
                # 允许为旧发布记录补充原本缺失的作品链接。
                record.platform_post_url = post_url
            return record
        if (record.publisher_backend == "chrome" and not from_executor and data.publish_status == "failed"
                and record.delivery_phase != "prepared" and not data.confirm_not_published):
            raise ConflictError("发布可能已经提交，需核实平台确实未发布并传 confirm_not_published=true")
        if record.publish_status not in {"publishing", "unknown"} or task.status not in {"publishing", "failed"}:
            raise ConflictError("任务和发布记录必须处于发布中")
        record.publish_status = data.publish_status
        record.platform_post_id = post_id
        record.platform_post_url = post_url
        record.error_message = data.error_message
        record.delivery_phase = "awaiting_confirmation" if data.publish_status == "unknown" else "finished"
        if data.publish_status == "published":
            if data.final_content is not None or data.external_materials_note is not None:
                record.publication_details = {"final_content": data.final_content.model_dump(exclude_none=True) if data.final_content else {},
                    "external_materials_note": data.external_materials_note}
            # 当前 Model 使用无时区 DateTime，约定按 UTC 保存。
            record.published_at = (data.published_at or datetime.now(timezone.utc)).replace(tzinfo=None)
            task.status = "completed"
            task.error_message = None
            task.failed_from_status = None
            from memory.services.publications import queue_sync
            queue_sync(db, record)
        elif data.publish_status == "unknown":
            task.status = "publishing"
            task.error_message = data.error_message
            task.checkpoint = {**(task.checkpoint or {}), "publish_record_id": record.id,
                               "content_id": record.content_id, "step": "publish_confirmation"}
        else:
            task.failed_from_status = "publishing"
            task.error_message = data.error_message
            task.checkpoint = {**(task.checkpoint or {}), "publish_record_id": record.id,
                               "content_id": record.content_id, "step": "publish"}
            task.status = "failed"
    if data.publish_status == "published":
        from memory.services.publications import try_sync_queued
        try_sync_queued(db, record_id)
    return record


def retry_publish_record(db, record_id: int, *, idempotency_key=None) -> PublishRecord:
    """开始新的发布尝试，保留旧失败记录。远程结果不确定时应先核实平台。"""
    with write_transaction(db):
        old = get_record(db, PublishRecord, record_id)
        task = get_record(db, Task, old.task_id, lock=True)
        old = get_record(db, PublishRecord, record_id, lock=True)
        if idempotency_key is not None and idempotency_key == old.idempotency_key:
            raise ConflictError("新发布尝试需使用新的 idempotency_key，不能复用旧尝试的键")
        request = PublishRequest(task_id=old.task_id, content_id=old.content_id, platform=old.platform,
            account_id=old.account_id, publisher_backend=old.publisher_backend,
            visibility=old.visibility,
            idempotency_key=idempotency_key) if old.account_id is not None and (old.publisher_backend != "chrome" or idempotency_key) else None
        if request is not None:
            replay = _request_replay(db, request)
            if replay is not None:
                return replay
        if old.publisher_backend == "chrome" and not idempotency_key:
            raise ConflictError("重试 Chrome 发布需提供新的 idempotency_key")
        if old.publish_status != "failed":
            raise ConflictError("只能重试失败的发布记录")
        if task.status not in {"failed", "publishing"} or task.failed_from_status != "publishing":
            raise ConflictError("任务不处于可恢复的发布阶段")
        _require_approval(db, task, old.content_id)
        require_account(db, old.account_id, old.platform)
        latest_id = db.scalar(select(PublishRecord.id).where(
            PublishRecord.task_id == task.id).order_by(PublishRecord.id.desc()).limit(1))
        if latest_id != old.id:
            raise ConflictError("只能重试最近一次发布失败，已有后续尝试")
        record = _prepare_record(db, task, request)
        if old.publisher_backend == "chrome" and record.content_fingerprint != old.content_fingerprint:
            raise ConflictError("重试素材与原审核版本快照不一致，请保存新版本并重新人工审核")
    return record


def get_publish_record(db, record_id):
    return get_record(db, PublishRecord, record_id)


def list_publish_records(db, *, task_id=None, content_id=None, offset=0, limit=20):
    return list_records(db, PublishRecord, task_id=task_id, content_id=content_id,
                        offset=offset, limit=limit)


def save_metrics(db, record_id, data):
    with write_transaction(db):
        record = get_record(db, PublishRecord, record_id, lock=True)
        if record.publish_status != "published":
            raise ConflictError("只有发布成功的作品可以登记运营反馈")
        require_account(db, record.account_id, record.platform, active=False)
        metrics = PublishMetrics(user_id=current_user_id(db), publish_record_id=record.id,
            account_id=record.account_id, metrics=data.metrics.model_dump(exclude_none=True), source="manual")
        db.add(metrics)
    return metrics


def list_metrics(db, record_id, *, offset=0, limit=20):
    get_record(db, PublishRecord, record_id)
    return list_records(db, PublishMetrics, publish_record_id=record_id, offset=offset, limit=limit)
