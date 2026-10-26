"""Synchronous WebUI/API service. The bot always runs in prelaunch's process."""

import argparse
import ast
from collections import deque
from datetime import timedelta
import getpass
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4

from flask import Flask, Response, jsonify, redirect, request, send_from_directory, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
import prelaunch


ROOT = Path(__file__).resolve().parent
LOCAL_CONFIG = ROOT / "webui_server.local.json"
SECRET_FIELDS = {"IMAP_PASSWORD", "WP_APP_PASSWORD", "VK_ACCESS_TOKEN"}
INTEGER_FIELDS = {
    "IMAP_PORT": (1, 65535), "IMAP_TIMEOUT": (1, 3600),
    "IMAP_RETRY_COUNT": (1, 20), "IMAP_RETRY_DELAY": (0, 600),
    "WP_API_TIMEOUT": (1, 3600), "GALLERY_COLUMNS": (1, 9), "VK_API_TIMEOUT": (1, 3600),
}
TEXT_FIELDS = {"IMAP_HOST", "IMAP_USERNAME", "IMAP_MAILBOX", "IMAP_TRASH", "IMAP_SUBJECT",
               "IMAP_OUTPUT_DIR", "WP_URL", "WP_USERNAME", "POST_STATUS", "GALLERY_SIZE",
               "VK_API_VERSION"} | SECRET_FIELDS
SETTING_FIELDS = TEXT_FIELDS | INTEGER_FIELDS.keys()
JOURNAL_NAME = re.compile(r"\d{8}_\d{6}_[0-9a-f]{6}\.txt\Z")
HEADER = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \[(INFO|OK|WARNING|ERROR)\] ")


class APIError(Exception):
    def __init__(self, code, message, status=400, fields=None):
        self.code, self.message, self.status, self.fields = code, message, status, fields


