import unittest
from unittest.mock import patch

from rag.text_splitter import split_document, split_text
from services.exceptions import InvalidInputError


class TextSplitterTest(unittest.TestCase):
    def split(self, text, **options):
        return split_text(text, user_id=1, product_id=2, document_id=3, **options)

    def test_short_text_and_metadata(self):
        text = "  产品名称：保温杯\n容量：500ml  "
        chunk, = self.split(text)
        self.assertEqual(chunk.text, text.strip())
        self.assertEqual(text[chunk.start_char:chunk.end_char], chunk.text)
        self.assertEqual((chunk.user_id, chunk.product_id, chunk.document_id, chunk.chunk_index), (1, 2, 3, 0))
        self.assertEqual(chunk.metadata["chunk_id"], chunk.chunk_id)
        self.assertEqual(chunk.metadata["product_id"], 2)
        self.assertEqual(len(chunk.document_hash), 64)

    def test_long_unbroken_text_overlap_and_offsets(self):
        text = "abcdefghijklmnopqrstuvwxyz"
        chunks = self.split(text, chunk_size=10, chunk_overlap=3)
        self.assertEqual([(c.start_char, c.end_char) for c in chunks], [(0, 10), (7, 17), (14, 24), (21, 26)])
        self.assertEqual(chunks[0].text[-3:], chunks[1].text[:3])
        self.assertEqual([c.chunk_index for c in chunks], list(range(4)))

    def test_prefers_paragraph_and_sentence_boundary(self):
        text = "第一段产品介绍。\n\n第二段产品参数以及使用方式。第三段说明。"
        chunks = self.split(text, chunk_size=15, chunk_overlap=2)
        self.assertEqual(chunks[0].text, "第一段产品介绍。")
        self.assertTrue(all(len(c.text) <= 15 for c in chunks))

    def test_no_text_loss_for_different_options(self):
        texts = ["甲乙丙丁。戊己庚辛！\n\n壬癸，产品 参数。" * 5,
                 "# Product\n\n- Keep warm\n- 500 ml\nEnglish sentence. " * 4,
                 "abcdef" * 20]
        for text in texts:
            for size, overlap in ((1, 0), (5, 0), (5, 4), (20, 3), (8, 7)):
                with self.subTest(size=size, overlap=overlap):
                    chunks = self.split(text, chunk_size=size, chunk_overlap=overlap)
                    covered = set()
                    for chunk in chunks:
                        self.assertEqual(chunk.text, text[chunk.start_char:chunk.end_char])
                        self.assertTrue(0 < len(chunk.text) <= size)
                        covered.update(range(chunk.start_char, chunk.end_char))
                    self.assertTrue(all(i in covered for i, char in enumerate(text) if not char.isspace()))

    def test_stable_ids_and_document_changes(self):
        chunks = self.split("产品介绍" * 20, chunk_size=15, chunk_overlap=2)
        self.assertEqual(chunks, self.split("产品介绍" * 20, chunk_size=15, chunk_overlap=2))
        changed = self.split("产品介绍" * 20 + "更新", chunk_size=15, chunk_overlap=2)
        self.assertNotEqual(chunks[0].document_hash, changed[0].document_hash)
        self.assertNotEqual(chunks[0].chunk_id, changed[0].chunk_id)
        other = split_text("产品介绍" * 20, user_id=2, product_id=2, document_id=3, chunk_size=15, chunk_overlap=2)
        self.assertNotEqual(chunks[0].chunk_id, other[0].chunk_id)

    def test_empty_and_whitespace_tail(self):
        self.assertEqual(self.split(" \n\t "), [])
        chunks = self.split("a" * 8 + " " * 4, chunk_size=10, chunk_overlap=3)
        self.assertEqual(len(chunks), 1)

    def test_invalid_parameters(self):
        for options in ({"chunk_size": 0}, {"chunk_size": True}, {"chunk_size": 2.5},
                        {"chunk_overlap": -1}, {"chunk_overlap": 800}, {"chunk_overlap": True}):
            with self.subTest(options=options), self.assertRaises(InvalidInputError):
                self.split("text", **options)
        with self.assertRaises(InvalidInputError):
            split_text("text", user_id=0, product_id=2, document_id=3)
        with self.assertRaises(InvalidInputError):
            self.split(None)

    def test_document_entry_uses_authenticated_owner(self):
        class Session:
            info = {"user_id": 7}
        with patch("rag.text_splitter.load_document", return_value="已验证归属的产品资料") as loader:
            chunks = split_document(Session(), product_id=2, document_id=3)
            loader.assert_called_once()
            self.assertEqual(chunks[0].user_id, 7)

    def test_chunk_count_limit(self):
        with patch("rag.text_splitter.MAX_CHUNKS", 1), self.assertRaises(InvalidInputError):
            self.split("abcdefghij", chunk_size=3, chunk_overlap=0)
