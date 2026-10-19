from typing import Annotated
from fastapi import APIRouter, Depends, Path, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
from schemas.product import ProductCreate, ProductRead, ProductUpdate, ProductDocumentRead
from services import product as service
from services.common import current_user_id
from .dependencies import get_current_user

router = APIRouter(prefix="/products", tags=["product"], dependencies=[Depends(get_current_user)])
Database = Annotated[Session, Depends(get_db)]


@router.post("", response_model=ProductRead, status_code=201)
def create_product(data: ProductCreate, db: Database):
    return service.create_product(db, data)


@router.get("", response_model=list[ProductRead])
def list_products(db: Database, offset: Annotated[int, Query(ge=0)] = 0,
                  limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return service.list_products(db, offset=offset, limit=limit)


@router.get("/{product_id}", response_model=ProductRead)
def get_product(product_id: Annotated[int, Path(gt=0)], db: Database):
    return service.get_product(db, product_id)


@router.patch("/{product_id}", response_model=ProductRead)
def update_product(product_id: Annotated[int, Path(gt=0)], data: ProductUpdate, db: Database):
    return service.update_product(db, product_id, data)


@router.delete("/{product_id}", status_code=204)
def delete_product(product_id: Annotated[int, Path(gt=0)], db: Database):
    service.delete_product(db, product_id)
    return Response(status_code=204)


@router.post("/{product_id}/documents", response_model=ProductDocumentRead, status_code=201)
def upload_document(product_id: Annotated[int, Path(gt=0)], file: UploadFile, db: Database):
    document_id = service.upload_document(db, product_id, file)
    return service.get_document(db, product_id, document_id)


@router.get("/{product_id}/documents", response_model=list[ProductDocumentRead])
def list_documents(product_id: Annotated[int, Path(gt=0)], db: Database,
                   offset: Annotated[int, Query(ge=0)] = 0,
                   limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return service.list_documents(db, product_id, offset=offset, limit=limit)


@router.get("/{product_id}/documents/{document_id}/download")
def download_document(product_id: Annotated[int, Path(gt=0)],
                       document_id: Annotated[int, Path(gt=0)], db: Database):
    document = service.get_document(db, product_id, document_id)
    return FileResponse(service.document_path(document, user_id=current_user_id(db)), filename=document.filename,
                        media_type=document.content_type, headers={"Cache-Control": "no-store"})


@router.delete("/{product_id}/documents/{document_id}", status_code=204)
def delete_document(product_id: Annotated[int, Path(gt=0)],
                     document_id: Annotated[int, Path(gt=0)], db: Database):
    service.delete_document(db, product_id, document_id)
    return Response(status_code=204)
