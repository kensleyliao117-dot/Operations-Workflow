"""作品观测、经验提炼和历史经验检索接口。"""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from database import get_db
from memory_database import get_memory_db
from memory.models import Feedback, OptimizationMemory, PublishedPost
from memory.schemas import (
    CollectMetricsRequest, FeedbackRequest, ManualMetricsRequest, MemoryRead, MemoryReference,
    MemoryUpdate, MetricsRead, PublicationRead, SyncRead, FeedbackRead, FeedbackPublicationRead,
)
from memory.services import publications, metrics, feedback
from memory.services import deletion
from memory.services.common import get_owned, memory_transaction, owned
from memory import retriever
from models.memory_sync import MemorySyncEvent
from models.publish_record import PublishRecord
from models.product import Product
from services.common import get_record
from .dependencies import get_current_user, require_local_publishing


router = APIRouter(tags=["运营经验"], dependencies=[Depends(get_current_user)])
Database = Annotated[Session, Depends(get_db)]
MemoryDatabase = Annotated[Session, Depends(get_memory_db)]
RecordId = Annotated[int, Path(gt=0)]


@router.post("/publish-records/{record_id}/memory/sync", response_model=PublicationRead)
def sync(record_id: RecordId, db: Database, memory_db: MemoryDatabase):
    return publications.sync_publication(db, memory_db, record_id, restore_deleted=True)


@router.delete('/publish-records/{record_id}/memory', status_code=204)
def delete_publication_feedback(record_id: RecordId, db: Database, memory_db: MemoryDatabase):
    deletion.delete_publication_feedback(db, memory_db, record_id)


@router.delete('/publish-records/{record_id}/memory/metrics/{metric_id}', status_code=204)
def delete_metric(record_id: RecordId, metric_id: Annotated[int, Path(gt=0)], db: Database, memory_db: MemoryDatabase):
    deletion.delete_metric(db, memory_db, record_id, metric_id)


@router.delete('/memory/experiences/{memory_id}', status_code=204)
def delete_experience(memory_id: Annotated[int, Path(gt=0)], db: Database, memory_db: MemoryDatabase):
    deletion.delete_experience(db, memory_db, memory_id)


@router.get("/publish-records/{record_id}/memory/sync-status", response_model=SyncRead)
def sync_status(record_id: RecordId, db: Database):
    get_record(db, PublishRecord, record_id)
    event = db.scalar(owned(db, db, MemorySyncEvent).where(MemorySyncEvent.publish_record_id == record_id))
    return event or {"publish_record_id": record_id, "status": "pending", "attempts": 0, "error_message": None}


@router.get("/publish-records/{record_id}/memory/publication", response_model=PublicationRead)
def publication_for_record(record_id: RecordId, db: Database, memory_db: MemoryDatabase):
    from services.exceptions import NotFoundError
    publications.publication_source(db, record_id)
    post = memory_db.scalar(owned(db, memory_db, PublishedPost).where(PublishedPost.publish_record_id == record_id))
    if post is None:
        raise NotFoundError("作品尚未同步到经验库")
    return post


