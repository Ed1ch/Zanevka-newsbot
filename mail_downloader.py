import base64
import imaplib
import os
import socket
import time
import log
from email import message_from_bytes
from email.header import decode_header, make_header

from config import (
    IMAP_HOST,
    IMAP_PORT,
    IMAP_USERNAME,
    IMAP_PASSWORD,
    IMAP_MAILBOX,
    IMAP_TRASH,
    IMAP_SUBJECT,
    IMAP_OUTPUT_DIR,
    IMAP_TIMEOUT,
    IMAP_RETRY_COUNT,
    IMAP_RETRY_DELAY,
)


def ensure_ok(status, message):
    if status != "OK":
        raise RuntimeError(message)


def encode_imap_utf7(value):
    """Кодирует человекочитаемое имя IMAP-папки в Modified UTF-7."""
    result = []
    buffer = []

    def flush_buffer():
        if not buffer:
            return

        text = "".join(buffer)
        encoded = base64.b64encode(
            text.encode("utf-16-be")
        ).decode("ascii")
        encoded = encoded.rstrip("=").replace("/", ",")
        result.append(f"&{encoded}-")
        buffer.clear()

    for char in value:
        code = ord(char)

        if 0x20 <= code <= 0x7E:
            flush_buffer()
            result.append("&-" if char == "&" else char)
        else:
            buffer.append(char)

    flush_buffer()
    return "".join(result)


def mailbox_arg(value):
    """Готовит имя mailbox для передачи imaplib."""
    encoded = encode_imap_utf7(value)
    encoded = encoded.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{encoded}"'


def close_mail(mail):
    if mail is None:
        return

    try:
        mail.close()
    except Exception:
        pass

    try:
        mail.logout()
    except Exception:
        pass


def connect_mail():
    """Подключается к IMAP и открывает рабочий mailbox."""
    mail = imaplib.IMAP4_SSL(
        IMAP_HOST,
        IMAP_PORT,
        timeout=IMAP_TIMEOUT,
    )

    try:
        status, _ = mail.login(
            IMAP_USERNAME,
            IMAP_PASSWORD,
        )
        ensure_ok(status, "Ошибка IMAP-авторизации.")

        status, _ = mail.select(
            mailbox_arg(IMAP_MAILBOX)
        )
        ensure_ok(
            status,
            f"Не удалось открыть почтовый ящик: {IMAP_MAILBOX}",
        )

        return mail

    except Exception:
        close_mail(mail)
        raise


def decode_subject(raw_headers):
    """Декодирует MIME Subject в Unicode."""
    message = message_from_bytes(raw_headers)
    raw_subject = message.get("Subject", "")

    try:
        return str(make_header(decode_header(raw_subject)))
    except Exception:
        return raw_subject


def extract_raw_email(data):
    for response_part in data:
        if (
            isinstance(response_part, tuple)
            and len(response_part) >= 2
            and isinstance(response_part[1], (bytes, bytearray))
        ):
            return bytes(response_part[1])

    raise RuntimeError("IMAP не вернул содержимое письма.")


def search_unseen_news(mail):
    """
    Возвращает подходящие UNSEEN-письма как (uid, subject).

    Кириллический Subject фильтруется локально, чтобы не зависеть
    от поддержки UTF-8 в IMAP SEARCH конкретного сервера.
    """
    status, data = mail.uid(
        "search",
        None,
        "UNSEEN",
    )
    ensure_ok(
        status,
        "Ошибка поиска непрочитанных писем в IMAP.",
    )

    if not data or not data[0]:
        return []

    uid_values = data[0].split()
    uid_values.sort(key=lambda value: int(value))

    matches = []

    for uid_bytes in uid_values:
        uid = uid_bytes.decode("ascii")

        status, fetch_data = mail.uid(
            "fetch",
            uid,
            "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])",
        )
        ensure_ok(
            status,
            f"Не удалось получить Subject письма UID {uid}.",
        )

        try:
            raw_headers = extract_raw_email(fetch_data)
        except RuntimeError:
            log.block(f"UID {uid}: Subject получить не удалось, письмо пропущено.",
                      level="WARNING")
            continue

        subject = decode_subject(raw_headers)

        if IMAP_SUBJECT.casefold() in subject.casefold():
            matches.append((uid, subject))

    return matches


