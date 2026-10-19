from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response
from sqlalchemy.orm import Session

from database import get_db
from schemas.analysis_model import AnalysisModelRead, AnalysisModelWrite, ModelConnectionRead
from services import analysis_models as service
from .dependencies import get_current_user

router = APIRouter(prefix="/analysis-models", tags=["分析模型"], dependencies=[Depends(get_current_user)])
Database = Annotated[Session, Depends(get_db)]
ModelId = Annotated[int, Path(gt=0)]


@router.get("", response_model=list[AnalysisModelRead])
def list_models(db: Database):
    return service.list_models(db)


@router.post("", response_model=AnalysisModelRead, status_code=201)
def create_model(data: AnalysisModelWrite, db: Database):
    return service.save_model(db, data)


@router.put("/{model_id}", response_model=AnalysisModelRead)
def update_model(model_id: ModelId, data: AnalysisModelWrite, db: Database):
    return service.save_model(db, data, model_id)


@router.delete("/system/{model_id:path}", status_code=204)
def remove_system_model(model_id: Annotated[str, Path(min_length=1, max_length=100)], db: Database):
    service.remove_system_model(db, model_id)
    return Response(status_code=204)


@router.post("/system/restore", status_code=204)
def restore_system_models(db: Database):
    service.restore_system_models(db)
    return Response(status_code=204)


@router.delete("/{model_id}", status_code=204)
def delete_model(model_id: ModelId, db: Database):
    service.delete_model(db, model_id)
    return Response(status_code=204)


@router.post("/{model_id}/test", response_model=ModelConnectionRead)
def test_connection(model_id: ModelId, db: Database):
    return service.test_connection(db, model_id)

