from typing import Annotated
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from database import get_db
from schemas.auth import RegisterRequest, RegisteredUser, LoginRequest, TokenResponse
from services import auth as service
from .dependencies import CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])
Database = Annotated[Session, Depends(get_db)]


@router.get("/me", response_model=RegisteredUser)
def me(current_user: CurrentUser):
    return current_user


@router.post("/register", response_model=RegisteredUser, status_code=201)
def register(data: RegisterRequest, db: Database):
    return service.register(db, data)


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, db: Database, response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return service.login(db, data)


@router.post("/logout", status_code=204)
def logout(db: Database, current_user: CurrentUser):
    service.logout(db)
    return Response(status_code=204)
