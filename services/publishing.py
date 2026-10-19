"""执行已登记且审核通过的发布尝试；不自动重试外部发布副作用。"""
from datetime import datetime, timezone
import logging

from sqlalchemy.exc import SQLAlchemyError

from integrations.publishers.base import PublishOutcome
from integrations.publishers.locking import publishing_lock
from integrations.publishers.rednote import RednotePublisher
from models.publish_record import PublishRecord
from models.task import Task
from schemas.publish_record import PublishResult
from . import publish_record as records
from .common import get_record, write_transaction
from .exceptions import ConflictError, ServiceError
from .platform_account import require_account
from .publishing_snapshot import snapshot
from .review import require_latest_content
from .rules import require_review_ready


logger = logging.getLogger(__name__)


def _validated_payload(db, record, task):
    if task.status != "publishing" or record.publish_status != "publishing":
        raise ConflictError("任务或发布记录状态已变化")
    if record.platform != "rednote" or record.publisher_backend != "chrome":
        raise ConflictError("该记录不属于小红书 Chrome 发布")
    review = records._require_approval(db, task, record.content_id)
    content = require_latest_content(db, task.id, record.content_id)
    account = require_account(db, record.account_id, record.platform)
    require_review_ready(db, task, content, required=True)
    _, fingerprint, payload = snapshot(db, content, account, visibility=record.visibility)
    if record.review_id != review.id or record.content_version != content.version or record.content_fingerprint != fingerprint:
        raise ConflictError("审核记录、文案版本、账号或图片文件已变化，发布已停止")
    return payload


def _before_submit(db, record_id):
    with write_transaction(db):
        initial = get_record(db, PublishRecord, record_id)
        task = get_record(db, Task, initial.task_id, lock=True)
        record = get_record(db, PublishRecord, record_id, lock=True)
        if record.delivery_phase != "executing":
            raise ConflictError("该发布尝试已提交或已中断，不能再次提交")
        _validated_payload(db, record, task)
        record.delivery_phase = "submitting"
        record.submitted_at = datetime.now(timezone.utc).replace(tzinfo=None)


def execute_publish(db, record_id):
    # 先检查归属，未经授权的调用不能占用本地浏览器会话。
    record = get_record(db, PublishRecord, record_id)
    if record.publisher_backend != "chrome":
        raise ConflictError("人工发布记录不能执行 Chrome 自动发布，请创建 publisher_backend=chrome 的记录")
    if record.publish_status in {"published", "failed", "unknown"}:
        return record
    db.rollback()
    with publishing_lock(db):
        try:
            with write_transaction(db):
                record = get_record(db, PublishRecord, record_id)
                task = get_record(db, Task, record.task_id, lock=True)
                record = get_record(db, PublishRecord, record_id, lock=True)
                if record.publish_status in {"published", "failed", "unknown"}:
                    return record
                if record.delivery_phase != "prepared":
                    # 锁已释放但执行标记仍存在，意味着旧进程中断，不重放发布。
                    outcome = PublishOutcome("unknown", error="上一执行进程已中断，请核实创作中心后登记结果，禁止直接重发")
                    payload = None
                else:
                    payload = _validated_payload(db, record, task)
                    record.delivery_phase = "executing"
                    record.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        except ServiceError as exc:
            db.rollback()
            record = get_record(db, PublishRecord, record_id)
            return records.record_publish_result(db, record_id, PublishResult(publish_status="failed",
                account_id=record.account_id, error_message=str(exc)), _from_executor=True)
        db.rollback()
        if payload is not None:
            try:
                outcome = RednotePublisher().publish(payload, before_submit=lambda: _before_submit(db, record_id))
                if not isinstance(outcome, PublishOutcome):
                    raise ValueError("invalid publisher result")
                if outcome.status == "published" and get_record(db, PublishRecord, record_id).delivery_phase != "submitting":
                    raise ValueError("publisher skipped submission guard")
            except Exception as exc:
                db.rollback()
                current = get_record(db, PublishRecord, record_id)
                status = "unknown" if current.delivery_phase == "submitting" else "failed"
                error = ("提交后结果不明，请先核实创作中心，禁止直接重发" if status == "unknown" else
                         str(exc) if isinstance(exc, ServiceError) else "发布准备失败，请核对浏览器、素材和账号后重试")
                outcome = PublishOutcome(status, error=error)
        data = PublishResult(publish_status=outcome.status, platform_post_id=outcome.post_id,
            platform_post_url=outcome.post_url, error_message=outcome.error,
            account_id=get_record(db, PublishRecord, record_id).account_id)
        try:
            return records.record_publish_result(db, record_id, data, _from_executor=True)
        except SQLAlchemyError:
            # 提交前已保存阶段标记，数据库恢复后查询/执行会转为待确认，不再次点击发布。
            db.rollback()
            logger.error("Cannot save browser publishing outcome; reconcile existing record before retrying")
            raise