def atomic_write(path, data):
    """Same-directory replace; preserve permissions and leave old bytes on failure."""
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.chmod(temporary, mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Settings:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()

    def read(self):
        try:
            if self.path.is_symlink():
                raise ValueError("config.py не должен быть ссылкой.")
            raw = self.path.read_bytes()
            text = raw.decode("utf-8-sig")
            tree = ast.parse(text)
            values, spans = {}, {}
            lines = text.encode("utf-8").splitlines(keepends=True)
            starts = [0]
            for line in lines:
                starts.append(starts[-1] + len(line))
            for node in tree.body:
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    continue
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    name, value = node.targets[0].id, node.value
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                    name, value = node.target.id, node.value
                else:
                    raise ValueError("Поддерживаются только отдельные присваивания литералов.")
                if name in values:
                    raise ValueError("Повторное присваивание в конфигурации.")
                values[name] = ast.literal_eval(value)
                spans[name] = (starts[value.lineno - 1] + value.col_offset,
                               starts[value.end_lineno - 1] + value.end_col_offset)
            if not SETTING_FIELDS <= values.keys():
                raise ValueError("В config.py отсутствуют поля рабочей формы.")
            if any(not isinstance(values[key], str) for key in TEXT_FIELDS) or any(type(values[key]) is not int for key in INTEGER_FIELDS):
                raise ValueError("Неподдерживаемые типы конфигурации.")
            return raw, values, spans
        except (OSError, ValueError, SyntaxError, TypeError):
            raise APIError("config_unavailable", "config.py отсутствует или не поддерживается безопасным редактором. Файл сохранён.", 503) from None

    @staticmethod
    def safe(raw, values):
        return {"values": {key: values[key] for key in sorted(SETTING_FIELDS - SECRET_FIELDS)},
                "configured_secrets": {key: bool(values[key]) for key in sorted(SECRET_FIELDS)},
                "revision": hashlib.sha256(raw).hexdigest()}

    def get(self):
        with self.lock:
            raw, values, _ = self.read()
            return self.safe(raw, values)

    @staticmethod
    def validate(values):
        if not isinstance(values, dict) or values.keys() - SETTING_FIELDS:
            raise APIError("invalid_settings", "Разрешены только поля рабочей формы.")
        errors = {}
        for name, value in values.items():
            if name in INTEGER_FIELDS:
                lower, upper = INTEGER_FIELDS[name]
                valid = type(value) is int and lower <= value <= upper
            else:
                valid = isinstance(value, str) and len(value) <= 4096 and not any(c in value for c in "\r\n\0")
                if valid and name not in SECRET_FIELDS | {"IMAP_SUBJECT", "IMAP_TRASH"}:
                    valid = bool(value.strip())
                if valid and name == "POST_STATUS":
                    valid = value in {"draft", "publish", "pending"}
                if valid and name == "GALLERY_SIZE":
                    valid = value in {"thumbnail", "medium", "large", "full"}
                if valid and name == "VK_API_VERSION":
                    valid = bool(re.fullmatch(r"\d+\.\d+", value))
                if valid and name == "WP_URL":
                    from urllib.parse import urlsplit
                    try:
                        parsed = urlsplit(value)
                        valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
                    except ValueError:
                        valid = False
                if valid and name in SECRET_FIELDS:
                    valid = value != "***"
                if valid and name == "IMAP_OUTPUT_DIR":
                    # This is an existing form field; it cannot overwrite source,
                    # flags/config/logs or point cleanup outside the project.
                    path = Path(value)
                    valid = not path.is_absolute() and ".." not in path.parts and bool(path.parts) and path.parts[0].casefold() not in {"flags", "webui", "gpt_context", "probe", ".git", "logs"}
            if not valid:
                errors[name] = "Неверный тип, диапазон или значение."
        if errors:
            raise APIError("invalid_settings", "Проверьте настройки.", fields=errors)

    def save(self, data):
        if set(data) != {"values", "revision"}:
            raise APIError("invalid_settings", "Нужны values и revision.")
        self.validate(data["values"])
        with self.lock:
            raw, values, spans = self.read()
            if data["revision"] != hashlib.sha256(raw).hexdigest():
                raise APIError("revision_conflict", "Конфигурация изменена. Загрузите её заново.", 409)
            replacements = []
            for name, value in data["values"].items():
                if name in SECRET_FIELDS and not value:
                    continue
                start, end = spans[name]
                replacements.append((start, end, repr(value).encode("utf-8")))
                values[name] = value
            encoded = raw.decode("utf-8-sig").encode("utf-8")
            for start, end, value in sorted(replacements, reverse=True):
                encoded = encoded[:start] + value + encoded[end:]
            if raw.startswith(b"\xef\xbb\xbf"):
                encoded = b"\xef\xbb\xbf" + encoded
            try:
                # Recheck immediately before replace, including external edits.
                if self.path.read_bytes() != raw:
                    raise APIError("revision_conflict", "Конфигурация изменена.", 409)
                atomic_write(self.path, encoded)
            except OSError:
                raise APIError("write_failed", "Не удалось сохранить config.py. Проверьте права и свободное место.", 503) from None
            return self.safe(encoded, values)


def load_server_config(path):
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError("Сначала выполните python webui_server.py init-admin") from None
    if not isinstance(cfg, dict):
        raise RuntimeError("Серверная конфигурация должна быть JSON-объектом.")
    defaults = {"host": "127.0.0.1", "port": 8081, "threads": 12,
                "prefix": "", "external_https": True, "trusted_proxies": [],
                "session_hours": 8}
    cfg = defaults | cfg
    if not all(isinstance(cfg.get(key), str) and cfg[key] for key in ("username", "password_hash", "session_key")):
        raise RuntimeError("Выполните локальную инициализацию администратора.")
    if (not cfg["password_hash"].startswith(("scrypt:", "pbkdf2:sha256:"))
            or not re.fullmatch(r"[0-9a-f]{64}", cfg["session_key"])):
        raise RuntimeError("Хеш и случайный ключ должны быть созданы командой init-admin; пример не является рабочим конфигом.")
    prefix = cfg["prefix"]
    if prefix and not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)+", prefix):
        raise RuntimeError("prefix: пустая строка либо /newsbot без завершающего слеша.")
    if (type(cfg["external_https"]) is not bool or
            any(type(cfg[key]) is not int for key in ("port", "threads", "session_hours")) or
            not 1 <= cfg["port"] <= 65535 or not 8 <= cfg["threads"] <= 128 or not 1 <= cfg["session_hours"] <= 24):
        raise RuntimeError("Проверьте port, threads (8–128), session_hours (1–24), external_https.")
    if not cfg["external_https"] and cfg["host"] not in {"127.0.0.1", "::1", "localhost"}:
        raise RuntimeError("Локальный HTTP разрешён только на loopback.")
    if not isinstance(cfg["trusted_proxies"], list):
        raise RuntimeError("trusted_proxies должен быть списком IP-адресов.")
    for address in cfg["trusted_proxies"]:
        ipaddress.ip_address(address)
    return cfg


