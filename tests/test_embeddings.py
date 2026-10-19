import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from config import ConfigurationError
from rag.embeddings import EmbeddingError, QwenEmbeddings, embed_chunks, embed_document
from rag.text_splitter import split_text
from rag.token_counter import LocalTokenCounter
from tokenizers import Tokenizer, models, pre_tokenizers, processors
from services.exceptions import InvalidInputError, NotFoundError


class EmbeddingsTest(unittest.TestCase):
    def setUp(self):
        tokenizer_config = patch("config.EMBEDDING_TOKENIZER_PATH", "")
        tokenizer_config.start()
        self.addCleanup(tokenizer_config.stop)
        self.requests = []
        self.custom_handler = None
        self.http = httpx.Client(transport=httpx.MockTransport(self.handler))
        self.addCleanup(self.http.close)
        self.embedder = self.client()

    def client(self, **overrides):
        options = dict(api_key="unit-test-key", base_url="https://embedding.example/compatible-mode/v1",
                       model="text-embedding-v3", dimensions=64, batch_size=10, http_client=self.http)
        options.update(overrides)
        return QwenEmbeddings(**options)

    def handler(self, request):
        self.requests.append(request)
        if self.custom_handler is not None:
            return self.custom_handler(request)
        body = json.loads(request.content)
        # 故意倒序返回，验证 index 与输入片段的关联。
        data = [{"index": i, "embedding": [float(len(text))] * body["dimensions"]}
                for i, text in enumerate(body["input"])]
        return httpx.Response(200, json={"model": body["model"], "data": data[::-1]})

    def test_batches_and_input_order(self):
        texts = ["a" * size for size in range(1, 24)]
        vectors = self.embedder.embed_texts(texts)
        self.assertEqual(len(vectors), 23)
        self.assertEqual([vector[0] for vector in vectors], list(range(1, 24)))
        self.assertEqual([len(json.loads(r.content)["input"]) for r in self.requests], [10, 10, 3])
        self.assertTrue(all(len(vector) == 64 for vector in vectors))
        self.assertEqual(self.requests[0].url.path, "/compatible-mode/v1/embeddings")
        self.assertEqual(self.requests[0].headers["authorization"], "Bearer unit-test-key")

    def test_query_and_chunk_metadata(self):
        self.assertEqual(self.embedder.embed_query("query"), [5.0] * 64)
        chunks = split_text("产品资料。" * 20, user_id=7, product_id=3, document_id=1,
                            chunk_size=20, chunk_overlap=3)
        results = embed_chunks(chunks, embedder=self.embedder)
        self.assertEqual(len(results), len(chunks))
        for result, chunk in zip(results, chunks):
            self.assertIs(result.chunk, chunk)
            self.assertEqual(result.chunk_id, chunk.chunk_id)
            self.assertEqual(result.text, chunk.text)
            self.assertEqual(result.metadata["user_id"], 7)
            self.assertEqual(result.metadata["product_id"], 3)
            self.assertEqual(result.metadata["document_id"], 1)
            self.assertEqual(result.metadata["embedding_model"], "text-embedding-v3")
            self.assertEqual(result.metadata["embedding_dimensions"], 64)

    def test_empty_and_invalid_inputs_make_no_requests(self):
        self.assertEqual(self.embedder.embed_texts([]), [])
        self.assertEqual(embed_chunks([]), [])
        for value in ("single string", [""], ["   "], [123], ["ok"] * 10 + ["界" * 3000]):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(InvalidInputError):
                self.embedder.embed_texts(value)
        self.assertEqual(self.requests, [])

    def test_configuration_validation(self):
        for options in ({"api_key": ""}, {"model": "qwen-plus"}, {"dimensions": 1536},
                        {"batch_size": 11}, {"batch_size": 0}, {"timeout": 0},
                        {"base_url": "http://embedding.example/v1"},
                        {"base_url": "https://user:password@embedding.example/v1"},
                        {"base_url": "https://embedding.example/v1/embeddings"}):
            with self.subTest(options=options), self.assertRaises(ConfigurationError):
                self.client(**options)
        self.client(model="text-embedding-v4", dimensions=2048)

    def test_authentication_and_redirect_do_not_retry(self):
        for status in (401, 403, 400, 302):
            self.requests.clear()
            self.custom_handler = lambda request, code=status: httpx.Response(
                code, headers={"Location": "https://unexpected.example"}, text="private upstream details")
            with self.assertRaises(EmbeddingError) as caught:
                self.embedder.embed_texts(["private product text"])
            self.assertEqual(len(self.requests), 1)
            self.assertNotIn("private", str(caught.exception))

    def test_rate_limit_retry_and_timeout(self):
        def rate_limit(request):
            if len(self.requests) == 1:
                return httpx.Response(429)
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1] * 64}]})
        self.custom_handler = rate_limit
        with patch("rag.embeddings.time.sleep"):
            self.assertEqual(self.embedder.embed_texts(["text"]), [[1.0] * 64])
        self.assertEqual(len(self.requests), 2)
        self.requests.clear()

        def timeout(request):
            raise httpx.ReadTimeout("private upstream details", request=request)
        self.custom_handler = timeout
        with patch("rag.embeddings.time.sleep"), self.assertRaises(EmbeddingError) as caught:
            self.embedder.embed_query("private product text")
        self.assertEqual(len(self.requests), 3)
        self.assertNotIn("private", str(caught.exception))

    def test_malformed_responses_and_invalid_values(self):
        valid = {"index": 0, "embedding": [0.1] * 64}
        payloads = [None, {}, {"data": []}, {"data": [None]},
                    {"data": [{**valid, "index": 1}]}, {"data": [{**valid, "index": True}]},
                    {"data": [{**valid, "embedding": [0.1]}]},
                    {"data": [{**valid, "embedding": [float("nan")] * 64}]},
                    {"data": [{**valid, "embedding": [True] * 64}]},
                    {"data": [valid], "model": "different-model"}]
        for payload in payloads:
            self.custom_handler = lambda request, body=payload: httpx.Response(200, content=json.dumps(body))
            with self.subTest(payload_type=type(payload).__name__), self.assertRaises(EmbeddingError):
                self.embedder.embed_query("text")
        self.custom_handler = lambda request: httpx.Response(200, text="not json")
        with self.assertRaises(EmbeddingError):
            self.embedder.embed_query("text")

    def test_document_owner_checked_before_network(self):
        with patch("rag.embeddings.split_document", side_effect=NotFoundError("not found")):
            with self.assertRaises(NotFoundError):
                embed_document(object(), 3, 1, embedder=self.embedder)
        self.assertEqual(self.requests, [])

    def test_borrowed_client_is_not_closed(self):
        with self.embedder:
            self.embedder.embed_query("query")
        self.assertFalse(self.http.is_closed)

    def make_test_tokenizer(self):
        temporary = TemporaryDirectory(prefix="embedding-tokenizer-test-")
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "tokenizer.json"
        # 仅用于测试计数机制的微型词表，不是千问模型词表。
        tokenizer = Tokenizer(models.WordLevel(
            {"[UNK]": 0, "[CLS]": 1, "[SEP]": 2, "hello": 3, "world": 4}, unk_token="[UNK]"))
        tokenizer.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
        tokenizer.post_processor = processors.TemplateProcessing(
            single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 1), ("[SEP]", 2)])
        tokenizer.enable_truncation(max_length=3)
        tokenizer.enable_padding(length=30)
        tokenizer.save(str(path))
        return path

    def test_local_tokenizer_counts_without_truncation_or_padding(self):
        counter = LocalTokenCounter(self.make_test_tokenizer(), model="text-embedding-v3")
        self.assertEqual(counter.count_tokens("hello world hello"), 5)
        self.assertTrue(counter.source.startswith("tokenizer-sha256:"))
        client = self.client(token_counter=counter)
        self.assertEqual(client.count_tokens("hello world"), 4)
        # 字节超过原 8000 上限，但实际 Token 远低于模型上限，应允许。
        text = "hello " * 1800
        vectors = client.embed_texts([text])
        self.assertEqual(len(vectors), 1)
        self.assertEqual(client.last_usage.input_token_counts, (1802,))

    def test_token_limit_boundaries_before_any_request(self):
        counter = LocalTokenCounter(self.make_test_tokenizer(), model="text-embedding-v3")
        client = self.client(token_counter=counter)
        client.embed_texts(["hello " * 8190])  # 加上两个特殊 Token，正好 8192。
        self.requests.clear()
        with self.assertRaises(InvalidInputError):
            client.embed_texts(["hello"] * 10 + ["hello " * 8191])
        self.assertEqual(self.requests, [])
        self.assertIsNone(client.last_usage)

    def test_tokenizer_model_and_file_validation(self):
        counter = LocalTokenCounter(self.make_test_tokenizer(), model="text-embedding-v4")
        with self.assertRaises(ConfigurationError):
            self.client(token_counter=counter)
        with self.assertRaises(ConfigurationError):
            LocalTokenCounter("missing-tokenizer.json", model="text-embedding-v3")
        with self.assertRaises(ConfigurationError):
            self.embedder.count_tokens("hello")
        with patch("config.EMBEDDING_TOKENIZER_PATH", str(self.make_test_tokenizer())), patch(
                "config.EMBEDDING_TOKENIZER_MODEL", "text-embedding-v3"):
            self.assertEqual(self.client().count_tokens("hello"), 3)

    def test_provider_usage_is_recorded_not_guessed(self):
        def with_usage(request):
            body = json.loads(request.content)
            return httpx.Response(200, json={
                "data": [{"index": i, "embedding": [0.1] * 64} for i in range(len(body["input"]))],
                "usage": {"total_tokens": len(body["input"]) * 7},
            })
        self.custom_handler = with_usage
        self.embedder.embed_texts(["hello"] * 23)
        self.assertEqual(self.embedder.last_usage.provider_total_tokens, 161)
        self.assertIsNone(self.embedder.last_usage.input_token_counts)
        self.custom_handler = None
        self.embedder.embed_query("hello")
        self.assertIsNone(self.embedder.last_usage.provider_total_tokens)

    def test_chunk_metadata_includes_tokenizer_count_and_source(self):
        counter = LocalTokenCounter(self.make_test_tokenizer(), model="text-embedding-v3")
        chunks = split_text("hello world", user_id=1, product_id=2, document_id=3)
        item, = embed_chunks(chunks, embedder=self.client(token_counter=counter))
        self.assertEqual(item.token_count, 4)
        self.assertEqual(item.metadata["token_count_source"], counter.source)

    def test_invalid_provider_usage_rejected(self):
        for invalid in (True, -1, "10"):
            self.custom_handler = lambda request, value=invalid: httpx.Response(200, json={
                "data": [{"index": 0, "embedding": [0.1] * 64}], "usage": {"total_tokens": value}})
            with self.assertRaises(EmbeddingError):
                self.embedder.embed_query("hello")
