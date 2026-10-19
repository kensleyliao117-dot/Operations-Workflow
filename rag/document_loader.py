"""统一读取产品资料，返回供切分使用的纯文本。

支持 PDF、TXT、MD 和 DOCX；切分、Embedding 和 OCR 分别处理。
通过认证后的 Session 校验资料归属，读取过程保留原有数据库状态。
"""
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree

from pypdf import PdfReader
from pypdf.errors import PyPdfError
from sqlalchemy.orm import Session

from config import MAX_UPLOAD_BYTES
from services import product as product_service
from services.common import current_user_id
from services.exceptions import InvalidInputError, NotFoundError, ServiceUnavailableError

MAX_PDF_PAGES = 500
MAX_PDF_PAGE_STREAM_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARACTERS = 2_000_000
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024


def load_document(db: Session, product_id: int, document_id: int) -> str:
    """校验用户/产品归属后读取资料，返回非空文本；失败时抛出业务异常。"""
    document = product_service.get_document(db, product_id, document_id)
    path = product_service.document_path(document, user_id=current_user_id(db))
    suffix = path.suffix.lower()
    if suffix not in {".pdf", ".txt", ".md", ".docx"}:
        raise InvalidInputError("只支持读取 PDF、TXT、MD、DOCX 产品资料")
    raw = _read_bytes(path)
    if suffix == ".pdf":
        text = _read_pdf(raw)
    elif suffix == ".docx":
        text = _read_docx(raw)
    else:
        try:
            # Markdown 保留标题、列表等原始文本结构，不渲染为 HTML。
            text = raw.decode("utf-8-sig")
        except UnicodeError:
            raise InvalidInputError("TXT 和 MD 资料必须使用 UTF-8 编码") from None
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()
    if not text:
        message = "PDF 未提取到文本，扫描件需要先进行 OCR" if suffix == ".pdf" else "资料没有可读取的文本"
        raise InvalidInputError(message)
    if len(text) > MAX_TEXT_CHARACTERS:
        raise InvalidInputError("资料文本过长，请拆分成较小文件后上传")
    return text


def _read_docx(raw: bytes) -> str:
    """读取 DOCX 正文中的段落及表格文字，不执行宏或读取外部资源。"""
    try:
        with ZipFile(BytesIO(raw)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise InvalidInputError("DOCX 正文过大，请拆分后上传")
            with archive.open(info) as stream:
                xml = stream.read(MAX_DOCX_XML_BYTES + 1)
        if len(xml) > MAX_DOCX_XML_BYTES or b"<!DOCTYPE" in xml.upper():
            raise InvalidInputError("DOCX 正文过大或含不支持的 XML 声明")
        root = ElementTree.fromstring(xml)
        namespaces = ("http://schemas.openxmlformats.org/wordprocessingml/2006/main",
                      "http://purl.oclc.org/ooxml/wordprocessingml/main")
        namespace = next((f"{{{ns}}}" for ns in namespaces if root.tag == f"{{{ns}}}document"), None)
        if namespace is None:
            raise InvalidInputError("DOCX 正文结构无效")
        parts = []
        length = 0
        # 表格单元格中的段落也按文档顺序读取。
        for paragraph in root.iter(namespace + "p"):
            text = "".join(
                node.text or "" if node.tag == namespace + "t"
                else "\t" if node.tag == namespace + "tab"
                else "\n" if node.tag in {namespace + "br", namespace + "cr"}
                else ""
                for node in paragraph.iter()
            )
            length += len(text) + 2
            if length > MAX_TEXT_CHARACTERS:
                raise InvalidInputError("资料文本过长，请拆分成较小文件后上传")
            if text.strip():
                parts.append(text.strip())
        return "\n\n".join(parts)
    except (BadZipFile, KeyError, ElementTree.ParseError, RuntimeError, ValueError, OSError):
        raise InvalidInputError("DOCX 解析失败，文件可能损坏、加密或不是有效的 Word 文档") from None


def _read_bytes(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_UPLOAD_BYTES + 1)
    except FileNotFoundError:
        raise NotFoundError("资料文件不存在") from None
    except OSError:
        raise ServiceUnavailableError("资料文件暂时无法读取，请稍后重试") from None
    if len(raw) > MAX_UPLOAD_BYTES:
        raise InvalidInputError("资料文件不能超过 20 MB")
    if not raw:
        raise InvalidInputError("资料文件为空")
    return raw


def _read_pdf(raw: bytes) -> str:
    if not raw.startswith(b"%PDF-"):
        raise InvalidInputError("文件内容不是有效的 PDF")
    try:
        reader = PdfReader(BytesIO(raw), strict=False)
        if reader.is_encrypted:
            raise InvalidInputError("暂不支持加密 PDF，请解密后重新上传")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise InvalidInputError("PDF 超过 500 页，请拆分后上传")
        parts = []
        total_length = 0
        for page in reader.pages:
            contents = page.get_contents()
            if contents is not None and len(contents.get_data()) > MAX_PDF_PAGE_STREAM_BYTES:
                raise InvalidInputError("PDF 单页内容过大，请简化后重新上传")
            page_text = page.extract_text() or ""
            total_length += len(page_text) + 2
            if total_length > MAX_TEXT_CHARACTERS:
                raise InvalidInputError("资料文本过长，请拆分成较小文件后上传")
            parts.append(page_text.strip())
        return "\n\n".join(parts)
    except (PyPdfError, ValueError, TypeError, KeyError, IndexError, RecursionError, NotImplementedError):
        raise InvalidInputError("PDF 解析失败，文件可能损坏或包含不支持的内容") from None
