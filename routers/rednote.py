from .dependencies import get_current_user, require_local_collection
from typing import Annotated
from fastapi import APIRouter, Body, Depends, Path, Query, Response
from sqlalchemy.orm import Session
from database import get_db
from schemas.rednote import RednoteCaseCreate, RednoteCaseRead, RednoteAnalysisCreate, RednoteAnalysisRead
from schemas.rednote import RednoteCollectRequest, RednoteCollectResult
from schemas.rednote import (
    RednoteAnalyzeRequest, RednoteAnalysisComparison, RednoteCaseSearchRead,
    RednoteFeedbackCreate, RednoteFeedbackRead, RednoteReviseRequest,
)
from schemas.analysis_model import AnalysisModelRead
from services import analysis_models
from services import rednote as service

router = APIRouter(prefix="/rednote", tags=["rednote"], dependencies=[Depends(get_current_user)])
Database = Annotated[Session, Depends(get_db)]


@router.get("/browser-status", dependencies=[Depends(require_local_collection)])
def browser_status():
    from collectors.chrome import ChromeBrowser
    from collectors.base import CollectorError
    try:
        with ChromeBrowser() as browser:
            return {"connected": browser.is_connected, "message": "Chrome 已连接"}
    except CollectorError as exc:
        return {"connected": False, "message": str(exc)}


@router.post("/collect", response_model=RednoteCollectResult, status_code=201,
             dependencies=[Depends(require_local_collection)])
def collect_case(data: RednoteCollectRequest, db: Database):
    """本机采集并保存到登录用户的案例库，可选自动分析。"""
    return service.collect_and_save(db, data)


@router.post("/cases/{case_id}/analyze", response_model=RednoteAnalysisRead, status_code=201,
             dependencies=[Depends(require_local_collection)])
def analyze_case(case_id: Annotated[int, Path(gt=0)], db: Database,
                 data: Annotated[RednoteAnalyzeRequest | None, Body()] = None):
    """新增分析版本，默认参考最近版本和最近 20 条用户反馈。"""
    return service.analyze_case(db, case_id, data)


@router.get("/analysis-models", response_model=list[AnalysisModelRead])
def list_analysis_models(db: Database):
    """兼容原有入口，返回系统配置及当前用户的模型，不暴露凭据。"""
    return analysis_models.list_models(db)


@router.post("/cases", response_model=RednoteCaseRead, status_code=201)
def create_case(data: RednoteCaseCreate, db: Database):
    return service.create_case(db, data)


@router.get("/cases", response_model=list[RednoteCaseRead])
def list_cases(db: Database, offset: Annotated[int, Query(ge=0)] = 0,
               limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return service.list_cases(db, offset=offset, limit=limit)


@router.get("/cases/search", response_model=list[RednoteCaseSearchRead])
def search_cases(db: Database, q: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
                 url: Annotated[str | None, Query(min_length=1, max_length=4000)] = None,
                 offset: Annotated[int, Query(ge=0)] = 0,
                 limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return service.search_cases(db, q=q, url=url, offset=offset, limit=limit)


@router.get("/cases/{case_id}", response_model=RednoteCaseRead)
def get_case(case_id: Annotated[int, Path(gt=0)], db: Database):
    return service.get_case(db, case_id)


@router.put("/cases/{case_id}", response_model=RednoteCaseRead)
def update_case(case_id: Annotated[int, Path(gt=0)], data: RednoteCaseCreate, db: Database):
    return service.update_case(db, case_id, data)


@router.delete("/cases/{case_id}", status_code=204)
def delete_case(case_id: Annotated[int, Path(gt=0)], db: Database):
    service.delete_case(db, case_id)
    return Response(status_code=204)


@router.post("/analyses", response_model=RednoteAnalysisRead, status_code=201)
def create_analysis(data: RednoteAnalysisCreate, db: Database):
    return service.create_analysis(db, data)


@router.get("/analyses", response_model=list[RednoteAnalysisRead])
def list_analyses(db: Database, cases_id: Annotated[int | None, Query(gt=0)] = None,
                  offset: Annotated[int, Query(ge=0)] = 0,
                  limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return service.list_analyses(db, cases_id=cases_id, offset=offset, limit=limit)


@router.get("/analyses/compare", response_model=RednoteAnalysisComparison)
def compare_analyses(db: Database, left_id: Annotated[int, Query(gt=0)], right_id: Annotated[int, Query(gt=0)]):
    return service.compare_analyses(db, left_id, right_id)


@router.post("/analyses/{analysis_id}/feedback", response_model=RednoteFeedbackRead, status_code=201)
def create_feedback(analysis_id: Annotated[int, Path(gt=0)], data: RednoteFeedbackCreate, db: Database):
    """先持久化用户反馈，AI 修订失败时仍可查询并重试。"""
    return service.save_feedback(db, analysis_id, data)


@router.get("/analyses/{analysis_id}/feedback", response_model=list[RednoteFeedbackRead])
def list_feedback(analysis_id: Annotated[int, Path(gt=0)], db: Database,
                   offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return service.list_feedback(db, analysis_id, offset=offset, limit=limit)


@router.post("/analyses/{analysis_id}/revise", response_model=RednoteAnalysisRead, status_code=201,
             dependencies=[Depends(require_local_collection)])
def revise_analysis(analysis_id: Annotated[int, Path(gt=0)], data: RednoteReviseRequest, db: Database):
    return service.revise_analysis(db, analysis_id, data)


@router.get("/analyses/{analysis_id}", response_model=RednoteAnalysisRead)
def get_analysis(analysis_id: Annotated[int, Path(gt=0)], db: Database):
    return service.get_analysis(db, analysis_id)
