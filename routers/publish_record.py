from .dependencies import get_current_user
from typing import Annotated
from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.orm import Session
from database import get_db
from schemas.publish_record import PublishRequest, PublishRecordRead, PublishResult
from schemas.publish_record import PublishMetricsCreate, PublishMetricsRead
from schemas.publish_record import PublishExecuteRequest, PublishRetryRequest
from services import publish_record as service
from services import publishing
from .dependencies import require_local_publishing

router = APIRouter(prefix="/publish-records", tags=["publish-record"], dependencies=[Depends(get_current_user)])
Database = Annotated[Session, Depends(get_db)]


@router.post("", response_model=PublishRecordRead, status_code=201)
def create_publish_record(data: PublishRequest, db: Database):
    """登记发布开始，不调用平台 API。"""
    return service.create_publish_record(db, data)


@router.get("", response_model=list[PublishRecordRead])
def list_publish_records(db: Database, task_id: Annotated[int | None, Query(gt=0)] = None,
                         content_id: Annotated[int | None, Query(gt=0)] = None,
                         offset: Annotated[int, Query(ge=0)] = 0,
                         limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return service.list_publish_records(db, task_id=task_id, content_id=content_id,
                                       offset=offset, limit=limit)


@router.get("/{record_id}", response_model=PublishRecordRead)
def get_publish_record(record_id: Annotated[int, Path(gt=0)], db: Database):
    return service.get_publish_record(db, record_id)


@router.post("/{record_id}/result", response_model=PublishRecordRead)
def record_result(record_id: Annotated[int, Path(gt=0)], data: PublishResult, db: Database, request: Request):
    if service.get_publish_record(db, record_id).publisher_backend == "chrome":
        require_local_publishing(request)
    return service.record_publish_result(db, record_id, data)


@router.post("/{record_id}/retry", response_model=PublishRecordRead, status_code=201)
def retry_publish(record_id: Annotated[int, Path(gt=0)], db: Database, data: PublishRetryRequest | None = None):
    return service.retry_publish_record(db, record_id, idempotency_key=data.idempotency_key if data else None)


@router.post("/{record_id}/execute", response_model=PublishRecordRead,
             dependencies=[Depends(require_local_publishing)])
def execute_publish(record_id: Annotated[int, Path(gt=0)], data: PublishExecuteRequest, db: Database):
    return publishing.execute_publish(db, record_id)


@router.post("/{record_id}/metrics", response_model=PublishMetricsRead, status_code=201)
def save_metrics(record_id: Annotated[int, Path(gt=0)], data: PublishMetricsCreate, db: Database):
    return service.save_metrics(db, record_id, data)


@router.get("/{record_id}/metrics", response_model=list[PublishMetricsRead])
def list_metrics(record_id: Annotated[int, Path(gt=0)], db: Database,
                 offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return service.list_metrics(db, record_id, offset=offset, limit=limit)
