from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.task import Task
from schemas.task import TaskCreate, TaskStatus, TaskStatusUpdate, TaskCheckpointSave, TaskFailureSave
from .exceptions import ConflictError, InvalidInputError, InvalidTransitionError, NotFoundError
from .user import get_user
from .validation import validate_id, validate_pagination
from .common import current_user_id, get_record, owned_query
from .common import write_transaction
from models.product import Product


# 没有内容的基础任务允许直接完成，内容任务必须经过审核和发布流程。
ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.pending: {TaskStatus.running, TaskStatus.cancelled},
    TaskStatus.running: {
        TaskStatus.pending_review, TaskStatus.completed,
        TaskStatus.failed, TaskStatus.cancelled,
    },
    TaskStatus.pending_review: {
        TaskStatus.approved, TaskStatus.rejected, TaskStatus.cancelled,
    },
    TaskStatus.rejected: {TaskStatus.running, TaskStatus.cancelled},
    TaskStatus.approved: {TaskStatus.publishing, TaskStatus.cancelled},
    TaskStatus.publishing: {TaskStatus.completed, TaskStatus.failed},
    # 重试通过 resume_task，按失败前的阶段恢复。
    TaskStatus.failed: {TaskStatus.cancelled},
    TaskStatus.completed: set(),
    TaskStatus.cancelled: set(),
}


def get_task(db: Session, task_id: int) -> Task:
    validate_id(task_id)
    task = db.scalar(owned_query(db, Task).where(Task.id == task_id))
    if task is None:
        raise NotFoundError(f"任务 {task_id} 不存在")
    return task


def create_task(db: Session, data: TaskCreate, *, user_id: int) -> Task:
    try:
        if user_id != current_user_id(db):
            raise NotFoundError("用户不存在")
        get_user(db, user_id)
        get_record(db, Product, data.product_id, lock=True)
        task = Task(**data.model_dump(), user_id=user_id, status=TaskStatus.pending.value)
        db.add(task)
        db.flush()
        db.refresh(task)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("任务数据与数据库约束冲突，请检查关联用户") from exc
    except Exception:
        db.rollback()
        raise
    return task


def list_tasks(
    db: Session,
    *,
    user_id: int | None = None,
    status: TaskStatus | None = None,
    platform_name: str | None = None,
    offset: int = 0,
    limit: int = 20,
    include_deleted: bool = False,
) -> list[Task]:
    """按创建时间倒序分页，ID 用于保证同一时间下排序稳定。"""
    validate_pagination(offset, limit)
    statement = select(Task).where(Task.user_id == current_user_id(db)) if include_deleted else owned_query(db, Task)
    if user_id is not None:
        validate_id(user_id)
        statement = statement.where(Task.user_id == user_id)
    if status is not None:
        try:
            selected_status = TaskStatus(status)
        except ValueError as exc:
            raise InvalidInputError("未知的任务状态") from exc
        statement = statement.where(Task.status == selected_status.value)
    if platform_name is not None:
        statement = statement.where(Task.platform_name == platform_name)
    statement = statement.order_by(Task.created_at.desc(), Task.id.desc())
    return list(db.scalars(statement.offset(offset).limit(limit)))


def update_task_status(db: Session, task_id: int, data: TaskStatusUpdate) -> Task:
    """锁住任务行后检查状态，避免并发请求基于旧状态更新。"""
    try:
        validate_id(task_id)
        task = db.scalar(
            owned_query(db, Task).where(Task.id == task_id).with_for_update()
            .execution_options(populate_existing=True)
        )
        if task is None:
            raise NotFoundError(f"任务 {task_id} 不存在")
        try:
            current = TaskStatus(task.status)
        except ValueError as exc:
            raise InvalidTransitionError(f"数据库中的任务状态未知：{task.status}") from exc
        target = data.status
        # 重复提交相同状态视为成功，不修改更新时间。
        if target != current:
            if target in {TaskStatus.approved, TaskStatus.rejected, TaskStatus.publishing}:
                raise InvalidTransitionError("审核和发布状态必须通过审核、发布记录接口更新")
            if target in {TaskStatus.pending_review, TaskStatus.completed}:
                from models.content import Content
                has_content = db.scalar(select(Content.id).where(Content.task_id == task.id).limit(1))
                if target == TaskStatus.pending_review and has_content is None:
                    raise InvalidTransitionError("没有内容的任务不能提交审核")
                if target == TaskStatus.pending_review and has_content is not None:
                    from .rules import require_review_ready
                    latest_content = db.scalar(select(Content).where(Content.task_id == task.id)
                                               .order_by(Content.version.desc()).limit(1))
                    require_review_ready(db, task, latest_content)
                if target == TaskStatus.completed and has_content is not None:
                    raise InvalidTransitionError("内容任务需通过审核和发布流程完成")
            if target not in ALLOWED_TRANSITIONS[current]:
                raise InvalidTransitionError(f"任务不能从 {current.value} 转为 {target.value}")
            task.status = target.value
            if target == TaskStatus.failed:
                task.failed_from_status = current.value
                # 保留之前保存的 checkpoint。
                task.error_message = "任务执行失败，未提供详细错误"
        db.flush()
        db.refresh(task)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return task


def _lock_task(db: Session, task_id: int) -> Task:
    validate_id(task_id)
    task = db.scalar(owned_query(db, Task).where(Task.id == task_id).with_for_update()
                     .execution_options(populate_existing=True))
    if task is None:
        raise NotFoundError(f"任务 {task_id} 不存在")
    return task


def delete_task(db: Session, task_id: int) -> None:
    """彻底删除所属任务及其私有派生数据；失败后可重试。"""
    from .deletion import delete_task_data
    delete_task_data(db, task_id)


def save_task_checkpoint(db: Session, task_id: int, data: TaskCheckpointSave) -> Task:
    """在执行阶段边界保存完整进度快照；不改变任务状态。"""
    try:
        task = _lock_task(db, task_id)
        if task.status not in {TaskStatus.running.value, TaskStatus.publishing.value}:
            raise InvalidTransitionError("只允许为处理或发布中的任务保存进度")
        task.checkpoint = data.model_dump(mode="json")["checkpoint"]
        db.flush()
        db.refresh(task)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return task


def fail_task(db: Session, task_id: int, data: TaskFailureSave) -> Task:
    """同一事务内保存失败现场及失败前的状态。"""
    try:
        task = _lock_task(db, task_id)
        if task.status not in {TaskStatus.running.value, TaskStatus.publishing.value}:
            raise InvalidTransitionError("只允许将处理或发布中的任务标记为失败")
        task.failed_from_status = task.status
        task.checkpoint = data.model_dump(mode="json")["checkpoint"]
        task.error_message = data.error_message
        task.status = TaskStatus.failed.value
        db.flush()
        db.refresh(task)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return task


def resume_task(db: Session, task_id: int) -> Task:
    """恢复到失败前的状态并保留快照；执行器仍需读取快照继续工作。"""
    try:
        task = _lock_task(db, task_id)
        if task.status != TaskStatus.failed.value:
            raise InvalidTransitionError("只能恢复失败的任务")
        if task.failed_from_status not in {TaskStatus.running.value, TaskStatus.publishing.value}:
            raise InvalidTransitionError("该任务没有有效的失败阶段，不能自动恢复")
        task.status = task.failed_from_status
        # 保留最近一次错误及阶段，便于诊断。
        db.flush()
        db.refresh(task)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return task