class ProxyBoundary:
    """Strip forwarding headers unless the immediate peer is explicitly trusted."""
    def __init__(self, app, cfg):
        self.app, self.cfg = app, cfg
        self.forwarded = ProxyFix(app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=0)

    def __call__(self, environ, start_response):
        trusted = environ.get("REMOTE_ADDR") in self.cfg["trusted_proxies"]
        if not trusted:
            for name in list(environ):
                if name.startswith("HTTP_X_FORWARDED_") or name == "HTTP_FORWARDED":
                    environ.pop(name, None)
        prefix = self.cfg["prefix"]
        path = environ.get("PATH_INFO", "")
        if prefix:
            if path == prefix:
                start_response("308 Permanent Redirect", [("Location", prefix + "/"), ("Content-Length", "0")])
                return [b""]
            if path != prefix and not path.startswith(prefix + "/"):
                start_response("404 Not Found", [("Content-Type", "text/plain")])
                return [b"Not found"]
            environ["SCRIPT_NAME"] = prefix
            environ["PATH_INFO"] = path[len(prefix):] or "/"
        return (self.forwarded if trusted else self.app)(environ, start_response)


def _journal_order(name):
    # DDMMYYYY is not lexically chronological; the suffix breaks same-second ties.
    return name[4:8], name[2:4], name[:2], name[9:15], name[16:22]


