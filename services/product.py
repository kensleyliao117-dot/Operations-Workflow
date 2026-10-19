from pathlib import Path, PurePosixPath
import uuid
from zipfile import BadZipFile, ZipFile
from sqlalchemy import select

from config import MAX_UPLOAD_BYTES, UPLOAD_DIR
from models.product import Product, ProductDocument
from models.task import Task
from .common import current_user_id, get_record, list_records, write_transaction
from .exceptions import ConflictError, InvalidInputError, NotFoundError, ServiceUnavailableError


def _upload_path(storage_key: str, *, user_id: int, product_id: int) -> Path:
    """storage_key 保留 uploads/ 逻辑前缀，实际根目录由 UPLOAD_DIR 决定。"""
    parts = PurePosixPath(storage_key).parts
    if (len(parts) != 4 or parts[:3] != ("uploads", str(user_id), str(product_id))
            or "\\" in storage_key or ":" in storage_key or ".." in parts):
        raise InvalidInputError("资料保存路径无效")
    root = UPLOAD_DIR.resolve()
    expected = (root / str(user_id) / str(product_id)).resolve()
    target = (root / Path(*parts[1:])).resolve()
    if (not expected.is_relative_to(root) or not target.is_relative_to(expected)
            or target == expected):
        raise InvalidInputError("资料路径不在当前用户的产品目录内")
    return target


def create_product(db, data):
    with write_transaction(db):
        product = Product(**data.model_dump(), user_id=current_user_id(db))
        db.add(product)
    return product


def get_product(db, product_id):
    return get_record(db, Product, product_id)


def list_products(db, *, offset=0, limit=20):
    return list_records(db, Product, offset=offset, limit=limit)


def update_product(db, product_id, data):
    with write_transaction(db):
        product = get_record(db, Product, product_id, lock=True)
        for name, value in data.model_dump(exclude_unset=True).items():
            setattr(product, name, value)
    return product


def delete_product(db, product_id):
    """仅删除没有任务和资料引用的产品，保留业务历史。"""
    with write_transaction(db):
        product = get_record(db, Product, product_id, lock=True)
        if db.scalar(select(Task.id).where(Task.product_id == product_id).limit(1)) is not None:
            raise ConflictError("产品已关联任务，不能删除")
        if db.scalar(select(ProductDocument.id).where(ProductDocument.product_id == product_id).limit(1)) is not None:
            raise ConflictError("请先删除产品资料，再删除产品")
        db.delete(product)


def upload_document(db, product_id, upload):
    """仅存储资料，不执行解析或 RAG 入库；文件名不参与实际保存路径。"""
    # 与产品删除串行，避免校验后产品被另一个请求删除。
    get_record(db, Product, product_id, lock=True)
    filename = Path((upload.filename or "").replace("\\", "/")).name
    suffix = Path(filename).suffix.lower()
    media_types = {".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown",
                   ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    if suffix not in media_types or not filename or len(filename) > 300:
        raise InvalidInputError("资料仅支持 PDF、TXT、MD、DOCX，文件名不能超过 300 字符")
    storage_key = f"uploads/{current_user_id(db)}/{product_id}/{uuid.uuid4().hex}{suffix}"
    target = _upload_path(storage_key, user_id=current_user_id(db), product_id=product_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        size = 0
        with target.open("xb") as output:
            while chunk := upload.file.read(1024 * 1024):
                if size == 0 and suffix == ".pdf" and not chunk.startswith(b"%PDF-"):
                    raise InvalidInputError("文件内容不是有效的 PDF 文件头")
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise InvalidInputError("资料文件不能超过 20 MB")
                output.write(chunk)
        if not size:
            raise InvalidInputError("不能上传空文件")
        if suffix in {".txt", ".md"}:
            try:
                target.read_text(encoding="utf-8-sig")
            except UnicodeError:
                raise InvalidInputError("TXT 和 MD 资料请使用 UTF-8 编码") from None
        elif suffix == ".docx":
            try:
                with ZipFile(target) as archive:
                    archive.getinfo("word/document.xml")
            except (BadZipFile, KeyError):
                raise InvalidInputError("文件不是有效的 DOCX 文档") from None
        with write_transaction(db):
            document = ProductDocument(product_id=product_id, filename=filename,
                storage_key=storage_key, size_bytes=size, content_type=media_types[suffix], status="uploaded")
            db.add(document)
            db.flush()
            document_id = document.id
        # 提交后返回 ID，由 Router 使用归属校验后的查询读取。
        return document_id
    except Exception:
        target.unlink(missing_ok=True)
        raise


def list_documents(db, product_id, *, offset=0, limit=20):
    get_product(db, product_id)
    return list_records(db, ProductDocument, product_id=product_id, offset=offset, limit=limit)


def get_document(db, product_id, document_id):
    get_product(db, product_id)
    document = get_record(db, ProductDocument, document_id)
    if document.product_id != product_id or document.status == "deleting":
        raise NotFoundError("资料不存在")
    return document


def delete_document(db, product_id, document_id):
    """分阶段删除：持久标记、移除文件、删除记录。中途失败可安全重试。"""
    with write_transaction(db):
        get_record(db, Product, product_id, lock=True)
        document = get_record(db, ProductDocument, document_id, lock=True)
        if document.product_id != product_id:
            raise NotFoundError("资料不存在")
        target = _upload_path(document.storage_key, user_id=current_user_id(db), product_id=product_id)
        document.status = "deleting"
    try:
        target.unlink(missing_ok=True)
    except OSError:
        raise ServiceUnavailableError("资料文件暂时无法删除，请稍后重试删除接口") from None
    with write_transaction(db):
        get_record(db, Product, product_id, lock=True)
        # 并发的另一次删除可能已完成，此时也视为成功。
        document = db.scalar(select(ProductDocument).where(
            ProductDocument.id == document_id, ProductDocument.product_id == product_id,
        ).with_for_update().execution_options(populate_existing=True))
        if document is not None:
            db.delete(document)


def document_path(document, *, user_id: int):
    path = _upload_path(document.storage_key, user_id=user_id, product_id=document.product_id)
    if not path.is_file():
        raise NotFoundError("资料文件不存在")
    return path
