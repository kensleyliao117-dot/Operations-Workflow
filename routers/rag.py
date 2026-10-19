from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, UploadFile
from sqlalchemy.orm import Session

from database import get_db
from rag import retriever
from schemas.rag import IndexRead, IndexRequest, RetrievalRead, RetrieveRequest
from .dependencies import get_current_user

router = APIRouter(prefix="/products/{product_id}/rag", tags=["产品知识库"],
                   dependencies=[Depends(get_current_user)])
Database = Annotated[Session, Depends(get_db)]
ProductId = Annotated[int, Path(gt=0)]


@router.post("/documents", response_model=IndexRead, status_code=201)
def upload_and_index(product_id: ProductId, file: UploadFile, db: Database,
                     options: Annotated[IndexRequest, Query()]):
    return retriever.upload_and_index_document(db, product_id, file, **options.model_dump())


@router.post("/documents/{document_id}/index", response_model=IndexRead)
def index_document(product_id: ProductId, document_id: Annotated[int, Path(gt=0)],
                   options: IndexRequest, db: Database):
    return retriever.index_document(db, product_id, document_id, **options.model_dump())


@router.post("/retrieve", response_model=RetrievalRead)
def retrieve(product_id: ProductId, data: RetrieveRequest, db: Database):
    return retriever.retrieve(db, product_id, **data.model_dump())
