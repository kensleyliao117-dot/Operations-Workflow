from .dependencies import get_current_user
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from database import get_db
from schemas.content import ContentCreate, ContentRead, ContentUpdate
from schemas.image_generation import ImageGenerationRequest, ImageGenerationSavedResult
from services import content as service
from services import image_generation

router = APIRouter(prefix="/contents", tags=["content"], dependencies=[Depends(get_current_user)])
Database = Annotated[Session, Depends(get_db)]


@router.post("", response_model=ContentRead, status_code=201)
def create_content(data: ContentCreate, db: Database):
    return service.create_content(db, data)


@router.get("", response_model=list[ContentRead])
def list_contents(db: Database,
                  task_id: Annotated[int | None, Query(gt=0)] = None,
                  offset: Annotated[int, Query(ge=0)] = 0,
                  limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return service.list_contents(db, task_id=task_id, offset=offset, limit=limit)


@router.get("/{content_id}", response_model=ContentRead)
def get_content(content_id: Annotated[int, Path(gt=0)], db: Database):
    return service.get_content(db, content_id)


@router.patch("/{content_id}", response_model=ContentRead, status_code=201)
def update_content(content_id: Annotated[int, Path(gt=0)], data: ContentUpdate, db: Database):
    """保存修改为新版本，返回新的内容 ID。"""
    return service.update_content(db, content_id, data)


@router.post("/{content_id}/images/generate", response_model=ImageGenerationSavedResult, status_code=201)
def generate_images(content_id: Annotated[int, Path(gt=0)], data: ImageGenerationRequest, db: Database):
    return image_generation.generate_and_save_images(db, content_id, data)


@router.post("/{content_id}/images/submit-review", response_model=ImageGenerationSavedResult)
def submit_image_review(content_id: Annotated[int, Path(gt=0)], db: Database):
    return image_generation.retry_review_submission(db, content_id)


@router.get("/{content_id}/images/{index}")
def get_image(content_id: Annotated[int, Path(gt=0)], index: Annotated[int, Path(ge=0)], db: Database):
    path = image_generation.image_path(db, content_id, index)
    return FileResponse(path, media_type="image/png", headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
