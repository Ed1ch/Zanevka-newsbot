"""Readable run journals; no mailbox, parsing or publication logic lives here."""

from datetime import datetime
import importlib
import logging
import math
from pathlib import Path
import re
import sys
import time
from uuid import uuid4


OK = 25
logging.addLevelName(OK, "OK")
_LEVELS = {"INFO": logging.INFO, "OK": OK,
           "WARNING": logging.WARNING, "ERROR": logging.ERROR}
_ROOT = Path(__file__).resolve().parent
_NAME = re.compile(r"newsbot_\d{8}_\d{6}_\d{6}_[0-9a-f]{12}\.txt\Z")
_logger = logging.getLogger("newsbot.run")
_logger.setLevel(logging.INFO)
_logger.propagate = False
_configured = False
_file_handler = None
_file_path = None
_header = None
_directory = _ROOT / "logs"
_retention_days = 30
_max_bytes = 200 * 1024 * 1024
_secrets = ()
_archive_broken = False


class LogWriteError(RuntimeError):
    """The active journal cannot be written; the pipeline must stop."""


def _redact(text):
    for value in _secrets:
        text = text.replace(value, "[REDACTED]")
    return text


class _BlockFormatter(logging.Formatter):
    def format(self, record):
        if record.detail and not record.getMessage():
            return ""
        lines = record.getMessage().splitlines() or [""]
        if record.detail:
            result = "\n".join("\t" + line for line in lines)
        else:
            stamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
            result = f"{stamp} [{record.levelname}] [{record.source}] {lines[0]}"
            result += "".join("\n\t" + line for line in lines[1:])
        if record.exc_info:
            result += "".join("\n\t" + line for line in
                              self.formatException(record.exc_info).splitlines())
        return _redact(result)


class _RunFileHandler(logging.FileHandler):
    def handleError(self, record):
        # Stop processing on a write failure instead of silently losing the journal.
        global _archive_broken, _file_handler
        error = sys.exc_info()[1]
        _archive_broken = True
        _logger.removeHandler(self)
        _file_handler = None
        try:
            self.close()
        except OSError:
            pass
        raise LogWriteError(f"Не удалось записать журнал {self.baseFilename}") from error


def setup(*, directory=None, retention_days=None, max_total_mb=None, stream=None):
    """Configure one run without creating a directory or a journal yet.

    Relative LOG_DIR is anchored to the project, independently of cron's cwd.
    Older local config.py files may omit all three LOG_* settings.
    """
    global _configured, _directory, _retention_days, _max_bytes
    global _file_path, _header, _secrets, _archive_broken
    close()
    config = importlib.import_module("config")
    directory = directory if directory is not None else getattr(config, "LOG_DIR", "logs")
    retention_days = (retention_days if retention_days is not None else
                      getattr(config, "LOG_RETENTION_DAYS", 30))
    max_total_mb = (max_total_mb if max_total_mb is not None else
                    getattr(config, "LOG_MAX_TOTAL_MB", 200))
    _directory = Path(directory).expanduser()
    if not _directory.is_absolute():
        _directory = _ROOT / _directory
    _retention_days = float(retention_days)
    _max_bytes = int(float(max_total_mb) * 1024 * 1024)
    if not math.isfinite(_retention_days) or _retention_days <= 0 or _max_bytes <= 0:
        raise ValueError("LOG_RETENTION_DAYS и LOG_MAX_TOTAL_MB должны быть больше нуля.")
    _secrets = tuple(str(value) for name, value in vars(config).items()
                     if any(key in name for key in ("PASSWORD", "TOKEN", "SECRET", "API_KEY"))
                     and isinstance(value, str) and value)
    _file_path = None
    _header = None
    _archive_broken = False
    console = logging.StreamHandler(stream if stream is not None else sys.stdout)
    console.setFormatter(_BlockFormatter())
    _logger.addHandler(console)
    _configured = True
    _prune()


def _ensure_setup():
    if not _configured:
        setup()


def _prune():
    """Only delete this module's completed journals; the active file is excluded."""
    if not _directory.is_dir():
        return
    try:
        active_path = _file_path if _file_handler is not None else None
        files = [(path, path.stat()) for path in _directory.iterdir()
                 if _NAME.fullmatch(path.name) and not path.is_symlink()
                 and path.is_file() and path != active_path]
        files.sort(key=lambda item: (item[1].st_mtime, item[0].name))
        cutoff = time.time() - _retention_days * 86400
        active_bytes = active_path.stat().st_size if active_path and active_path.exists() else 0
        total = sum(stat.st_size for _, stat in files) + active_bytes
        for path, stat in files:
            if stat.st_mtime < cutoff or total > _max_bytes:
                path.unlink()
                total -= stat.st_size
    except OSError as error:
        # Retention failure must not prevent processing or recursively open a file.
        block("Не удалось очистить старые журналы", module="log", level="WARNING",
              exception=error, persist=False)


