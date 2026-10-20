"""删除观测及其派生经验，整篇反馈删除可重复执行。"""
from sqlalchemy import delete, select

from memory.models import PublishedPost, PostMetrics, Feedback, OptimizationMemory
from services.common import current_user_id
from services.exceptions import NotFoundError, ConflictError, ServiceUnavailableError
from sqlalchemy.exc import SQLAlchemyError
from .common import get_owned, memory_transaction, owned


def delete_posts(memory_db, user_id, *, record_ids=None, task_id=None):
    query = select(PublishedPost).where(PublishedPost.user_id == user_id)
    if task_id is not None:
        query = query.where(PublishedPost.task_id == task_id)
    else:
        query = query.where(PublishedPost.publish_record_id.in_(record_ids or []))
    with memory_transaction(memory_db):
        ids = [row.id for row in memory_db.scalars(query.with_for_update())]
        for model in (OptimizationMemory, Feedback, PostMetrics, PublishedPost):
            column = model.id if model is PublishedPost else model.post_id
            memory_db.execute(delete(model).where(model.user_id == user_id, column.in_(ids)))


def delete_metric(db, memory_db, record_id, metric_id):
    from .publications import find_publication
    post = find_publication(db, memory_db, record_id)
    with memory_transaction(memory_db):
        get_owned(db, memory_db, PublishedPost, post.id, lock=True)
        metric = get_owned(db, memory_db, PostMetrics, metric_id, lock=True)
        if metric.post_id != post.id:
            raise NotFoundError('该作品的指标不存在')
        feedback_ids = [row.id for row in memory_db.scalars(owned(db, memory_db, Feedback).where(
            Feedback.post_id == post.id)) if any(
                item.get('id') == metric_id for item in (row.evidence_snapshot or {}).get('metrics_snapshots', []))]
        for model in (OptimizationMemory, Feedback):
            column = model.feedback_id if model is OptimizationMemory else model.id
            memory_db.execute(delete(model).where(model.user_id == current_user_id(db), column.in_(feedback_ids)))
        memory_db.delete(metric)


def delete_experience(db, memory_db, memory_id):
    previous = get_owned(db, memory_db, OptimizationMemory, memory_id)
    with memory_transaction(memory_db):
        get_owned(db, memory_db, PublishedPost, previous.post_id, lock=True)
        record = get_owned(db, memory_db, OptimizationMemory, memory_id, lock=True)
        feedback_id = record.feedback_id
        memory_db.delete(record)
        memory_db.flush()
        memory_db.execute(delete(Feedback).where(Feedback.id == feedback_id, Feedback.user_id == current_user_id(db)))


def delete_publication_feedback(db, memory_db, record_id):
    from models.publish_record import PublishRecord
    from models.memory_sync import MemorySyncEvent
    from services.common import get_record, write_transaction
    from .publications import queue_sync

    with write_transaction(db):
        record = get_record(db, PublishRecord, record_id, lock=True)
        if record.publish_status != 'published':
            raise ConflictError('只有已发布作品有可删除的数据反馈')
        record.feedback_deleted = True
        event = queue_sync(db, record)
        event.status, event.error_message = 'deleting', None
    try:
        delete_posts(memory_db, current_user_id(db), record_ids=[record_id])
    except SQLAlchemyError:
        raise ServiceUnavailableError('经验数据库暂不可用，反馈删除未完成，请稍后重试') from None
    with write_transaction(db):
        event = db.scalar(select(MemorySyncEvent).where(MemorySyncEvent.publish_record_id == record_id,
            MemorySyncEvent.user_id == current_user_id(db)).with_for_update())
        event.status, event.error_message = 'deleted', None
