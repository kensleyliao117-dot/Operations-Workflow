from .dependencies import get_current_user
from typing import Annotated
from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.orm import Session
from database import get_db
from schemas.rules import CustomRuleCreate, CustomRuleRead, CustomRuleUpdate, PlatformRuleCreate, PlatformRuleRead
from schemas.rules import RuleCheckRequest, RuleCheckRead
from schemas.notices import user_notices
from services import rules as service

router = APIRouter(prefix="/rules", tags=["rules"], dependencies=[Depends(get_current_user)])
Database = Annotated[Session, Depends(get_db)]


@router.get("/notices")
def get_notices():
    return {"notices": user_notices(), "image_binary_reviewed": False}


@router.post("/checks", response_model=RuleCheckRead, status_code=201)
def check_content(data: RuleCheckRequest, db: Database):
    """检查已保存的内容版本，返回潜在风险及建议，不代替平台审核。"""
    return service.check_content(db, data.content_id, force=data.force)


@router.get("/checks", response_model=list[RuleCheckRead])
def list_checks(db: Database, content_id: Annotated[int | None, Query(gt=0)] = None,
                 offset: Annotated[int, Query(ge=0)] = 0,
                 limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return service.list_checks(db, content_id=content_id, offset=offset, limit=limit)


@router.get("/checks/{check_id}", response_model=RuleCheckRead)
def get_check(check_id: Annotated[int, Path(gt=0)], db: Database):
    return service.get_check(db, check_id)


@router.post("/custom", response_model=CustomRuleRead, status_code=201)
def create_custom_rule(data: CustomRuleCreate, db: Database):
    return service.create_custom_rule(db, data)


@router.get("/custom", response_model=list[CustomRuleRead])
def list_custom_rules(db: Database,
                      platform_name: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
                      offset: Annotated[int, Query(ge=0)] = 0,
                      limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return service.list_custom_rules(db, platform_name=platform_name, offset=offset, limit=limit)


@router.get("/custom/{rule_id}", response_model=CustomRuleRead)
def get_custom_rule(rule_id: Annotated[int, Path(gt=0)], db: Database):
    return service.get_custom_rule(db, rule_id)


@router.put("/custom/{rule_id}", response_model=CustomRuleRead)
def update_custom_rule(rule_id: Annotated[int, Path(gt=0)], data: CustomRuleUpdate, db: Database):
    return service.update_custom_rule(db, rule_id, data)


@router.delete("/custom/{rule_id}", status_code=204)
def delete_custom_rule(rule_id: Annotated[int, Path(gt=0)], db: Database):
    service.delete_custom_rule(db, rule_id)
    return Response(status_code=204)


@router.post("/platform", response_model=PlatformRuleRead, status_code=201)
def create_platform_rule(data: PlatformRuleCreate, db: Database):
    return service.create_platform_rule(db, data)


@router.get("/platform", response_model=list[PlatformRuleRead])
def list_platform_rules(db: Database,
                        platform_name: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
                        offset: Annotated[int, Query(ge=0)] = 0,
                        limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return service.list_platform_rules(db, platform_name=platform_name, offset=offset, limit=limit)


@router.get("/platform/{rule_id}", response_model=PlatformRuleRead)
def get_platform_rule(rule_id: Annotated[int, Path(gt=0)], db: Database):
    return service.get_platform_rule(db, rule_id)


@router.put("/platform/{rule_id}", response_model=PlatformRuleRead)
def update_platform_rule(rule_id: Annotated[int, Path(gt=0)], data: PlatformRuleCreate, db: Database):
    return service.update_platform_rule(db, rule_id, data)


@router.delete("/platform/{rule_id}", status_code=204)
def delete_platform_rule(rule_id: Annotated[int, Path(gt=0)], db: Database):
    service.delete_platform_rule(db, rule_id)
    return Response(status_code=204)
