"""Offline logging checks: python probe/check_logging.py (no pytest required).

Uses a placeholder config and mocks IMAP/WordPress. No real mail or posts are
changed. requests may be absent in the probe environment; HTTP is forbidden
either way. Production still uses the dependencies in requirements.txt.
"""

from contextlib import ExitStack
from datetime import datetime
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
from uuid import UUID


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
        self.stack.enter_context(patch.object(prelaunch, "FLAGS_DIR", self.flags))
        self.stack.enter_context(patch.object(prelaunch, "RUNNING_FILE", self.flags / ".newsbot.running"))
        self.stack.enter_context(patch.object(prelaunch, "load_configuration", return_value=config))
        self.stack.enter_context(patch.object(log, "_FLAGS", self.flags))
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
        self.assertRegex(paths[0].name, r"^\d{8}_\d{6}_[0-9a-f]{6}\.txt\Z")
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

    def test_empty_check_has_one_journal_and_explicit_marker_without_ok_summary(self):
        self.stack.enter_context(patch.object(mail, "search_unseen_news", return_value=[]))
        prelaunch.main()
        self.assertIn("Новых писем нет", self.text())
        self.assertEqual(len(list(self.flags.glob("*.empty.json"))), 1)
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

    def test_empty_minimum_age_and_bot_owned_cleanup(self):
        self.stack.enter_context(patch.object(mail, "search_unseen_news", return_value=[]))
        prelaunch.main()
        first = next(self.logs.glob("*.txt"))
        marker = next(self.flags.glob("*.empty.json"))
        data = json.loads(marker.read_text(encoding="utf-8"))
        prelaunch.main()
        self.assertTrue(first.exists())
        data["completed"] = time.time() - 601
        marker.write_text(json.dumps(data), encoding="utf-8")
        prelaunch.main()
        self.assertFalse(first.exists())
        self.assertFalse(marker.exists())

    def test_empty_repeat_and_debug_marker_loss_keep_meaningful_logs(self):
        self.stack.enter_context(patch.object(mail, "search_unseen_news", return_value=[]))
        prelaunch.DELAYED_PUBLISH_FILE.touch()
        prelaunch.main()
        path = next(self.logs.glob("*.txt"))
        self.assertFalse(prelaunch.DELAYED_PUBLISH_FILE.exists())
        self.assertFalse(list(self.flags.glob("*.empty.json")))
        self.assertIn("delayed.publish погашен", path.read_text(encoding="utf-8"))
        prelaunch.reset_flags()
        prelaunch.main()
        self.assertTrue(path.exists())

    def test_locked_empty_file_warns_but_next_run_continues(self):
        self.stack.enter_context(patch.object(mail, "search_unseen_news", return_value=[]))
        prelaunch.main()
        first = next(self.logs.glob("*.txt"))
        marker = next(self.flags.glob("*.empty.json"))
        data = json.loads(marker.read_text(encoding="utf-8"))
        data["completed"] = time.time() - 601
        marker.write_text(json.dumps(data), encoding="utf-8")
        original = Path.unlink
        def unlink(path, *args, **kwargs):
            if path == first:
                raise PermissionError("Windows sharing fixture")
            return original(path, *args, **kwargs)
        with patch.object(Path, "unlink", unlink):
            prelaunch.main()
        self.assertTrue(first.exists())
        self.assertTrue(marker.exists())
        prelaunch.main()
        self.assertFalse(first.exists())

    def test_empty_marker_requires_journal_name_and_matching_metadata(self):
        log.setup()
        path = log.activate()
        log.block("Пустая проверка")
        log.close()
        unrelated = self.logs / "important.txt"
        unrelated.write_text("keep", encoding="utf-8")
        log.mark_empty(unrelated)
        self.assertFalse((self.flags / (unrelated.name + ".empty.json")).exists())
        log.mark_empty(path)
        marker = self.flags / (path.name + ".empty.json")
        data = json.loads(marker.read_text(encoding="utf-8"))
        data["completed"] = time.time() - 601
        # Even a well-formed marker cannot remove the current journal.
        marker.write_text(json.dumps(data), encoding="utf-8")
        log.cleanup_empty_journals()
        self.assertTrue(path.exists())
        self.assertTrue(marker.exists())
        with patch.object(log, "_file_path", None):
            for field, invalid in (("size", data["size"] + 1),
                                   ("mtime_ns", data["mtime_ns"] + 1),
                                   ("path", str(unrelated.resolve()))):
                with self.subTest(field=field):
                    marker.write_text(json.dumps(dict(data, **{field: invalid})), encoding="utf-8")
                    log.cleanup_empty_journals()
                    self.assertTrue(path.exists())
                    self.assertTrue(marker.exists())
            unrelated_marker = self.flags / (unrelated.name + ".empty.json")
            unrelated_marker.write_text(json.dumps(dict(data, path=str(unrelated.resolve()))), encoding="utf-8")
            log.cleanup_empty_journals()
            self.assertTrue(unrelated.exists())
            self.assertTrue(unrelated_marker.exists())
            marker.write_text(json.dumps(data), encoding="utf-8")
            log.cleanup_empty_journals()
        self.assertFalse(path.exists())
        self.assertFalse(marker.exists())

    def test_deferred_summary_exact_hint_and_repeat_no_second_deferral(self):
        self.prepare_pipeline((1,))
        self.stack.enter_context(patch.object(main, "check_integrity", return_value={
            "matches": [], "unmatched_images": ["unmatched.jpg"], "ambiguous_images": {},
            "documents_without_media": [], "incomplete": True}))
        self.stack.enter_context(patch.object(main, "mark_email_unseen"))
        prelaunch.main()
        text = self.text()
        self.assertEqual(blocks(text)[-1]["title"], "ЗАПУСК ЗАВЕРШЁН: ПУБЛИКАЦИЯ ОТЛОЖЕНА")
        self.assertEqual(blocks(text)[-1]["details"][-1], 'Для форсированной публикации нажмите кнопку "Запустить сейчас"')
        self.assertTrue(prelaunch.DELAYED_PUBLISH_FILE.exists())
        self.posts.assert_not_called()
        self.stack.enter_context(patch.object(main, "extract_attachments", return_value={
            "documents":[{"filename":"документ0.docx"}], "images":[]}))
        prelaunch.main()
        self.assertFalse(prelaunch.DELAYED_PUBLISH_FILE.exists())
        self.posts.assert_called_once()

    def test_batch_cross_mail_snapshot_dedup_and_docx_vk_priority(self):
        self.prepare_pipeline((1, 1))
        # Existing main uses one batch download boundary and prepares all EML
        # before the first publication, with DOCX VK overriding email VK.
        self.docx_links.return_value = [{"document":"документ0.docx", "url":"https://vk.ru/wall-1_2"}]
        process_vk = self.stack.enter_context(patch.object(main, "process_vk"))
        prelaunch.main()
        self.assertEqual(self.download.call_count, 2)
        self.assertEqual(self.posts.call_count, 2)
        self.assertEqual(process_vk.call_args.args[1]["url"], "https://vk.ru/wall-1_2")
        self.assertTrue(process_vk.call_args.args[2])

    def test_snapshot_all_downloads_before_shared_materials_and_duplicate_warning(self):
        self.prepare_pipeline((1, 1))
        candidates = [("25", "ЗГП"), ("26", "ЗГП")]
        search = self.stack.enter_context(patch.object(mail, "search_unseen_news", side_effect=lambda connection: list(candidates)))
        original_download = self.download.side_effect
        reservations = []
        def download(uid):
            candidates.append(("99", "late ЗГП"))
            return original_download(uid)
        self.download.side_effect = download
        self.stack.enter_context(patch.object(mail, "reserve_downloaded_email", side_effect=lambda uid, path: reservations.append(uid)))
        payloads = [
            {"documents":[{"filename":"shared.docx", "data":b"first"}], "images":[], "text":"mail one", "html":""},
            {"documents":[{"filename":"SHARED.DOCX", "data":b"duplicate"}], "images":[{"filename":"shared.jpg"}], "text":"mail two", "html":""},
        ]
        def extract(path):
            self.assertEqual(reservations, ["25", "26"])
            return payloads.pop(0)
        self.stack.enter_context(patch.object(main, "extract_attachments", side_effect=extract))
        def build(documents, images):
            self.assertEqual([d["data"] for d in documents], [b"first"])
            self.assertEqual([i["filename"] for i in images], ["shared.jpg"])
            return [{"title":"Shared", "document_filename":"shared.docx", "featured_image":"shared.jpg"}]
        self.stack.enter_context(patch.object(main, "build_news", side_effect=build))
        vk = self.stack.enter_context(patch.object(main, "extract_vk_links", return_value=[]))
        prelaunch.main()
        self.assertEqual(search.call_count, 1)
        self.assertEqual([c.args[0] for c in self.download.call_args_list], ["25", "26"])
        self.assertEqual([c.args[0]["text"] for c in vk.call_args_list], ["mail one", "mail two"])
        self.assertIn("Дубль вложения пропущен", self.text())
        self.posts.assert_called_once()

    def test_unseen_all_confirmed_before_delay_marker_and_failure_stops_it(self):
        self.prepare_pipeline((1, 1))
        self.stack.enter_context(patch.object(main, "check_integrity", return_value={
            "matches":[],"unmatched_images":["orphan.jpg"],"ambiguous_images":{},
            "documents_without_media":[],"incomplete":True}))
        calls = []
        def unseen(uid):
            self.assertFalse(prelaunch.DELAYED_PUBLISH_FILE.exists())
            calls.append(uid)
            if uid == "26": raise OSError("UNSEEN not confirmed")
        self.stack.enter_context(patch.object(main, "mark_email_unseen", side_effect=unseen))
        with self.assertRaisesRegex(OSError, "UNSEEN not confirmed"):
            prelaunch.main()
        self.assertEqual(calls, ["25", "26"])
        self.assertFalse(prelaunch.DELAYED_PUBLISH_FILE.exists())
        self.posts.assert_not_called()
        self.assertNotIn('Для форсированной публикации', self.text())

    def test_default_id_cached_per_batch_and_vk_without_preview_skips_only_item(self):
        batches = self.prepare_pipeline((2,))
        for news in batches[0]:
            news["featured_image"] = None
            news["gallery_images"] = []
        self.stack.enter_context(patch.object(main, "get_default_media_id", return_value=77))
        prelaunch.main()
        main.get_default_media_id.assert_called_once()
        for call in self.posts.call_args_list:
            self.assertEqual(call.args[0]["featured_media_id"], 77)
            self.assertEqual(call.args[0]["gallery_images"], [])

    def test_vk_without_own_images_needs_preview_no_default_and_next_item_continues(self):
        batches = self.prepare_pipeline((2,))
        for news in batches[0]: news["featured_image"] = None
        self.docx_links.return_value = [{"document":"документ0.docx", "url":"https://vk.ru/wall-1_2"}]
        default = self.stack.enter_context(patch.object(main, "get_default_media_id", return_value=77))
        self.stack.enter_context(patch.object(main, "get_vk_video_from_post", return_value={"player":"https://example.invalid/player"}))
        prelaunch.main()
        self.posts.assert_called_once()
        self.assertEqual(self.posts.call_args.args[0]["document_filename"], "документ1.docx")
        default.assert_called_once()
        self.assertEqual(blocks(self.text())[-1]["level"], "WARNING")

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
        # File activation now happens before the pipeline is imported/called;
        # an unavailable log directory is early stderr, before any mailbox work.
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

    def test_journal_name_uses_creation_time_and_first_six_uuid_hex_characters(self):
        with patch.object(log, "datetime") as clock, patch.object(log, "uuid4", return_value=UUID(
                "a7f2c900-1234-4abc-89ab-0123456789ab")):
            clock.now.return_value = datetime(2026, 10, 26, 13, 20, 15, 123456)
            log.setup()
            path = log.activate()
        self.assertEqual(path.name, "26102026_132015_a7f2c9.txt")
        self.assertIsNotNone(log._NAME.fullmatch(path.name))
        for invalid in ("26102026_132015_a7f2c.txt", "26102026_132015_A7F2C9.txt",
                        "26102026_132015_a7f2c9.txt.bak", "26102026_132015_a7f2c9.txt\n"):
            with self.subTest(name=invalid):
                self.assertIsNone(log._NAME.fullmatch(invalid))

    def test_unique_files_and_repeated_setup_do_not_duplicate_handlers(self):
        clock = self.stack.enter_context(patch.object(log, "datetime", wraps=datetime))
        clock.now.return_value = datetime(2026, 10, 26, 13, 20, 15)
        log.setup()
        log.block("Первый")
        first = log.activate()
        log.detail("Одна строка")
        log.setup()
        log.block("Второй")
        second = log.activate()
        log.detail("Другая строка")
        self.assertNotEqual(first, second)
        for path in (first, second):
            self.assertRegex(path.name, r"^\d{8}_\d{6}_[0-9a-f]{6}\.txt\Z")
        self.assertEqual(first.read_text(encoding="utf-8").count("Одна строка"), 1)
        self.assertEqual(second.read_text(encoding="utf-8").count("Другая строка"), 1)

    def test_age_size_retention_and_unrelated_file_protection(self):
        self.logs.mkdir()
        names = [f"01012026_000000_{i:06x}.txt" for i in range(4)]
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
