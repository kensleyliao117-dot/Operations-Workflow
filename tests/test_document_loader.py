from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models.product import Product, ProductDocument
from models.user import Users
from rag.document_loader import load_document
from rag.text_splitter import split_document
from services.exceptions import AuthenticationError, InvalidInputError, NotFoundError


def pdf_bytes(*pages, encrypted=False):
    """构建内存解析样本，不产生交付用 PDF 文件。"""
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                NameObject("/Subtype"): NameObject("/Type1"),
                                NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
            DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 250 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("test-password")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class DocumentLoaderTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="operations-loader-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = patch("services.product.UPLOAD_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        user = Users(username="loader-owner")
        other = Users(username="other-owner")
        self.db.add_all([user, other])
        self.db.flush()
        self.user_id, self.other_id = user.id, other.id
        product = Product(user_id=user.id, name="Product")
        self.db.add(product)
        self.db.flush()
        self.product_id = product.id
        self.db.commit()
        self.db.info["user_id"] = self.user_id

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def document(self, name, body):
        key = f"uploads/{self.user_id}/{self.product_id}/{name}"
        path = self.root / key.removeprefix("uploads/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        doc = ProductDocument(product_id=self.product_id, filename=name, storage_key=key,
                              size_bytes=len(body), content_type="application/octet-stream", status="uploaded")
        self.db.add(doc)
        self.db.commit()
        return doc.id

    def test_txt_and_markdown(self):
        doc_id = self.document("manual.txt", "\ufeff 产品资料\r\n保温杯 \r\n".encode("utf-8"))
        self.assertEqual(load_document(self.db, self.product_id, doc_id), "产品资料\n保温杯")
        doc_id = self.document("manual.md", "# 产品\n\n- 容量 500ml".encode("utf-8"))
        self.assertEqual(load_document(self.db, self.product_id, doc_id), "# 产品\n\n- 容量 500ml")

    def test_pdf_text_pages(self):
        doc_id = self.document("manual.pdf", pdf_bytes("First page", "Second page"))
        self.assertEqual(load_document(self.db, self.product_id, doc_id), "First page\n\nSecond page")

    def test_docx_paragraph_and_table_text(self):
        xml = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body><w:p><w:r><w:t>产品介绍</w:t></w:r></w:p>
        <w:tbl><w:tr><w:tc><w:p><w:r><w:t>耳机参数</w:t><w:tab/><w:t>蓝牙</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
        <w:p><w:r><w:t>最后一段</w:t><w:br/><w:t>续行</w:t></w:r></w:p></w:body></w:document>'''
        output = BytesIO()
        with ZipFile(output, "w") as archive:
            archive.writestr("word/document.xml", xml.encode("utf-8"))
        doc_id = self.document("manual.docx", output.getvalue())
        text = load_document(self.db, self.product_id, doc_id)
        self.assertEqual(text, "产品介绍\n\n耳机参数\t蓝牙\n\n最后一段\n续行")
        with patch("rag.document_loader.MAX_DOCX_XML_BYTES", 10), self.assertRaises(InvalidInputError):
            load_document(self.db, self.product_id, doc_id)

    def test_invalid_docx_rejected(self):
        for index, payload in enumerate((b"invalid zip", b"<broken", b"<!DOCTYPE test><test/>", b"<test/>")):
            if index:
                output = BytesIO()
                with ZipFile(output, "w") as archive:
                    archive.writestr("word/document.xml", payload)
                payload = output.getvalue()
            doc_id = self.document(f"bad-{index}.docx", payload)
            with self.assertRaises(InvalidInputError):
                load_document(self.db, self.product_id, doc_id)

    def test_invalid_empty_and_encrypted(self):
        for index, (suffix, body) in enumerate([
            (".txt", b""), (".txt", b" \r\n"), (".txt", b"\xff"),
            (".pdf", b"invalid"), (".pdf", b"%PDF-1.7\ninvalid"),
            (".pdf", pdf_bytes("")), (".pdf", pdf_bytes("Secret", encrypted=True)),
            (".exe", b"invalid"),
        ]):
            with self.subTest(index=index):
                doc_id = self.document(f"bad-{index}{suffix}", body)
                with self.assertRaises(InvalidInputError):
                    load_document(self.db, self.product_id, doc_id)

    def test_owner_and_product_isolation(self):
        doc_id = self.document("private.txt", b"private")
        self.db.info["user_id"] = self.other_id
        with self.assertRaises(NotFoundError):
            load_document(self.db, self.product_id, doc_id)
        self.db.info.clear()
        with self.assertRaises(AuthenticationError):
            load_document(self.db, self.product_id, doc_id)
        self.db.info["user_id"] = self.user_id
        product = Product(user_id=self.user_id, name="Another")
        self.db.add(product)
        self.db.commit()
        with self.assertRaises(NotFoundError):
            load_document(self.db, product.id, doc_id)

    def test_missing_and_deleting(self):
        doc_id = self.document("deleted.txt", b"text")
        document = self.db.get(ProductDocument, doc_id)
        (self.root / document.storage_key.removeprefix("uploads/")).unlink()
        with self.assertRaises(NotFoundError):
            load_document(self.db, self.product_id, doc_id)
        doc_id = self.document("deleting.txt", b"text")
        self.db.get(ProductDocument, doc_id).status = "deleting"
        self.db.commit()
        with self.assertRaises(NotFoundError):
            load_document(self.db, self.product_id, doc_id)

    def test_outside_product_path_rejected(self):
        doc_id = self.document("original.txt", b"text")
        (self.root / "outside.txt").write_text("private", encoding="utf-8")
        self.db.get(ProductDocument, doc_id).storage_key = "outside.txt"
        self.db.commit()
        with self.assertRaises(InvalidInputError):
            load_document(self.db, self.product_id, doc_id)

    def test_file_and_pdf_page_limits(self):
        doc_id = self.document("large.txt", b"12345")
        with patch("rag.document_loader.MAX_UPLOAD_BYTES", 4), self.assertRaises(InvalidInputError):
            load_document(self.db, self.product_id, doc_id)
        doc_id = self.document("pages.pdf", pdf_bytes("Page1", "Page2"))
        with patch("rag.document_loader.MAX_PDF_PAGES", 1), self.assertRaises(InvalidInputError):
            load_document(self.db, self.product_id, doc_id)

    def test_split_uploaded_document_preserves_owner_and_source(self):
        doc_id = self.document("split.md", ("产品介绍。\n\n容量 500ml，适合通勤。\n" * 4).encode("utf-8"))
        chunks = split_document(self.db, self.product_id, doc_id, chunk_size=25, chunk_overlap=5)
        self.assertGreater(len(chunks), 1)
        original = load_document(self.db, self.product_id, doc_id)
        for chunk in chunks:
            self.assertEqual((chunk.user_id, chunk.product_id, chunk.document_id),
                             (self.user_id, self.product_id, doc_id))
            self.assertEqual(chunk.text, original[chunk.start_char:chunk.end_char])
        self.db.info["user_id"] = self.other_id
        with self.assertRaises(NotFoundError):
            split_document(self.db, self.product_id, doc_id)