def activate():
    """Create the run file before downloading the first matching email.

    Keep just the current block header, not a buffer of empty-check messages.
    This lets the first persisted detail inherit its header unambiguously.
    """
    global _file_handler, _file_path
    _ensure_setup()
    if _file_handler is not None:
        return _file_path
    if _archive_broken:
        raise OSError("Запись журнала этого запуска уже завершилась ошибкой.")
    _directory.mkdir(parents=True, exist_ok=True)
    name = f"newsbot_{datetime.now():%Y%m%d_%H%M%S_%f}_{uuid4().hex[:12]}.txt"
    path = _directory / name
    handler = _RunFileHandler(path, mode="x", encoding="utf-8", errors="backslashreplace")
    handler.setFormatter(_BlockFormatter())
    _file_path = path
    _file_handler = handler
    _logger.addHandler(handler)
    if _header is not None:
        handler.handle(_header)
    return path


def current_path():
    return _file_path


def _record(message, module, level, detail, exception=None):
    info = ((type(exception), exception, exception.__traceback__)
            if exception is not None else None)
    return _logger.makeRecord(_logger.name, _LEVELS[level], "", 0, str(message), (), info,
                              extra={"source": module, "detail": detail})


def block(title, *, module="main", level="INFO", exception=None, persist=True):
    """Start a block. WARNING/ERROR are always standalone, parseable headers."""
    global _header
    _ensure_setup()
    if level not in _LEVELS:
        raise ValueError(f"Неизвестный уровень журнала: {level}")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", module):
        raise ValueError(f"Некорректное имя модуля: {module}")
    if persist and level in ("WARNING", "ERROR") and _file_handler is None and not _archive_broken:
        try:
            activate()
        except OSError as error:
            # Fatal startup/file errors still reach the console. The pipeline's
            # explicit activate() call remains fail-fast before any download.
            record = _record(f"Журнал недоступен: {error}", "log", "ERROR", False)
            _logger.handle(record)
    record = _record(title, module, level, False, exception)
    if _header is not None:
        _logger.handle(_record("", _header.source, _header.levelname, True))
    _header = record
    _logger.handle(record)


def detail(message):
    """Append complete lines with a leading tab; emit/flush immediately."""
    _ensure_setup()
    if _header is None:
        block("Ход выполнения")
    _logger.handle(_record(message, _header.source, _header.levelname, True))


def problem(message, error, *, module="main", level="WARNING"):
    block(message, module=module, level=level, exception=error)


def close():
    """Flush and close handlers. Call after working-file cleanup and the summary."""
    global _file_handler, _configured
    if _file_handler is not None:
        handler = _file_handler
        _logger.removeHandler(handler)
        _file_handler = None
        try:
            handler.close()
        except OSError:
            pass
    if _configured:
        _prune()
    for handler in _logger.handlers[:]:
        _logger.removeHandler(handler)
        try:
            handler.close()
        except OSError:
            pass
    _file_handler = None
    _configured = False


def email_started(uid, subject, eml_path):
    block(f"ОБРАБОТКА ПИСЬМА UID {uid} ...")
    detail(f"Subject: {subject}")
    detail("Установлено состояние IMAP: SEEN")
    detail(f"Загружен EML: {eml_path}")


def attachments_found(document_count, image_count):
    detail(f"DOCX найдено: {document_count}")
    detail(f"Изображений найдено: {image_count}")


def matching_report(matches, unmatched, ambiguous):
    block("СОПОСТАВЛЕНИЕ DOCX И ИЗОБРАЖЕНИЙ ...",
          level="WARNING" if unmatched or ambiguous else "INFO")
    for filename, images in matches:
        detail(f"DOCX: {filename}")
        for image in images:
            detail(f"  ✓ {image}")
        if not images:
            detail("  Картинки: НЕ НАЙДЕНЫ")
    if unmatched:
        detail("Непривязанные картинки:")
        for filename in unmatched:
            detail(f"  ! {filename}")
    else:
        detail("Непривязанных картинок нет.")
    if ambiguous:
        detail("ВНИМАНИЕ: неоднозначные сопоставления:")
        for filename, documents in ambiguous.items():
            detail(f"  ! {filename}")
            for document in documents:
                detail(f"      -> {document}")
    else:
        detail("Неоднозначных сопоставлений нет.")


def vk_links_report(email_links, document_links):
    block("ПАРСИНГ VK ссылок ...")
    if email_links:
        detail("VK ссылки из письма:")
        for item in email_links:
            detail(f"DOCX: {item['document']}")
            detail(f"URL: {item['url']}")
    else:
        detail("VK ссылок, привязанных к DOCX в письме, не найдено.")
    if document_links:
        detail("VK ссылки из DOCX:")
        for item in document_links:
            detail(item["document"])
            detail(f"URL: {item['url']}")
    else:
        detail("VK ссылок DOCX не найдено.")


def news_started(number, title, *, continued=False):
    label = "ПРОДОЛЖЕНИЕ НОВОСТИ" if continued else "НОВОСТЬ"
    block(f"{label} #{number}: {title}")


def post_result(post_id, url, status):
    detail(f"ID: {post_id}")
    detail(f"URL: {url}")
    detail(f"Статус: {status}")


def run_finished(level, processed_emails, failed_emails, failed_downloads=0):
    block("ГОТОВО", level=level)
    detail(f"Успешно обработано писем: {processed_emails}")
    detail(f"Оставлено в INBOX как SEEN после ошибок: {failed_emails + failed_downloads}")
    if failed_downloads:
        detail(f"Из них не удалось скачать: {failed_downloads}")
