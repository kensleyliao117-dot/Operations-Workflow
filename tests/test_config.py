import os
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from config import (
    BASE_DIR, ConfigurationError, chrome_cdp_url, validate_database_url,
    load_cors_origins, require_jwt_secret, load_upload_dir,
)


class ConfigurationTest(unittest.TestCase):
    def embedding_configuration(self, **overrides):
        values = {"DATABASE_URL": "postgresql://localhost/config_test", **overrides}
        with patch.dict(os.environ, values, clear=True), patch("dotenv.load_dotenv"):
            return runpy.run_path(str(BASE_DIR / "config.py"))

    def test_embedding_defaults_do_not_follow_chat_configuration(self):
        values = self.embedding_configuration(DASHSCOPE_MODEL="text-embedding-v4",
            DASHSCOPE_URL="https://legacy-provider.example/v1")
        self.assertEqual(values["EMBEDDING_MODEL"], "text-embedding-v3")
        self.assertEqual(values["EMBEDDING_DIMENSIONS"], 1024)
        self.assertEqual(values["EMBEDDING_BASE_URL"], "https://dashscope.aliyuncs.com/compatible-mode/v1")

    def test_explicit_embedding_configuration_overrides_defaults(self):
        values = self.embedding_configuration(EMBEDDING_MODEL="text-embedding-v4",
            EMBEDDING_DIMENSIONS="1536", EMBEDDING_BASE_URL=" https://embedding.example/v1/ ")
        self.assertEqual(values["EMBEDDING_MODEL"], "text-embedding-v4")
        self.assertEqual(values["EMBEDDING_DIMENSIONS"], 1536)
        self.assertEqual(values["EMBEDDING_BASE_URL"], "https://embedding.example/v1")

    def test_invalid_embedding_configuration_fails_during_loading(self):
        for options in [{"EMBEDDING_MODEL": "qwen-plus"}, {"EMBEDDING_DIMENSIONS": "1536"},
                        {"EMBEDDING_DIMENSIONS": "invalid"}, {"EMBEDDING_BATCH_SIZE": "11"},
                        {"EMBEDDING_BASE_URL": "http://embedding.example/v1"},
                        {"EMBEDDING_BASE_URL": "https://secret-user:secret-key@embedding.example/v1"}]:
            with self.subTest(options=tuple(options)):
                # runpy 会创建独立的 ConfigurationError 类，以共同父类和配置字段验证。
                with self.assertRaisesRegex(RuntimeError, "EMBEDDING_") as caught:
                    self.embedding_configuration(**options)
                self.assertNotIn("secret-user", str(caught.exception))
                self.assertNotIn("secret-key", str(caught.exception))

    def test_upload_directory_configuration(self):
        with patch.dict(os.environ, {"UPLOAD_DIR": ""}):
            self.assertEqual(load_upload_dir(), (BASE_DIR / "storage" / "uploads").resolve())
        with patch.dict(os.environ, {"UPLOAD_DIR": "data/product-documents"}):
            self.assertEqual(load_upload_dir(), (BASE_DIR / "data" / "product-documents").resolve())
        with TemporaryDirectory(prefix="upload-config-test-") as directory:
            absolute = Path(directory) / "uploads"
            with patch.dict(os.environ, {"UPLOAD_DIR": str(absolute)}):
                self.assertEqual(load_upload_dir(), absolute.resolve())
            file = Path(directory) / "file.txt"
            file.write_text("not a directory", encoding="utf-8")
            with patch.dict(os.environ, {"UPLOAD_DIR": str(file)}), self.assertRaises(ConfigurationError):
                load_upload_dir()

    def test_jwt_key_is_required(self):
        for key in ("", "short"):
            with patch("config.JWT_SECRET_KEY", key), self.assertRaises(ConfigurationError):
                require_jwt_secret()
    def test_missing_and_invalid_database_url(self):
        for value in (None, "", "  ", "invalid", "postgresql://localhost", "sqlite:///test.db",
                      "postgresql://localhost:invalid/test"):
            with self.subTest(value=value), self.assertRaises(ConfigurationError):
                validate_database_url(value)
        self.assertEqual(validate_database_url(" postgresql://localhost/test "),
                         "postgresql://localhost/test")

    def test_dotenv_path_and_clear_startup_error(self):
        path = Path(__file__).resolve().parents[1] / "config.py"
        with patch.dict(os.environ, {}, clear=True), patch("dotenv.load_dotenv") as loader:
            with self.assertRaisesRegex(RuntimeError, "缺少 DATABASE_URL"):
                runpy.run_path(str(path))
            loader.assert_called_once_with(path.parent / ".env")

    def test_cors_configuration(self):
        with patch.dict(os.environ, {"CORS_ORIGINS": "http://localhost:5173/, http://127.0.0.1:5173"}):
            self.assertEqual(load_cors_origins(), ["http://localhost:5173", "http://127.0.0.1:5173"])
        with patch.dict(os.environ, {"CORS_ORIGINS": "*"}):
            with self.assertRaises(ConfigurationError):
                load_cors_origins()

    def test_chrome_configuration_and_safe_endpoint_validation(self):
        values = self.embedding_configuration(CHROME_CDP_URL=" http://127.0.0.1:9333/ ",
                                              CHROME_CDP_TIMEOUT_MS="5000", CHROME_PAGE_TIMEOUT_MS="15000")
        self.assertEqual(values["CHROME_CDP_URL"], "http://127.0.0.1:9333")
        self.assertEqual(values["CHROME_CDP_TIMEOUT_MS"], 5000)
        self.assertEqual(values["CHROME_PAGE_TIMEOUT_MS"], 15000)
        self.assertEqual(chrome_cdp_url("ws://localhost:9222/devtools/browser/test"),
                         "ws://localhost:9222/devtools/browser/test")
        for value in ("", None, "ftp://localhost:9222", "http://localhost:bad", "http://bad host:9222",
                      "http://private:secret@localhost:9222", "http://localhost:9222/?key=secret",
                      "http://localhost:9222/json/version", "ws://localhost:9222/devtools/page/test",
                      "ws://localhost:9222/devtools/browser/"):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ConfigurationError) as caught:
                chrome_cdp_url(value)
            self.assertNotIn("secret", str(caught.exception))
        for options in ({"CHROME_CDP_TIMEOUT_MS": "0"}, {"CHROME_PAGE_TIMEOUT_MS": "invalid"}):
            with self.assertRaisesRegex(RuntimeError, "CHROME_"):
                self.embedding_configuration(**options)
