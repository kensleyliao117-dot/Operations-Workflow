"""JWT 用户的向量及生图模型配置接口。"""
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Path, Response
from sqlalchemy.orm import Session
from database import get_db
from schemas.resource_model import ResourceModelRead, ResourceModelWrite
from schemas.analysis_model import ModelConnectionRead
from services import resource_models as service
from .dependencies import get_current_user

router = APIRouter(prefix='/resource-models', tags=['模型连接'], dependencies=[Depends(get_current_user)])
Database = Annotated[Session, Depends(get_db)]
ModelId = Annotated[int, Path(gt=0)]


@router.get('', response_model=list[ResourceModelRead])
def list_models(db: Database, kind: Literal['embedding', 'image'] | None = None):
    return service.list_models(db, kind)


@router.post('', response_model=ResourceModelRead, status_code=201)
def create_model(data: ResourceModelWrite, db: Database):
    return service.save_model(db, data)


@router.put('/{model_id}', response_model=ResourceModelRead)
def update_model(model_id: ModelId, data: ResourceModelWrite, db: Database):
    return service.save_model(db, data, model_id)


@router.delete('/{model_id}', status_code=204)
def delete_model(model_id: ModelId, db: Database):
    service.delete_model(db, model_id)
    return Response(status_code=204)


@router.post('/{model_id}/test', response_model=ModelConnectionRead)
def test_embedding(model_id: ModelId, db: Database):
    return service.test_embedding(db, model_id)
