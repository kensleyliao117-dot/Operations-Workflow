"""按任务分配内容版本，修改时新增记录，保留历史版本。"""
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.content import Content
from models.task import Task
from schemas.content import ContentCreate, ContentUpdate
from .exceptions import ConflictError, NotFoundError
from .validation import validate_id, validate_pagination
from .common import get_record, owned_query


def get_content(db: Session, content_id: int) -> Content:
    validate_id(content_id)
    content = db.scalar(owned_query(db, Content).where(Content.id == content_id))
    if content is None:
        raise NotFoundError(f"内容 {content_id} 不存在")
    return content


def _lock_task(db: Session, task_id: int) -> Task:
    task = db.scalar(owned_query(db, Task).where(Task.id == task_id).with_for_update()
                     .execution_options(populate_existing=True))
    if task is None:
        raise NotFoundError(f"任务 {task_id} 不存在")
    if task.status in {"approved", "publishing", "completed", "cancelled"}:
        raise ConflictError("已通过审核、发布中、已完成或已取消的任务不能新增或修改内容")
    return task


def _next_version(db: Session, task_id: int) -> int:
    # 先锁住任务行，再串行分配版本号，避免并发请求取得相同版本。
    return (db.scalar(select(func.max(Content.version)).where(Content.task_id == task_id)) or 0) + 1


def _persist(db: Session, content: Content) -> Content:
    db.add(content)
    db.flush()
    db.refresh(content)
    db.commit()
    return content


def add_content_version(db: Session, data: ContentCreate) -> Content:
    """锁住任务并分配新版本；由调用方统一提交或回滚事务。"""
    task = _lock_task(db, data.task_id)
    if task.status == "pending_review":
        task.status = "running"
    content = Content(**data.model_dump(mode="json"), version=_next_version(db, data.task_id))
    db.add(content)
    return content


def create_content(db: Session, data: ContentCreate) -> Content:
    try:
        return _persist(db, add_content_version(db, data))
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("内容数据与数据库约束冲突") from exc
    except Exception:
        db.rollback()
        raise


def update_content(db: Session, content_id: int, data: ContentUpdate) -> Content:
    """以指定版本为基础创建新版本，拒绝修改旧版本，避免覆盖后续修改。"""
    try:
        original = get_content(db, content_id)
        task = _lock_task(db, original.task_id)
        version = _next_version(db, original.task_id)
        if original.version != version - 1:
            raise ConflictError("该内容不是最新版本，请获取最新版本后再修改")
        if task.status == "pending_review":
            task.status = "running"
        values = {name: getattr(original, name) for name in
                  ("task_id", "title", "body", "tags", "image_prompt", "video_script", "image_assets", "generation_metadata")}
        values.update(data.model_dump(mode="json", exclude_unset=True))
        if not (values["title"].strip() or values["body"].strip() or values["image_assets"]):
            raise ConflictError("不能清空全部已保存文案和图片")
        return _persist(db, Content(**values, version=version))
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("内容数据与数据库约束冲突") from exc
    except Exception:
        db.rollback()
        raise


def list_contents(db: Session, *, task_id: int | None = None,
                  offset: int = 0, limit: int = 20) -> list[Content]:
    validate_pagination(offset, limit)
    statement = owned_query(db, Content)
    if task_id is not None:
        validate_id(task_id)
        statement = statement.where(Content.task_id == task_id)
    return list(db.scalars(statement.order_by(Content.id.desc()).offset(offset).limit(limit)))
