"""按任务归属清理业务、经验、检查点和私有图片；删除过程可重试。"""
import re
from contextlib import nullcontext

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

import config
from models.content import Content
from models.memory_sync import MemorySyncEvent
from models.publish_record import PublishRecord, PublishMetrics
from models.review import Review
from models.rules import ContentRuleCheck
from models.task import Task
from models.workflow import TaskWorkflow
from services.common import current_user_id, write_transaction
from services.exceptions import ConflictError, NotFoundError, ServiceUnavailableError
from services.validation import validate_id
from workflows import persistence


def _task(db, task_id):
    validate_id(task_id)
    task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == current_user_id(db))
                     .with_for_update().execution_options(populate_existing=True))
    if task is None:
        raise NotFoundError('任务不存在')
    return task


def _remove_images(db, task):
    assets = db.scalars(select(Content.image_assets).where(Content.task_id == task.id))
    keys = {item.get('storage_key') for values in assets for item in (values or []) if isinstance(item, dict)}
    others = select(Task.id).where(Task.user_id == task.user_id, Task.product_id == task.product_id, Task.id != task.id)
    shared = {item.get('storage_key') for values in db.scalars(select(Content.image_assets).where(
        Content.task_id.in_(others))) for item in (values or []) if isinstance(item, dict)}
    root = config.GENERATED_IMAGE_DIR.resolve()
    directory = root / f'user_{task.user_id}' / f'product_{task.product_id}'
    prefix = f'generated_images/user_{task.user_id}/product_{task.product_id}/'
    for key in keys - shared:
        if not isinstance(key, str) or not key.startswith(prefix):
            continue
        filename = key[len(prefix):]
        if re.fullmatch(r'[0-9a-f]{32}\.png', filename) is None:
            continue
        path = directory / filename
        if directory.resolve() != directory or path.resolve().parent != directory:
            raise ServiceUnavailableError('图片路径检查失败，任务删除未完成，可修复路径后重试')
        try:
            path.unlink(missing_ok=True)
        except OSError:
            raise ServiceUnavailableError('图片文件暂时无法删除，请关闭占用后重试删除任务') from None


def delete_task_data(db, task_id):
    from services.image_generation import generation_in_progress
    from memory.services.deletion import delete_posts

    # 先阻止新增业务，清理中断时保留任务及删除标记供再次操作。
    with write_transaction(db):
        task = _task(db, task_id)
        run = db.get(TaskWorkflow, task_id)
        uncertain = db.scalar(select(PublishRecord.id).where(PublishRecord.task_id == task_id,
            PublishRecord.publish_status.in_(['publishing', 'unknown'])).limit(1))
        if (task.status in {'running', 'publishing'} or (run is not None and run.status == 'running')
                or generation_in_progress(task)
                or (task.checkpoint or {}).get('content_generation', {}).get('status') == 'running'
                or uncertain is not None):
            raise ConflictError('任务正在生成或发布，或发布结果待核实，请处理完成后再删除')
        task.is_deleted = True
        task.checkpoint = {**(task.checkpoint or {}), 'deletion': {'status': 'pending'}}
        thread_id = f'user:{task.user_id}:task:{task_id}:run:{run.run_id}' if run else None

    context = persistence.open_checkpointer(task_id, exclusive=True) if thread_id else nullcontext(None)
    with context as saver:
        with write_transaction(db):
            task = _task(db, task_id)
            records = list(db.scalars(select(PublishRecord.id).where(PublishRecord.task_id == task_id)))
            factory = db.info.get('memory_session_factory')
            if records and factory is None:
                raise ServiceUnavailableError('经验数据库连接未配置，任务删除未完成，请配置后重试')
            if factory is not None:
                try:
                    with factory() as memory_db:
                        delete_posts(memory_db, task.user_id, task_id=task_id)
                except SQLAlchemyError:
                    raise ServiceUnavailableError('经验数据库暂不可用，任务删除未完成，请稍后重试') from None
            _remove_images(db, task)
            if saver is not None:
                saver.delete_thread(thread_id)
            for model, condition in (
                (PublishMetrics, PublishMetrics.publish_record_id.in_(records)),
                (MemorySyncEvent, MemorySyncEvent.publish_record_id.in_(records)),
                (PublishRecord, PublishRecord.task_id == task_id),
                (Review, Review.task_id == task_id),
                (ContentRuleCheck, ContentRuleCheck.task_id == task_id),
                (TaskWorkflow, TaskWorkflow.task_id == task_id),
                (Content, Content.task_id == task_id),
                (Task, Task.id == task_id),
            ):
                db.execute(delete(model).where(condition).execution_options(synchronize_session=False))