def find_oldest_unseen():
    """Возвращает (uid, subject) самого старого подходящего письма."""
    mail = None

    try:
        mail = connect_mail()
        matches = search_unseen_news(mail)

        if not matches:
            return None

        return matches[0]

    finally:
        close_mail(mail)


def cleanup_part(temp_path):
    if not temp_path or not os.path.exists(temp_path):
        return

    try:
        os.remove(temp_path)
    except OSError as error:
        log.problem(f"Не удалось удалить временный файл {temp_path}", error)


def cleanup_eml(path):
    if not path or not os.path.isfile(path):
        return

    try:
        os.remove(path)
    except OSError as error:
        log.problem(f"Не удалось удалить локальный EML {path}", error)


def download_by_uid(mail, uid):
    """
    Скачивает письмо через BODY.PEEK[] без установки \\Seen.
    Пишет сначала .part, затем атомарно создаёт готовый .eml.
    """
    os.makedirs(IMAP_OUTPUT_DIR, exist_ok=True)

    filename = f"ЗГП_{uid}.eml"
    output_path = os.path.join(
        IMAP_OUTPUT_DIR,
        filename,
    )
    temp_path = output_path + ".part"

    cleanup_part(temp_path)

    try:
        status, data = mail.uid(
            "fetch",
            str(uid),
            "(BODY.PEEK[])",
        )
        ensure_ok(
            status,
            f"Не удалось получить содержимое письма UID {uid}.",
        )

        raw_email = extract_raw_email(data)

        with open(temp_path, "wb") as file:
            file.write(raw_email)
            file.flush()
            os.fsync(file.fileno())

        os.replace(
            temp_path,
            output_path,
        )

        return output_path

    except Exception:
        cleanup_part(temp_path)
        raise


def uid_is_seen(mail, uid):
    """Проверяет, что сервер действительно сохранил флаг \\Seen."""
    status, data = mail.uid(
        "fetch",
        str(uid),
        "(FLAGS)",
    )
    ensure_ok(
        status,
        f"Не удалось проверить FLAGS письма UID {uid}.",
    )

    for part in data:
        if isinstance(part, tuple):
            values = part
        else:
            values = (part,)

        for value in values:
            if isinstance(value, (bytes, bytearray)):
                if b"\\Seen" in value:
                    return True

    return False


def mark_seen_by_uid(mail, uid):
    status, _ = mail.uid(
        "store",
        str(uid),
        "+FLAGS",
        "(\\Seen)",
    )
    ensure_ok(
        status,
        f"Не удалось пометить письмо UID {uid} как прочитанное.",
    )

    if not uid_is_seen(mail, uid):
        raise RuntimeError(
            f"IMAP-сервер не подтвердил флаг SEEN для UID {uid}."
        )


def move_to_trash_by_uid(mail, uid):
    """Перемещает письмо в корзину через COPY -> Deleted -> EXPUNGE."""
    trash_arg = mailbox_arg(IMAP_TRASH)

    status, _ = mail.uid(
        "copy",
        str(uid),
        trash_arg,
    )
    ensure_ok(
        status,
        f"Не удалось скопировать письмо UID {uid} "
        f"в корзину: {IMAP_TRASH}",
    )

    status, _ = mail.uid(
        "store",
        str(uid),
        "+FLAGS",
        "(\\Deleted)",
    )
    ensure_ok(
        status,
        f"Не удалось пометить письмо UID {uid} на удаление.",
    )

    status, _ = mail.expunge()
    ensure_ok(
        status,
        "Ошибка EXPUNGE.",
    )


