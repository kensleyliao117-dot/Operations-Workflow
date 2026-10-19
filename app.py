import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from config import CORS_ORIGINS, require_jwt_secret
from routers.task import router as task_router
from routers.auth import router as auth_router
from routers.product import router as product_router
from routers.rag import router as rag_router
from routers.content import router as content_router
from routers.review import router as review_router
from routers.publish_record import router as publish_router
from routers.prompt import router as prompt_router
from routers.rules import router as rules_router
from routers.rednote import router as rednote_router
from routers.platform_account import router as platform_account_router
from routers.workflow import router as workflow_router
from routers.memory import router as memory_router
from routers.analysis_model import router as analysis_model_router
from routers.platform import router as platform_router
from routers.resource_model import router as resource_model_router
from services.exceptions import ConflictError, InvalidInputError, NotFoundError, ServiceError, ServiceUnavailableError
from services.exceptions import AuthenticationError, PermissionDeniedError

logger = logging.getLogger(__name__)
@asynccontextmanager
async def lifespan(app):
    require_jwt_secret()
    yield


app = FastAPI(title="运营工作流 API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError):
    if isinstance(exc, AuthenticationError):
        return JSONResponse(status_code=401, content={"detail": str(exc)},
                            headers={"WWW-Authenticate": "Bearer"})
    if isinstance(exc, PermissionDeniedError):
        status_code = 403
    elif isinstance(exc, ServiceUnavailableError):
        status_code = 503
    elif isinstance(exc, NotFoundError):
        status_code = 404
    elif isinstance(exc, ConflictError):
        status_code = 409
    elif isinstance(exc, InvalidInputError):
        status_code = 422
    else:
        status_code = 400
    return JSONResponse(status_code=status_code, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith(("/analysis-models", "/resource-models")):
        # 配置校验失败时不返回请求输入，避免 URL、密钥或整个请求体进入错误响应。
        errors = [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
                  for error in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, exc: SQLAlchemyError):
    # 不把 SQL 参数、连接地址或数据库内部错误暴露到响应中。
    logger.error("Database operation failed: %s", type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "数据库暂时不可用"})


@app.get("/health", tags=["健康检查"])
def health():
    return {"status": "ok"}


@app.get("/health/db", tags=["健康检查"])
def database_health(db: Annotated[Session, Depends(get_db)]):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}


app.include_router(auth_router)
app.include_router(product_router)
app.include_router(rag_router)
app.include_router(task_router)
app.include_router(workflow_router)
app.include_router(memory_router)
app.include_router(content_router)
app.include_router(review_router)
app.include_router(publish_router)
app.include_router(prompt_router)
app.include_router(rules_router)
app.include_router(rednote_router)
app.include_router(platform_account_router)
app.include_router(analysis_model_router)
app.include_router(platform_router)
app.include_router(resource_model_router)
