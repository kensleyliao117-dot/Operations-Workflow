from .dependencies import get_current_user
from typing import Annotated
from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.orm import Session
from database import get_db
from schemas.prompt import PromptCreate, PromptRead, PromptUpdate
from services import prompt as service

router = APIRouter(prefix="/prompts", tags=["prompt"], dependencies=[Depends(get_current_user)])
Database = Annotated[Session, Depends(get_db)]


@router.post("", response_model=PromptRead, status_code=201)
def create_prompt(data: PromptCreate, db: Database):
    return service.create_prompt(db, data)


@router.get("", response_model=list[PromptRead])
def list_prompts(db: Database,
                 platform: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
                 is_active: bool | None = None,
                 offset: Annotated[int, Query(ge=0)] = 0,
                 limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return service.list_prompts(db, platform=platform, is_active=is_active,
                               offset=offset, limit=limit)


@router.get("/{prompt_id}", response_model=PromptRead)
def get_prompt(prompt_id: Annotated[int, Path(gt=0)], db: Database):
    return service.get_prompt(db, prompt_id)


@router.put("/{prompt_id}", response_model=PromptRead)
def update_prompt(prompt_id: Annotated[int, Path(gt=0)], data: PromptUpdate, db: Database):
    return service.update_prompt(db, prompt_id, data)


@router.delete("/{prompt_id}", status_code=204)
def delete_prompt(prompt_id: Annotated[int, Path(gt=0)], db: Database):
    service.delete_prompt(db, prompt_id)
    return Response(status_code=204)