def download_with_retries(uid):
    """
    Скачивает письмо с retry.

    После исчерпания попыток письмо помечается SEEN и остаётся
    в INBOX как индикатор ошибки.
    """
    last_error = None

    for attempt in range(1, IMAP_RETRY_COUNT + 1):
        mail = None
        if attempt > 1:
            log.block(f"ПОВТОРНОЕ СКАЧИВАНИЕ UID {uid}")
        log.detail(
            f"Скачивание UID {uid}: "
            f"попытка {attempt}/{IMAP_RETRY_COUNT}..."
        )

        try:
            mail = connect_mail()
            output_path = download_by_uid(
                mail,
                uid,
            )
            log.detail(
                f"Письмо скачано: {output_path}"
            )
            return output_path

        except log.LogWriteError:
            raise
        except (
            TimeoutError,
            socket.timeout,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.error,
            OSError,
            RuntimeError,
        ) as error:
            last_error = error
            log.problem(f"Ошибка скачивания UID {uid}: "
                        f"попытка {attempt}/{IMAP_RETRY_COUNT}", error)

        finally:
            close_mail(mail)

        if attempt < IMAP_RETRY_COUNT:
            log.detail(
                f"Повтор через {IMAP_RETRY_DELAY} сек."
            )
            time.sleep(IMAP_RETRY_DELAY)

    mail = None

    try:
        mail = connect_mail()
        mark_seen_by_uid(
            mail,
            uid,
        )
        log.block(f"Не удалось скачать письмо UID {uid}",
                  level="WARNING")
        log.detail(
            f"UID {uid}: загрузка не удалась; "
            f"письмо оставлено в {IMAP_MAILBOX} как SEEN."
        )
    finally:
        close_mail(mail)

    if last_error is not None:
        log.detail(
            f"Последняя ошибка: "
            f"{type(last_error).__name__}: {last_error}"
        )

    return None


def reserve_downloaded_email(uid, output_path):
    """
    Резервирует успешно скачанное письмо перед публикацией.

    Критически важно: WordPress-конвейер не должен стартовать,
    пока сервер не подтвердил \\Seen.
    """
    mail = None

    try:
        mail = connect_mail()
        mark_seen_by_uid(
            mail,
            uid,
        )
        log.detail(
            f"UID {uid}: помечено SEEN до запуска публикации."
        )

    except Exception:
        # Публикация ещё не начиналась. Если не удалось надёжно
        # зарезервировать письмо, локальный EML удаляем и останавливаемся.
        cleanup_eml(output_path)
        raise

    finally:
        close_mail(mail)


def download_news_email(stats=None):
    """
    Находит и скачивает самое старое UNSEEN-письмо с IMAP_SUBJECT.

    После успешного скачивания письмо ОБЯЗАТЕЛЬНО переводится в SEEN
    до возврата управления вызывающему коду. Только после этого можно
    запускать WordPress-публикацию.

    Если конкретное письмо не удалось скачать после всех retry,
    оно становится SEEN и функция ищет следующее UNSEEN-письмо.

    Возвращает:
        (uid, eml_path, subject)
    либо:
        None

    Необязательный stats — словарь вызывающего конвейера. В нём суммируется
    failed_downloads после исчерпания retry; прежний вызов без аргументов
    и возвращаемые значения сохранены.
    """
    while True:
        log.block(f"ПРОВЕРКА INBOX {IMAP_USERNAME} ...")
        found = find_oldest_unseen()

        if found is None:
            return None

        uid, subject = found

        log.activate()
        log.detail(
            f"Найдено письмо UID {uid}: {subject}"
        )

        output_path = download_with_retries(
            uid
        )

        if output_path is not None:
            reserve_downloaded_email(
                uid,
                output_path,
            )

            return (
                uid,
                output_path,
                subject,
            )

        if stats is not None:
            stats["failed_downloads"] = stats.get("failed_downloads", 0) + 1
        log.detail(
            "Переходим к следующему "
            "непрочитанному письму."
        )


def mark_email_failed(uid):
    """
    Оставляет критически не обработанное письмо как SEEN + INBOX.

    В нормальном потоке письмо уже SEEN ещё до запуска WordPress.
    Повторная установка флага служит дополнительной проверкой состояния.
    """
    mail = None

    try:
        mail = connect_mail()
        mark_seen_by_uid(
            mail,
            uid,
        )
        log.detail(
            f"UID {uid}: оставлено в "
            f"{IMAP_MAILBOX} как SEEN."
        )
    finally:
        close_mail(mail)


def mark_email_processed(uid):
    """
    Перемещает полностью успешно обработанное письмо в корзину.

    Даже если перенос в корзину завершится ошибкой, письмо уже было
    помечено SEEN до публикации и не должно автоматически публиковаться
    повторно.
    """
    mail = None

    try:
        mail = connect_mail()
        move_to_trash_by_uid(
            mail,
            uid,
        )
        log.detail(f"UID {uid}: перемещено в корзину почтового ящика.")
    finally:
        close_mail(mail)

