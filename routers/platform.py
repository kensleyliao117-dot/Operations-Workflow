from fastapi import APIRouter, Depends

from services.platforms import platform_catalog
from .dependencies import get_current_user

router = APIRouter(prefix="/platforms", tags=["平台"], dependencies=[Depends(get_current_user)])


@router.get("")
def list_platforms():
    return platform_catalog()

