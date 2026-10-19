"""JWT 用户所属平台账号登记与查询。"""
from typing import Annotated
from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from database import get_db
from models.platform_account import PlatformAccount
from schemas.platform_account import PlatformAccountCreate, PlatformAccountRead
from services import platform_account as service
from services.common import get_record
from .dependencies import get_current_user
from .dependencies import require_local_publishing

router = APIRouter(prefix="/platform-accounts", tags=["platform-account"], dependencies=[Depends(get_current_user)])
Database = Annotated[Session, Depends(get_db)]


@router.post("", response_model=PlatformAccountRead, status_code=201)
def create_account(data: PlatformAccountCreate, db: Database):
    return service.create_account(db, data)


@router.get("", response_model=list[PlatformAccountRead])
def list_accounts(db: Database, offset: Annotated[int, Query(ge=0)] = 0,
                  limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return service.list_accounts(db, offset=offset, limit=limit)


@router.get("/browser-current", dependencies=[Depends(require_local_publishing)])
def current_browser_account(db: Database):
    from collectors.chrome import ChromeBrowser
    from integrations.publishers.rednote import CREATOR_URL, read_account
    from integrations.publishers.locking import publishing_lock
    with publishing_lock(db):
        with ChromeBrowser() as browser:
            page = browser.open_page(CREATOR_URL)
            return {"platform": "rednote", "platform_user_id": read_account(page), "confirmation_required": True}


@router.get("/{account_id}", response_model=PlatformAccountRead)
def get_account(account_id: Annotated[int, Path(gt=0)], db: Database):
    return get_record(db, PlatformAccount, account_id)
