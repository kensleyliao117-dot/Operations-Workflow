"""任务工作流启动、查询、审核恢复和故障重试。"""
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from database import get_db
from schemas.content_generation import TaskContentGenerationRequest
from schemas.workflow import WorkflowRead, WorkflowResumeRequest, WorkflowGenerationRequest
from services import workflow as service
from .dependencies import get_current_user

router = APIRouter(prefix="/tasks", tags=["workflow"], dependencies=[Depends(get_current_user)])
Database = Annotated[Session, Depends(get_db)]
TaskId = Annotated[int, Path(gt=0)]


@router.post("/{task_id}/workflow/start", response_model=WorkflowRead, status_code=201)
def start_workflow(task_id: TaskId, data: WorkflowGenerationRequest, db: Database):
    return service.start_workflow(db, task_id, data)


@router.get("/{task_id}/workflow", response_model=WorkflowRead)
def get_workflow(task_id: TaskId, db: Database):
    return service.get_workflow(db, task_id)


@router.post("/{task_id}/workflow/resume", response_model=WorkflowRead)
def resume_workflow(task_id: TaskId, data: WorkflowResumeRequest, db: Database):
    return service.resume_workflow(db, task_id, data)


@router.post("/{task_id}/workflow/retry", response_model=WorkflowRead)
def retry_workflow(task_id: TaskId, db: Database):
    return service.retry_workflow(db, task_id)