class LogHub:
    """One file reader, byte offsets, bounded replay and per-client queues."""
    def __init__(self, directory, status):
        self.directory, self.status = directory, status
        self.lock = threading.RLock()
        self.epoch, self.sequence = uuid4().hex, 0
        self.events = deque(maxlen=2048)
        self.lines = deque()
        self.bytes = 0
        self.truncated = False
        self.files = {}
        self.directory_stamp = None
        self.subscribers = set()
        self.stop = threading.Event()
        self.last_status = None
        self.error = None
        self.thread = None

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._read, name="newsbot-log-reader", daemon=True)
            self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)

    def cursor(self):
        return f"{self.epoch}:{self.sequence}"

    def emit(self, kind, data):
        with self.lock:
            self.sequence += 1
            event = {"id": self.cursor(), "kind": kind, "data": data}
            self.events.append(event)
            for subscriber in self.subscribers:
                if subscriber.overflow:
                    continue
                try:
                    subscriber.put_nowait(event)
                except queue.Full:
                    subscriber.overflow = True
                    while not subscriber.empty():
                        subscriber.get_nowait()
                    subscriber.put_nowait({"id": event["id"], "kind": "resync",
                                           "data": {"reason": "slow_client"}})

    def _discover(self):
        try:
            stamp = self.directory.stat().st_mtime_ns
        except FileNotFoundError:
            stamp = None
        if stamp == self.directory_stamp:
            return
        entries = [] if stamp is None else [path for path in self.directory.iterdir()
            if JOURNAL_NAME.fullmatch(path.name) and not path.is_symlink() and path.is_file()]
        self.directory_stamp = stamp
        entries.sort(key=lambda path: _journal_order(path.name))
        names = {path.name for path in entries[-10:]}
        removed = set(self.files) - names
        if removed:
            self.lines = deque(line for line in self.lines if line["file"] not in removed)
            self.bytes = sum(len(line["text"].encode("utf-8")) for line in self.lines)
            for name in removed:
                del self.files[name]
            self.emit("resync", {"reason": "files_changed"})
        for path in entries[-10:]:
            if path.name not in self.files:
                info = path.stat()
                self.files[path.name] = {"path": path, "offset": 0, "pending": b"",
                    "inode": (info.st_dev, info.st_ino),
                    "generation": f"{path.name}:{info.st_dev}:{info.st_ino}",
                    "context": {"time": None, "level": "INFO", "block": None},
                    "discard": False, "initial": True}

    def _line(self, state, raw, offset):
        text = raw.rstrip(b"\r").decode("utf-8", errors="replace")
        match = HEADER.match(text)
        identity = state["generation"] + ":" + str(offset)
        if match:
            state["context"] = {"time": match[1], "level": match[2], "block": identity}
        kind = "header" if match else "detail" if text else "blank"
        line = {"identity": identity, "file": state["path"].name, "type": kind,
                "text": text, **state["context"]}
        self.lines.append(line)
        self.bytes += len(text.encode("utf-8"))
        while self.bytes > 4 * 1024 * 1024 or len(self.lines) > 10000:
            old = self.lines.popleft()
            self.bytes -= len(old["text"].encode("utf-8"))
            self.truncated = True
        self.emit("line", line)

    def poll(self):
        with self.lock:
            self._discover()
            for state in self.files.values():
                path = state["path"]
                if path.is_symlink():
                    raise OSError("Ссылка вместо журнала.")
                info = path.stat()
                if (info.st_dev, info.st_ino) != state["inode"] or info.st_size < state["offset"]:
                    self.directory_stamp = None
                    state["offset"], state["pending"] = 0, b""
                    state["generation"] = uuid4().hex
                    state["context"] = {"time": None, "level": "INFO", "block": None}
                    self.lines = deque(line for line in self.lines if line["file"] != path.name)
                    self.bytes = sum(len(line["text"].encode("utf-8")) for line in self.lines)
                    state["inode"] = (info.st_dev, info.st_ino)
                    self.emit("resync", {"reason": "file_replaced"})
                if info.st_size == state["offset"]:
                    continue
                budget = info.st_size - state["offset"]
                if not state.pop("initial", False):
                    budget = min(budget, 256 * 1024)
                while budget > 0:
                    # On first discovery scan once to EOF before serving the
                    # snapshot; never allocate the entire file or rescan it.
                    # Each individual Windows handle is still short-lived.
                    with path.open("rb") as stream:
                        stream.seek(state["offset"])
                        chunk = stream.read(min(budget, 65536))
                    if not chunk:
                        break
                    budget -= len(chunk)
                    base = state["offset"] - len(state["pending"])
                    state["offset"] += len(chunk)
                    pending = state["pending"] + chunk
                    parts = pending.split(b"\n")
                    state["pending"] = parts.pop()
                    for raw in parts:
                        if not state["discard"]:
                            self._line(state, raw[:65536], base)
                            if len(raw) > 65536:
                                self.emit("diagnostic", {"text": "Строка журнала превышает лимит 64 КиБ.", "level": "WARNING"})
                        else:
                            state["discard"] = False
                        base += len(raw) + 1
                    if len(state["pending"]) > 65536:
                        state["pending"] = b""
                        state["discard"] = True
                        self.emit("diagnostic", {"text": "Строка журнала превышает лимит 64 КиБ.", "level": "WARNING"})

    def snapshot(self):
        with self.lock:
            ordered = sorted(self.lines, key=lambda line: (_journal_order(line["file"]), int(line["identity"].rsplit(":", 1)[1])))
            return {"files": sorted(self.files, key=_journal_order), "lines": ordered,
                    "cursor": self.cursor(), "truncated": self.truncated}

    def subscribe(self, cursor):
        subscriber = queue.Queue(maxsize=256)
        subscriber.overflow = False
        with self.lock:
            try:
                epoch, number = cursor.rsplit(":", 1)
                number = int(number)
                oldest = self.sequence - len(self.events)
                if epoch != self.epoch or not oldest <= number <= self.sequence:
                    raise ValueError
                replay = [event for event in self.events if int(event["id"].rsplit(":", 1)[1]) > number]
                if len(replay) >= subscriber.maxsize:
                    raise ValueError
                for event in replay:
                    subscriber.put_nowait(event)
            except (ValueError, AttributeError):
                subscriber.overflow = True
                subscriber.put_nowait({"id": self.cursor(), "kind": "resync", "data": {"reason": "cursor_expired"}})
            self.subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber):
        with self.lock:
            self.subscribers.discard(subscriber)

    def _read(self):
        while not self.stop.is_set():
            try:
                self.poll()
                if self.error:
                    self.emit("diagnostic", {"text": "Чтение журналов восстановлено.", "level": "INFO"})
                    self.error = None
            except OSError:
                if not self.error:
                    self.emit("diagnostic", {"text": "Не удалось прочитать журнал; проверьте доступ.", "level": "WARNING"})
                    self.error = True
            current = self.status()
            if current != self.last_status:
                self.last_status = current
                self.emit("status", current)
            self.stop.wait(0.5)


