from .dependencies import get_current_user
from typing import Annotated
from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session
from database import get_db
from schemas.review import ReviewCreate, ReviewRead
from services import review as service

router = APIRouter(prefix="/reviews", tags=["review"], dependencies=[Depends(get_current_user)])
Database = Annotated[Session, Depends(get_db)]


@router.post("", response_model=ReviewRead, status_code=201)
def create_review(data: ReviewCreate, db: Database):
    return service.create_review(db, data)


@router.get("", response_model=list[ReviewRead])
def list_reviews(db: Database, task_id: Annotated[int | None, Query(gt=0)] = None,
                 content_id: Annotated[int | None, Query(gt=0)] = None,
                 offset: Annotated[int, Query(ge=0)] = 0,
                 limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return service.list_reviews(db, task_id=task_id, content_id=content_id, offset=offset, limit=limit)


@router.get("/{review_id}", response_model=ReviewRead)
def get_review(review_id: Annotated[int, Path(gt=0)], db: Database):
    return service.get_review(db, review_id)
