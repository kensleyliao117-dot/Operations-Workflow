from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from database import get_db
from .dependencies import CurrentUser, get_current_user
from services import task as service
from services import content_generation
from schemas.content_generation import ContentGenerationSavedResult, TaskContentGenerationRequest
from schemas.task import TaskCreate, TaskRead, TaskStatus, TaskStatusUpdate, TaskCheckpointSave, TaskFailureSave

router = APIRouter(prefix="/tasks", tags=["task"], dependencies=[Depends(get_current_user)])
Database = Annotated[Session, Depends(get_db)]


@router.post("", response_model=TaskRead, status_code=201)
def create_task(data: TaskCreate, db: Database, current_user: CurrentUser):
    return service.create_task(db, data, user_id=current_user.id)


@router.get("", response_model=list[TaskRead])
def list_tasks(
    db: Database,
    status: TaskStatus | None = None,
    platform_name: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    include_deleted: bool = False,
):
    return service.list_tasks(db, status=status,
                              platform_name=platform_name, offset=offset, limit=limit,
                              include_deleted=include_deleted)


@router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: Annotated[int, Path(gt=0)], db: Database):
    return service.get_task(db, task_id)


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: Annotated[int, Path(gt=0)], db: Database):
    service.delete_task(db, task_id)


@router.patch("/{task_id}/status", response_model=TaskRead)
def update_task_status(task_id: Annotated[int, Path(gt=0)], data: TaskStatusUpdate, db: Database):
    return service.update_task_status(db, task_id, data)


@router.put("/{task_id}/checkpoint", response_model=TaskRead)
def save_checkpoint(task_id: Annotated[int, Path(gt=0)], data: TaskCheckpointSave, db: Database):
    return service.save_task_checkpoint(db, task_id, data)


@router.post("/{task_id}/fail", response_model=TaskRead)
def fail_task(task_id: Annotated[int, Path(gt=0)], data: TaskFailureSave, db: Database):
    return service.fail_task(db, task_id, data)


@router.post("/{task_id}/resume", response_model=TaskRead)
def resume_task(task_id: Annotated[int, Path(gt=0)], db: Database):
    return service.resume_task(db, task_id)


@router.post("/{task_id}/generate", response_model=ContentGenerationSavedResult, status_code=201)
def generate_content(task_id: Annotated[int, Path(gt=0)], data: TaskContentGenerationRequest, db: Database):
    """组合产品资料、可选 Prompt 和案例分析生成新版本，保存后进入人工审核。"""
    return content_generation.generate_and_save_content(db, task_id, data)