class BotControl:
    def __init__(self, root, settings):
        self.root, self.settings = root, settings
        self.flags = root / "flags"
        self.lock = threading.RLock()
        self.child = None
        self.accepted = False
        self.previous = "idle"
        self.cooldown = 0
        self.schedule_cache = (0, None)
        self.hub = None

    def schedule(self, refresh=False):
        if not refresh and self.schedule_cache[0] > time.monotonic():
            return self.schedule_cache[1]
        result = {"platform": "Windows/manual" if os.name == "nt" else "Unix/cron",
                  "enabled": None, "task_state": "unknown", "expression": None,
                  "timezone": None, "detail": "Расписание не подтверждено."}
        if os.name == "nt":
            result["task_state"] = "unsupported"
            result["detail"] = "Windows/manual; Планировщик заданий настраивается вне API."
        else:
            try:
                disabled = self.flags / ".newsbot.disabled"
                try:
                    disabled.lstat()
                    result["enabled"] = False
                except FileNotFoundError:
                    result["enabled"] = True
                executable = shutil.which("crontab")
                if executable:
                    process = subprocess.run([executable, "-l"], capture_output=True,
                        text=True, timeout=3, check=False)
                    if process.returncode == 0:
                        expected = str(self.root / "cron-newsbot.sh")
                        entries = [line.strip() for line in process.stdout.splitlines()
                                   if line.strip() and not line.lstrip().startswith("#")]
                        tasks = []
                        for line in entries:
                            fields = line.split(maxsplit=5)
                            if len(fields) != 6:
                                continue
                            try:
                                command = shlex.split(fields[5])
                            except ValueError:
                                continue
                            if command == [expected] or command in (["sh", expected], ["/bin/sh", expected]):
                                tasks.append(line)
                        if len(tasks) == 1:
                            result["task_state"] = "present"
                            result["expression"] = " ".join(tasks[0].split()[:5])
                            # Only explicit CRON_TZ preceding the job is known.
                            for line in entries[:entries.index(tasks[0])]:
                                if line.startswith("CRON_TZ="):
                                    result["timezone"] = line.split("=", 1)[1].strip('"\'')
                            result["detail"] = "Задание текущего пользователя найдено; состояние службы cron не проверяется."
                        else:
                            result["task_state"] = "missing" if not tasks else "ambiguous"
                    elif "no crontab" in process.stderr.lower():
                        result["task_state"] = "missing"
            except (OSError, subprocess.SubprocessError):
                pass
        self.schedule_cache = (time.monotonic() + 30, result)
        return result

    def status(self):
        with self.lock:
            state = prelaunch.check_running()
            if self.accepted:
                if self.child is not None and self.child.poll() is None:
                    if state == "idle":
                        state = "starting"
                else:
                    self.accepted = False
            if self.previous in {"starting", "running"} and state == "idle":
                self.cooldown = time.monotonic() + 1
            self.previous = state
            return {"process_state": state,
                    "can_run": state == "idle" and time.monotonic() >= self.cooldown,
                    "schedule": self.schedule()}

    def run(self):
        with self.lock:
            state = self.status()
            if state["process_state"] == "unknown":
                raise APIError("process_unknown", "Состояние процесса неизвестно.", 503)
            if not state["can_run"]:
                raise APIError("process_busy", "Запуск уже выполняется или ещё завершается.", 409)
            self.flags.mkdir(exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".api-stderr-", dir=self.flags)
            stderr = os.fdopen(fd, "wb")
            try:
                options = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
                self.child = subprocess.Popen([sys.executable, "-u", str(self.root / "prelaunch.py")],
                    cwd=self.root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr,
                    close_fds=True, env=os.environ | {"PYTHONUTF8": "1"}, **options)
                self.accepted = True
                self.previous = "starting"
            except OSError:
                stderr.close()
                Path(name).unlink(missing_ok=True)
                raise APIError("launch_failed", "Не удалось запустить prelaunch.py.", 503) from None
            finally:
                stderr.close()
            threading.Thread(target=self._watch, args=(self.child, Path(name)), daemon=True).start()

    def _watch(self, child, path):
        offset, pending = 0, b""
        while True:
            try:
                with path.open("rb") as stream:
                    stream.seek(offset)
                    data = stream.read(max(0, 65536 - offset))
                offset += len(data)
                pending += data
                done = child.poll() is not None
                parts = pending.split(b"\n")
                pending = parts.pop()
                if done and pending:
                    parts.append(pending)
                for line in parts:
                    text = line.decode("utf-8", errors="replace")
                    # Config may be unavailable; redact known values and avoid
                    # exposing source lines / exception values from early Python.
                    try:
                        _, values, _ = self.settings.read()
                        for key, value in values.items():
                            if any(word in key for word in ("PASSWORD", "TOKEN", "SECRET", "KEY")) and isinstance(value, str) and value:
                                text = text.replace(value, "[REDACTED]")
                    except APIError:
                        text = "Ранняя ошибка запуска; config.py недоступен для безопасной диагностики."
                    if text and self.hub:
                        self.hub.emit("diagnostic", {"text": text[:2000], "level": "ERROR"})
                if done:
                    break
            except OSError:
                if child.poll() is not None:
                    break
            time.sleep(0.25)
        child.wait()
        path.unlink(missing_ok=True)