@router.post("/memory/sync-pending", response_model=list[SyncRead])
def sync_pending(db: Database, memory_db: MemoryDatabase, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    from services.exceptions import ServiceError
    from sqlalchemy.exc import SQLAlchemyError
    events = list(db.scalars(owned(db, db, MemorySyncEvent).where(MemorySyncEvent.status.in_(["pending", "failed"]))
                            .order_by(MemorySyncEvent.id).limit(limit)))
    ids = [(event.id, event.publish_record_id) for event in events]
    db.rollback()
    result = []
    for event_id, record_id in ids:
        try:
            publications.sync_publication(db, memory_db, record_id)
        except (ServiceError, SQLAlchemyError):
            db.rollback()
            memory_db.rollback()
        result.append(SyncRead.model_validate(get_record(db, MemorySyncEvent, event_id)))
    return result


@router.get("/memory/publications/{post_id}", response_model=PublicationRead)
def get_publication(post_id: Annotated[int, Path(gt=0)], db: Database, memory_db: MemoryDatabase):
    value = get_owned(db, memory_db, PublishedPost, post_id)
    publications.publication_source(db, value.publish_record_id)
    return value


@router.get("/memory/publications", response_model=list[FeedbackPublicationRead])
def feedback_publications(db: Database, platform: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
                          product_id: Annotated[int | None, Query(gt=0)] = None,
                          offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return publications.list_for_feedback(db, platform=platform, product_id=product_id, offset=offset, limit=limit)


@router.get("/memory/feedback/{feedback_id}", response_model=FeedbackRead)
def get_feedback(feedback_id: Annotated[int, Path(gt=0)], db: Database, memory_db: MemoryDatabase):
    return get_owned(db, memory_db, Feedback, feedback_id)


@router.post("/publish-records/{record_id}/memory/metrics", response_model=MetricsRead, status_code=201)
def save_metrics(record_id: RecordId, data: ManualMetricsRequest, db: Database, memory_db: MemoryDatabase):
    value = metrics.save_manual(db, memory_db, record_id, data)
    return metrics.describe(db, memory_db, record_id, value)


@router.get("/publish-records/{record_id}/memory/metrics", response_model=list[MetricsRead])
def list_metrics(record_id: RecordId, db: Database, memory_db: MemoryDatabase,
                 offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return metrics.list_metrics(db, memory_db, record_id, offset=offset, limit=limit)


@router.post("/publish-records/{record_id}/memory/collect", response_model=MetricsRead, status_code=201,
             dependencies=[Depends(require_local_publishing)])
def collect_metrics(record_id: RecordId, data: CollectMetricsRequest, db: Database, memory_db: MemoryDatabase):
    value = metrics.collect(db, memory_db, record_id, data)
    return metrics.describe(db, memory_db, record_id, value)


@router.post("/publish-records/{record_id}/memory/analyze", response_model=MemoryRead, status_code=201)
def analyze(record_id: RecordId, data: FeedbackRequest, db: Database, memory_db: MemoryDatabase):
    return feedback.analyze(db, memory_db, record_id, data)


@router.get("/memory/experiences", response_model=list[MemoryRead])
def list_experiences(db: Database, memory_db: MemoryDatabase,
                     product_id: Annotated[int, Query(gt=0)],
                     offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    get_record(db, Product, product_id)
    return list(memory_db.scalars(owned(db, memory_db, OptimizationMemory).where(
        OptimizationMemory.product_id == product_id).order_by(OptimizationMemory.id.desc()).offset(offset).limit(limit)))


@router.get("/memory/experiences/{memory_id}", response_model=MemoryRead)
def get_experience(memory_id: Annotated[int, Path(gt=0)], db: Database, memory_db: MemoryDatabase):
    return get_owned(db, memory_db, OptimizationMemory, memory_id)


@router.patch("/memory/experiences/{memory_id}", response_model=MemoryRead)
def update_experience(memory_id: Annotated[int, Path(gt=0)], data: MemoryUpdate, db: Database, memory_db: MemoryDatabase):
    with memory_transaction(memory_db):
        record = get_owned(db, memory_db, OptimizationMemory, memory_id, lock=True)
        record.is_active = data.is_active
    return record


@router.get("/products/{product_id}/memory/retrieve", response_model=list[MemoryReference])
def retrieve_memory(product_id: Annotated[int, Path(gt=0)], db: Database, memory_db: MemoryDatabase,
                    query: Annotated[str, Query(min_length=1, max_length=2000)],
                    limit: Annotated[int, Query(ge=1, le=5)] = 5,
                    account_id: Annotated[int | None, Query(gt=0)] = None):
    return retriever.retrieve(db, memory_db, product_id, query, limit=limit, account_id=account_id)
