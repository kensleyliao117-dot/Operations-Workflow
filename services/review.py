from sqlalchemy import func, select

from models.content import Content
from models.review import Review
from models.task import Task
from models.user import Users
from schemas.review import ReviewCreate, ReviewStatus
from .common import current_user_id, get_record, list_records, write_transaction
from .exceptions import ConflictError, InvalidInputError


def require_latest_content(db, task_id, content_id):
    content = get_record(db, Content, content_id)
    if content.task_id != task_id:
        raise ConflictError("该内容不属于当前任务")
    latest = db.scalar(select(func.max(Content.version)).where(Content.task_id == task_id))
    if content.version != latest:
        raise ConflictError("只能审核或发布任务的最新内容版本")
    from .rules import require_review_ready
    require_review_ready(db, get_record(db, Task, task_id), content)
    return content


def create_review(db, data: ReviewCreate) -> Review:
    with write_transaction(db):
        task = get_record(db, Task, data.task_id, lock=True)
        if task.status != "pending_review":
            raise ConflictError("只有待审核任务可以提交审核结果")
        require_latest_content(db, task.id, data.content_id)
        get_record(db, Users, current_user_id(db))
        if data.status == ReviewStatus.rejected and not (data.comment or "").strip():
            raise InvalidInputError("驳回时需要填写修改意见")
        review = Review(**data.model_dump(mode="json"), reviewer_id=current_user_id(db))
        db.add(review)
        task.status = data.status.value
    return review


def get_review(db, review_id):
    return get_record(db, Review, review_id)


def list_reviews(db, *, task_id=None, content_id=None, offset=0, limit=20):
    return list_records(db, Review, task_id=task_id, content_id=content_id, offset=offset, limit=limit)