def create_app(cfg, *, root=ROOT, config_path=None, start_reader=True):
    app = Flask(__name__, static_folder=None)
    prefix = cfg.get("prefix", "")
    app.config.update(SECRET_KEY=cfg["session_key"], MAX_CONTENT_LENGTH=65536,
        SESSION_COOKIE_NAME="newsbot_session", SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_SECURE=cfg["external_https"],
        SESSION_COOKIE_PATH=(prefix + "/") if prefix else "/",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=cfg.get("session_hours", 8)),
        SESSION_REFRESH_EACH_REQUEST=False)
    app.wsgi_app = ProxyBoundary(app.wsgi_app, cfg)
    settings = Settings(root / "config.py")
    control = BotControl(root, settings)
    try:
        _, values, _ = settings.read()
        directory = Path(values.get("LOG_DIR", "logs")).expanduser()
    except APIError:
        directory = Path("logs")
    if not directory.is_absolute():
        directory = root / directory
    hub = LogHub(directory, control.status)
    control.hub = hub
    credentials_lock = threading.RLock()
    sessions = {}
    failures = {}
    app.extensions["newsbot"] = {"settings": settings, "control": control, "hub": hub,
                                  "sessions": sessions}

    def credentials():
        if config_path is None:
            return cfg
        try:
            return load_server_config(config_path)
        except (RuntimeError, ValueError, TypeError):
            return None

    def authenticated(sid=None):
        sid = sid if sid is not None else session.get("sid")
        with credentials_lock:
            entry = sessions.get(sid)
            current = credentials()
            if not entry or not current:
                return False
            valid = (entry["expires"] > time.time() and
                     entry["version"] == current["password_hash"] + current["session_key"])
            if not valid:
                sessions.pop(sid, None)
            return valid

    def session_data():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        valid = authenticated()
        return {"authenticated": valid, "csrf_token": session["csrf"],
                "expires_at": sessions[session["sid"]]["expires"] if valid else None}

    @app.errorhandler(APIError)
    def handle_api_error(error):
        body = {"code": error.code, "message": error.message}
        if error.fields:
            body["fields"] = error.fields
        return jsonify(error=body), error.status

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(error={"code": "body_too_large", "message": "Слишком большой запрос."}), 400

    @app.errorhandler(404)
    @app.errorhandler(405)
    def route_error(error):
        return jsonify(error={"code": "route_error", "message": "Маршрут или метод не поддерживается."}), error.code

    @app.errorhandler(Exception)
    def unexpected(error):
        # Never echo exception values, config source, request bodies or secrets.
        return jsonify(error={"code": "server_error", "message": "Внутренняя ошибка сервера; проверьте локальную конфигурацию и права."}), 500

    @app.before_request
    def guard():
        if cfg["external_https"] and not request.is_secure:
            raise APIError("https_required", "Для этого режима требуется внешний HTTPS.", 400)
        if not cfg["external_https"] and request.remote_addr not in {"127.0.0.1", "::1"}:
            raise APIError("local_only", "Режим HTTP доступен только с loopback.", 403)
        if request.path.startswith("/api/"):
            public = request.path in {"/api/session", "/api/login"}
            if not public and not authenticated():
                raise APIError("unauthorized", "Требуется вход.", 401)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                csrf = request.headers.get("X-CSRF-Token", "")
                if not csrf or not hmac.compare_digest(csrf.encode(), session.get("csrf", "").encode()):
                    raise APIError("csrf", "Неверный CSRF-токен.", 403)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def body(allowed=None):
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or (allowed is not None and set(data) != set(allowed)):
            raise APIError("invalid_body", "Неверная структура JSON-запроса.")
        return data

    @app.get("/api/session")
    def get_session():
        return jsonify(session_data())

    @app.post("/api/login")
    def login():
        data = body({"username", "password"})
        if not all(isinstance(data[key], str) and len(data[key]) <= 1024 for key in data):
            raise APIError("invalid_login", "Неверные данные входа.")
        peer = request.remote_addr
        with credentials_lock:
            now = time.monotonic()
            recent = [stamp for stamp in failures.get(peer, []) if now - stamp < 60]
            if len(recent) >= 10:
                raise APIError("login_throttled", "Слишком много попыток. Повторите через минуту.", 429)
            current = credentials()
            valid_password = bool(current) and check_password_hash(current["password_hash"], data["password"])
            if not current or not valid_password or not hmac.compare_digest(data["username"].encode(), current["username"].encode()):
                failures[peer] = recent + [now]
                raise APIError("invalid_login", "Неверное имя или пароль.", 401)
            failures.pop(peer, None)
            sessions.pop(session.get("sid"), None)
            # Expired records are pruned on login; bound the admin registry.
            for key in list(sessions):
                if sessions[key]["expires"] <= time.time():
                    sessions.pop(key)
            if len(sessions) >= 64:
                sessions.pop(next(iter(sessions)))
            session.clear()
            session.permanent = True
            sid = secrets.token_urlsafe(32)
            session["sid"] = sid
            session["csrf"] = secrets.token_urlsafe(32)
            sessions[sid] = {"expires": time.time() + cfg.get("session_hours", 8) * 3600,
                            "version": current["password_hash"] + current["session_key"]}
        return jsonify(session_data())

    @app.post("/api/logout")
    def logout():
        body(set())
        with credentials_lock:
            sessions.pop(session.get("sid"), None)
        session.clear()
        return jsonify(session_data())

    @app.get("/api/status")
    def get_status():
        return jsonify(control.status())

    @app.post("/api/run")
    def run():
        body(set())
        control.run()
        return jsonify(accepted=True), 202

    @app.get("/api/settings")
    def get_settings():
        return jsonify(settings.get())

    @app.put("/api/settings")
    def put_settings():
        return jsonify(settings.save(body({"values", "revision"})))

    @app.put("/api/schedule")
    def put_schedule():
        data = body({"enabled"})
        if type(data["enabled"]) is not bool:
            raise APIError("invalid_schedule", "enabled должен быть boolean.")
        if os.name == "nt":
            raise APIError("schedule_unsupported", "Windows/manual; управление планировщиком не поддерживается.", 400)
        with control.lock:
            try:
                control.flags.mkdir(exist_ok=True)
                disabled = control.flags / ".newsbot.disabled"
                if data["enabled"]:
                    disabled.unlink(missing_ok=True)
                else:
                    with disabled.open("xb"):
                        pass
            except FileExistsError:
                pass
            except OSError:
                raise APIError("schedule_failed", "Не удалось изменить флаг расписания.", 503) from None
            return jsonify(control.schedule(refresh=True))

    @app.post("/api/debug/reset-flags")
    def reset_flags():
        body(set())
        with control.lock:
            state = control.status()
            if state["process_state"] == "unknown":
                raise APIError("process_unknown", "Состояние процесса неизвестно.", 503)
            if state["process_state"] != "idle" or control.accepted:
                raise APIError("process_busy", "Бот выполняется или запускается.", 409)
            try:
                result = prelaunch.reset_flags()
            except OSError:
                raise APIError("reset_failed", "Нет безопасного доступа к flags/.", 503) from None
            if result is None:
                raise APIError("process_busy", "Общая блокировка занята.", 409)
            control.schedule(refresh=True)
            if result["errors"]:
                return jsonify(error={"code": "partial_reset", "message": "Часть записей не удалена."}, **result), 500
            return jsonify(result)

    @app.get("/api/logs/history")
    def history():
        if hub.error:
            raise APIError("logs_unavailable", "Нет доступа к каталогу журналов. Проверьте LOG_DIR и права.", 503)
        return jsonify(hub.snapshot())

    @app.get("/api/events")
    def events():
        sid = session.get("sid")
        subscriber = hub.subscribe(request.headers.get("Last-Event-ID") or request.args.get("cursor"))
        def generate():
            heartbeat = time.monotonic()
            try:
                yield ": connected\n\n"
                while authenticated(sid):
                    try:
                        event = subscriber.get(timeout=1)
                    except queue.Empty:
                        if time.monotonic() - heartbeat >= 10:
                            yield ": heartbeat\n\n"
                            heartbeat = time.monotonic()
                        continue
                    if not authenticated(sid):
                        return
                    yield "id: " + event["id"] + "\nevent: " + event["kind"] + "\ndata: " + json.dumps(event["data"], ensure_ascii=False) + "\n\n"
                    if subscriber.overflow:
                        return
            finally:
                hub.unsubscribe(subscriber)
        response = Response(generate(), content_type="text/event-stream; charset=utf-8",
                            headers={"X-Accel-Buffering": "no"})
        response.call_on_close(lambda: hub.unsubscribe(subscriber))
        return response

    @app.get("/")
    def index():
        if not authenticated():
            return redirect(url_for("login_page"))
        return send_from_directory(root / "webui", "index.html")

    @app.get("/login")
    def login_page():
        if authenticated():
            return redirect(url_for("index"))
        return send_from_directory(root / "webui", "login.html")

    # Only pre-existing public assets plus the isolated login's CSS/JS. Never
    # expose the project root, README/AGENTS, config, flags or arbitrary files.
    @app.get("/index_files/<path:name>")
    def assets(name):
        if "/" in name or Path(name).suffix not in {".css", ".js", ".svg"}:
            raise APIError("not_found", "Файл недоступен.", 404)
        asset = root / "webui" / "index_files" / name
        if asset.is_symlink():
            raise APIError("not_found", "Файл недоступен.", 404)
        return send_from_directory(root / "webui" / "index_files", name)

    if start_reader:
        hub.start()
    return app


