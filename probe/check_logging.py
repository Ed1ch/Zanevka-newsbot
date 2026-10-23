"""Offline logging checks: python probe/check_logging.py (no pytest required).

Uses a placeholder config and mocks IMAP/WordPress. No real mail or posts are
changed. requests may be absent in the probe environment; HTTP is forbidden
either way. Production still uses the dependencies in requirements.txt.
"""

from contextlib import ExitStack
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("config", ROOT / "config.example.py")
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)
sys.modules["config"] = config


def forbidden_http(*args, **kwargs):
    raise AssertionError("Network access is forbidden in the logging probe")


if importlib.util.find_spec("requests") is None:
    requests = types.ModuleType("requests")
    sys.modules["requests"] = requests
else:
    import requests
requests.get = requests.post = forbidden_http

import log
import mail_downloader as mail
import main
import prelaunch

REAL_DOWNLOAD_WITH_RETRIES = mail.download_with_retries


HEADER = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} "
                    r"\[(INFO|OK|WARNING|ERROR)\] (.*)$")


def blocks(text):
    result = []
    for line in text.splitlines():
        if not line:
            continue
        match = HEADER.fullmatch(line)
        if match:
            result.append({"level": match[1], "title": match[2], "details": []})
        else:
            if not result or not line.startswith("\t"):
                raise AssertionError(f"Invalid block continuation: {line!r}")
            result[-1]["details"].append(line[1:])
    return result