def admin_command(path, change=False):
    if change:
        cfg = load_server_config(path)
    else:
        if path.exists():
            raise RuntimeError("Конфигурация существует; используйте change-password.")
        username = input("Имя администратора: ").strip()
        if not username:
            raise RuntimeError("Имя не должно быть пустым.")
        cfg = {"username": username, "host": "127.0.0.1", "port": 8081, "threads": 12,
               "prefix": "", "external_https": True, "trusted_proxies": [], "session_hours": 8}
    password = getpass.getpass("Новый пароль (не менее 5 символов): ")
    confirm = getpass.getpass("Повторите пароль: ")
    if len(password) < 5 or password != confirm:
        raise RuntimeError("Пароли должны совпадать и содержать не менее 5 символов.")
    cfg["password_hash"] = generate_password_hash(password)
    cfg["session_key"] = secrets.token_hex(32)
    atomic_write(path, (json.dumps(cfg, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print("Администратор сохранён. Действующие сессии отозваны. Настройте HTTPS-прокси или явный локальный HTTP.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("serve", "init-admin", "change-password"), default="serve")
    parser.add_argument("--config", type=Path, default=LOCAL_CONFIG)
    parser.add_argument("--local-http", action="store_true", help="Явная loopback-проверка без HTTPS")
    args = parser.parse_args()
    try:
        if args.command != "serve":
            admin_command(args.config, change=args.command == "change-password")
            return
        cfg = load_server_config(args.config)
        if args.local_http:
            cfg = cfg | {"external_https": False, "host": "127.0.0.1", "trusted_proxies": []}
        app = create_app(cfg, config_path=args.config)
        from waitress import create_server
        try:
            server = create_server(app, host=cfg["host"], port=cfg["port"], threads=cfg["threads"],
                clear_untrusted_proxy_headers=False, channel_timeout=120)
        except OSError:
            raise RuntimeError(f"Не удалось слушать {cfg['host']}:{cfg['port']}. Порт занят или недоступен; другой порт не выбирается.") from None
        print(f"WebUI: {'HTTPS через прокси' if cfg['external_https'] else 'локальный HTTP'}; Waitress {cfg['host']}:{cfg['port']}{cfg['prefix']}/")
        try:
            server.run()
        finally:
            app.extensions["newsbot"]["hub"].close()
    except (RuntimeError, ValueError, TypeError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