class LoggingChecks(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.stack.close)
        self.addCleanup(log.close)
        self.root = Path(self.temp.name)
        self.flags = self.root / "flags"
        self.flags.mkdir()
        self.logs = self.root / "journals"
        self.console = io.StringIO()
        self.stack.enter_context(patch("sys.stdout", self.console))
        self.stack.enter_context(patch.object(config, "LOG_DIR", str(self.logs)))
        self.stack.enter_context(patch.object(config, "LOG_RETENTION_DAYS", 30))
        self.stack.enter_context(patch.object(config, "LOG_MAX_TOTAL_MB", 200))
        self.stack.enter_context(patch.object(main, "OUTPUT_DIR", str(self.root / "images")))
        self.stack.enter_context(patch.object(main, "IMAP_OUTPUT_DIR", str(self.root / "eml")))
        self.stack.enter_context(patch.object(prelaunch, "LOCK_FILE", self.flags / ".main.lock"))
        self.stack.enter_context(patch.object(mail, "IMAP_RETRY_DELAY", 0))
        self.stack.enter_context(patch.object(prelaunch, "DELAYED_PUBLISH_FILE", self.flags / "delayed.publish"))
        self.connection = MagicMock()
        self.connection.uid.side_effect = lambda *args: (
            ("OK", [b"25 (FLAGS (\\Seen))"]) if args[0] == "fetch" else ("OK", [])
        )
        self.connection.expunge.return_value = ("OK", [])
        self.stack.enter_context(patch.object(mail, "connect_mail", return_value=self.connection))

    def text(self):
        paths = list(self.logs.glob("*.txt"))
        self.assertEqual(len(paths), 1)
        text = paths[0].read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"))
        blocks(text)
        return text

    def prepare_pipeline(self, counts=(2,), post_side_effect=None):
        found = [(str(25 + i), "ЗГП") for i in range(len(counts))]
        self.stack.enter_context(patch.object(mail, "search_unseen_news", return_value=found))

        def download(uid):
            # This assertion is at the actual download boundary, not after main
            # receives the result: a failed download must already have a journal.
            self.assertTrue(log.current_path().is_file())
            self.assertIn("ПРОВЕРКА INBOX", log.current_path().read_text(encoding="utf-8"))
            directory = self.root / "eml"
            directory.mkdir(exist_ok=True)
            path = directory / f"ЗГП_{uid}.eml"
            path.write_text("Offline EML fixture", encoding="utf-8")
            log.detail(f"Письмо скачано: {path}")
            return str(path)

        self.download = self.stack.enter_context(
            patch.object(mail, "download_with_retries", side_effect=download))
        batches = []
        offset = 0
        for count in counts:
            batches.append([{"title": f"Новость {i + 1}", "document_filename": f"документ{i}.docx",
                             "content": "Текст", "excerpt": "Текст", "featured_image": "fixture.jpg"}
                            for i in range(offset, offset + count)])
            offset += count
        payloads = [{"documents": [{"filename": item["document_filename"]} for item in batch],
                     "images": [{"filename": item["document_filename"].replace(".docx", ".jpg")}
                                for item in batch]} for batch in batches]
        self.stack.enter_context(patch.object(main, "extract_attachments", side_effect=payloads))
        self.stack.enter_context(patch.object(main, "build_news", return_value=[n for b in batches for n in b]))
        self.stack.enter_context(patch.object(main, "process_images"))
        self.stack.enter_context(patch.object(main, "extract_vk_links", return_value=[]))
        self.docx_links = self.stack.enter_context(
            patch.object(main, "extract_vk_links_from_docx", return_value=[]))
        self.posts = self.stack.enter_context(patch.object(main, "create_post",
            side_effect=post_side_effect or [
                {"id": i, "link": f"https://example.invalid/news/{i}", "status": "draft"}
                for i in range(1, sum(counts) + 1)]))
        return batches

    def test_empty_check_has_no_file_directory_or_ok_summary(self):
        self.stack.enter_context(patch.object(mail, "search_unseen_news", return_value=[]))
        prelaunch.main()
        self.assertFalse(self.logs.exists())
        self.assertNotIn("[OK]", self.console.getvalue())
        self.assertIn("Новых писем нет", self.console.getvalue())

    def test_multiple_emails_share_one_live_utf8_file(self):
        self.prepare_pipeline((2, 3))
        prelaunch.main()
        text = self.text()
        self.assertEqual(self.posts.call_count, 5)
        self.assertEqual(text.count("[OK]"), 1)
        self.assertIn("Успешно обработано писем: 2", text)
        self.assertIn("как SEEN после ошибок: 0", text)
        self.assertEqual(sum("ПУБЛИКАЦИЯ" in b["title"] for b in blocks(text)), 1)
        self.assertFalse(list((self.root / "eml").iterdir()))
        self.assertIsNone(log._file_handler)
        self.assertFalse(log._logger.handlers)
        path = next(self.logs.glob("*.txt"))
        path.rename(path.with_suffix(".closed"))  # Also checks closed handles on Windows.

    def test_item_failure_continues_and_produces_warning_summary(self):
        self.prepare_pipeline((2,), [RuntimeError("WP fixture failure"),
                                   {"id": 2, "link": "https://example.invalid/2", "status": "draft"}])
        prelaunch.main()
        text = self.text()
        self.assertEqual(self.posts.call_count, 2)
        self.assertIn("Переходим к следующей новости", text)
        self.assertIn("RuntimeError: WP fixture failure", text)
        self.assertNotIn("[ERROR]", text)
        self.assertEqual(blocks(text)[-1]["level"], "WARNING")
        self.assertIn("как SEEN после ошибок: 1", text)
        self.assertFalse(list((self.root / "eml").iterdir()))

    def test_fatal_mail_error_is_saved_before_any_matching_email(self):
        self.stack.enter_context(patch.object(mail, "search_unseen_news",
                                             side_effect=OSError("IMAP unavailable")))
        with self.assertRaisesRegex(OSError, "IMAP unavailable"):
            prelaunch.main()
        text = self.text()
        self.assertEqual(text.count("[ERROR]"), 1)
        self.assertNotIn("[OK]", text)
        self.assertIn("OSError: IMAP unavailable", text)
        lock = prelaunch.acquire_process_lock()
        self.assertIsNotNone(lock)
        prelaunch.release_process_lock(lock)

    def test_duplicate_run_is_silent(self):
        first_lock = prelaunch.acquire_process_lock()
        try:
            prelaunch.main()
            self.assertEqual(self.console.getvalue(), "")
            self.assertFalse(self.logs.exists())
        finally:
            prelaunch.release_process_lock(first_lock)

    def test_exhausted_download_is_counted_without_any_publication(self):
        self.stack.enter_context(patch.object(mail, "search_unseen_news",
                                             return_value=[("25", "ЗГП")]))
        self.stack.enter_context(patch.object(mail, "download_by_uid",
                                             side_effect=OSError("download fixture failure")))
        self.stack.enter_context(patch.object(mail.time, "sleep"))
        posts = self.stack.enter_context(patch.object(main, "create_post"))
        with self.assertRaisesRegex(RuntimeError, "не удалось скачать"):
            prelaunch.main()
        text = self.text()
        posts.assert_not_called()
        self.assertEqual(blocks(text)[-1]["level"], "ERROR")
        self.assertIn("Партия остановлена", text)
        self.assertFalse(prelaunch.DELAYED_PUBLISH_FILE.exists())
        self.assertNotIn("[OK]", text)

    def test_failed_download_stops_before_next_email(self):
        self.prepare_pipeline((1,))
        self.stack.enter_context(patch.object(mail, "search_unseen_news",
                                             return_value=[("24", "ЗГП"), ("25", "ЗГП")]))
        self.download.side_effect = lambda uid: None
        with self.assertRaisesRegex(RuntimeError, "не удалось скачать"):
            prelaunch.main()
        self.download.assert_called_once_with("24")
        self.posts.assert_not_called()
        self.assertEqual(blocks(self.text())[-1]["level"], "ERROR")

    def test_vk_failure_resumes_info_and_still_creates_post(self):
        self.prepare_pipeline((1,))
        self.docx_links.return_value = [{"document": "документ0.docx",
                                       "url": "https://vk.ru/wall-1_2"}]
        self.stack.enter_context(patch.object(main, "get_vk_video_from_post",
                                             side_effect=RuntimeError("VK fixture failure")))
        prelaunch.main()
        parsed = blocks(self.text())
        self.posts.assert_called_once()
        continued = next(b for b in parsed if b["title"].startswith("ПРОДОЛЖЕНИЕ НОВОСТИ"))
        self.assertEqual(continued["level"], "INFO")
        self.assertIn("[3] Создание записи...", continued["details"])
        self.assertEqual(parsed[-1]["level"], "OK")

    def test_video_url_is_logged_without_embed_markup(self):
        self.prepare_pipeline((1,))
        self.docx_links.return_value = [{"document": "документ0.docx",
                                       "url": "https://vk.ru/wall-1_2"}]
        self.stack.enter_context(patch.object(main, "get_vk_video_from_post",
            return_value={"player": "https://example.invalid/player?a=1&b=2"}))
        prelaunch.main()
        text = self.text()
        self.assertIn('\tVIDEO URL: https://example.invalid/player?a=1&b=2\n', text)
        self.assertNotIn('EMBED:', text)
        self.assertNotIn('<iframe', text)
        self.assertIn('src="https://example.invalid/player?a=1&amp;b=2"',
                      self.posts.call_args.args[0]["vk_embed"])
        self.assertEqual(blocks(text)[-1]["level"], "OK")

    def test_mail_finalize_failure_is_fatal_and_does_not_claim_ok(self):
        self.prepare_pipeline((1,))
        self.stack.enter_context(patch.object(main, "mark_email_processed",
                                             side_effect=OSError("mail finalize failed")))
        with self.assertRaisesRegex(OSError, "mail finalize failed"):
            prelaunch.main()
        text = self.text()
        self.assertEqual(text.count("[ERROR]"), 1)
        self.assertNotIn("[OK]", text)
        self.assertFalse(list((self.root / "eml").iterdir()))

    def test_cannot_create_journal_stops_before_download(self):
        self.logs.write_text("This is a file, not a directory", encoding="utf-8")
        self.stack.enter_context(patch.object(mail, "search_unseen_news", return_value=[("25", "ЗГП")]))
        download = self.stack.enter_context(patch.object(mail, "download_with_retries"))
        with self.assertRaises(OSError):
            prelaunch.main()
        download.assert_not_called()
        self.assertIn("[ERROR]", self.console.getvalue())
        self.assertFalse(log._logger.handlers)

    def test_cleanups_both_run_and_release_lock_on_fatal_error(self):
        self.stack.enter_context(patch.object(main, "run_pipeline", side_effect=RuntimeError("pipeline failed")))
        clean_images = self.stack.enter_context(patch.object(main, "cleanup_working_files",
                                                           side_effect=OSError("cleanup failed")))
        clean_eml = self.stack.enter_context(patch.object(main, "cleanup_downloaded_eml"))
        with self.assertRaisesRegex(OSError, "cleanup failed"):
            prelaunch.main()
        clean_images.assert_called_once_with(None)
        clean_eml.assert_called_once_with()
        text = self.text()
        self.assertIn("pipeline failed", text)
        self.assertIn("cleanup failed", text)
        lock = prelaunch.acquire_process_lock()
        self.assertIsNotNone(lock)
        prelaunch.release_process_lock(lock)

    def test_multiline_prefix_and_transport_roundtrip(self):
        log.setup()
        log.block("ПРОВЕРКА INBOX")
        path = log.activate()
        log.detail("EMBED: <iframe>\n2026-10-21 00:00:00 [ERROR] Это подробность")
        # The file is readable before close(), proving per-message flush.
        text = path.read_text(encoding="utf-8")
        self.assertEqual([b["title"] for b in blocks(text)], ["ПРОВЕРКА INBOX"])
        self.assertIn("\t2026-10-21", text)
        self.assertEqual(json.loads(json.dumps(text, ensure_ascii=False)), text)
        raw = path.read_bytes()
        pending = b""
        decoded = []
        for byte in raw:
            pending += bytes([byte])
            if b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                decoded.append(line.removesuffix(b"\r").decode("utf-8"))
        self.assertFalse(pending)
        self.assertEqual("\n".join(decoded) + "\n", text)

    def test_configured_credentials_are_redacted(self):
        with patch.object(config, "IMAP_PASSWORD", "secret-imap-value"):
            log.setup()
            log.block("Failure secret-imap-value", level="ERROR")
            log.detail("secret-imap-value\nsecret-imap-value")
        text = self.text()
        self.assertNotIn("secret-imap-value", text)
        self.assertIn("[REDACTED]", text)

    def test_old_config_defaults_and_relative_directory_ignore_cwd(self):
        old = {name: getattr(config, name) for name in
               ("LOG_DIR", "LOG_RETENTION_DAYS", "LOG_MAX_TOTAL_MB")}
        try:
            for name in old:
                delattr(config, name)
            with patch.object(log, "_ROOT", self.root):
                log.setup()
                log.block("Проверка")
                path = log.activate()
                self.assertEqual(path.parent, self.root / "logs")
                self.assertTrue(path.is_file())
        finally:
            for name, value in old.items():
                setattr(config, name, value)

    def test_unique_files_and_repeated_setup_do_not_duplicate_handlers(self):
        log.setup()
        log.block("Первый")
        first = log.activate()
        log.detail("Одна строка")
        log.setup()
        log.block("Второй")
        second = log.activate()
        log.detail("Другая строка")
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_text(encoding="utf-8").count("Одна строка"), 1)
        self.assertEqual(second.read_text(encoding="utf-8").count("Другая строка"), 1)

    def test_age_size_retention_and_unrelated_file_protection(self):
        self.logs.mkdir()
        names = [f"newsbot_20260101_000000_000000_{i:012x}.txt" for i in range(4)]
        paths = [self.logs / name for name in names]
        for i, path in enumerate(paths):
            path.write_bytes(b"x" * 400)
            stamp = time.time() - (31 * 86400 if i == 0 else (4 - i) * 10)
            os.utime(path, (stamp, stamp))
        unrelated = self.logs / "important.txt"
        unrelated.write_text("keep", encoding="utf-8")
        log.setup(max_total_mb=0.001)
        self.assertFalse(paths[0].exists())
        self.assertFalse(paths[1].exists())
        self.assertTrue(paths[2].exists())
        self.assertTrue(paths[3].exists())
        self.assertTrue(unrelated.exists())

    def test_active_journal_is_not_deleted_by_retention(self):
        log.setup(max_total_mb=0.001)
        log.block("Большой активный журнал")
        path = log.activate()
        log.detail("x" * 2000)
        log._prune()
        self.assertTrue(path.is_file())
        self.assertIn("x" * 2000, path.read_text(encoding="utf-8"))

    def test_image_upload_deduplication_is_preserved(self):
        news = {"featured_image": "same.jpg", "gallery_images": ["same.jpg", "other.jpg"]}
        with patch.object(main, "upload_media", side_effect=[{"wp_id": 1}, {"wp_id": 2}]) as upload:
            main.process_images(news)
        self.assertEqual(upload.call_count, 2)
        self.assertEqual(news["featured_media_id"], 1)
        self.assertEqual(news["gallery_media_ids"], [1, 2])

    def test_journal_write_failure_stops_before_next_news_and_releases_lock(self):
        self.prepare_pipeline((2,))
        original_streams = []

        def create_then_break_disk(news):
            handler = log._file_handler
            original_streams.append(handler.stream)
            broken = MagicMock()
            broken.write.side_effect = OSError("disk fixture failure")
            handler.stream = broken
            return {"id": 1, "link": "https://example.invalid/1", "status": "draft"}

        self.posts.side_effect = create_then_break_disk
        try:
            with self.assertRaises(log.LogWriteError):
                prelaunch.main()
            self.assertEqual(self.posts.call_count, 1)
            self.assertIn("[ERROR]", self.console.getvalue())
            self.assertFalse(list((self.root / "eml").iterdir()))
            self.assertFalse(log._logger.handlers)
            lock = prelaunch.acquire_process_lock()
            self.assertIsNotNone(lock)
            prelaunch.release_process_lock(lock)
        finally:
            for stream in original_streams:
                stream.close()

    def test_recovered_retry_has_separate_warning_and_info_blocks(self):
        self.prepare_pipeline((1,))
        # Restore the real retry loop in place of the normal fixture boundary.
        self.stack.enter_context(patch.object(mail, "download_with_retries",
                                             wraps=REAL_DOWNLOAD_WITH_RETRIES))
        path = self.root / "eml" / "ЗГП_25.eml"

        def download(mail_connection, uid):
            if download.calls == 0:
                download.calls += 1
                raise OSError("transient fixture failure")
            path.parent.mkdir(exist_ok=True)
            path.write_text("fixture", encoding="utf-8")
            return str(path)

        download.calls = 0
        self.stack.enter_context(patch.object(mail, "download_by_uid", side_effect=download))
        prelaunch.main()
        parsed = blocks(self.text())
        self.assertTrue(any(b["level"] == "WARNING" for b in parsed))
        retry = next(b for b in parsed if "ПОВТОРНОЕ СКАЧИВАНИЕ" in b["title"])
        self.assertEqual(retry["level"], "INFO")
        self.assertEqual(parsed[-1]["level"], "OK")


if __name__ == "__main__":
    unittest.main(verbosity=2)
