import html
import mimetypes
import os
import requests
import log

from config import (
    OUTPUT_DIR,
    IMAP_OUTPUT_DIR,
    WP_URL,
)

from mail_downloader import (
    download_news_email,
    mark_email_failed,
    mark_email_processed,
)

from eml_parser import extract_attachments
from eml_links import (
    extract_vk_links,
    extract_vk_links_from_docx,
)
from news_parser import (
    build_news,
    find_matching_images,
)

from image_manager import (
    upload_media,
    upload_media_bytes,
)

from wordpress import create_post

from vk_video import (
    get_vk_video_from_post,
)


LOCK_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".main.lock",
)


def acquire_process_lock():
    """
    Захватывает системный lock для единственного экземпляра main.py.

    Сам файл .main.lock может оставаться на диске после завершения.
    Важен не факт его существования, а системная блокировка файла,
    которая автоматически освобождается ОС при завершении процесса.
    """
    lock_file = open(
        LOCK_FILE,
        "a+",
        encoding="utf-8",
    )

    lock_file.seek(0, os.SEEK_END)

    if lock_file.tell() == 0:
        lock_file.write("0")
        lock_file.flush()

    lock_file.seek(0)

    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(
                lock_file.fileno(),
                msvcrt.LK_NBLCK,
                1,
            )
        else:
            import fcntl

            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )

    except (OSError, IOError):
        lock_file.close()
        return None

    return lock_file


def release_process_lock(lock_file):
    if lock_file is None:
        return

    try:
        lock_file.seek(0)

        if os.name == "nt":
            import msvcrt

            msvcrt.locking(
                lock_file.fileno(),
                msvcrt.LK_UNLCK,
                1,
            )
        else:
            import fcntl

            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_UN,
            )
    finally:
        lock_file.close()


def download_vk_preview(url):
    response = requests.get(
        url,
        timeout=30
    )

    response.raise_for_status()

    content_type = response.headers.get(
        "Content-Type",
        "image/jpeg"
    )

    extension = (
        mimetypes.guess_extension(
            content_type.split(";")[0]
        )
        or ".jpg"
    )

    return (
        response.content,
        content_type,
        extension
    )


def process_vk(
    news,
    vk_info,
    force_preview_as_featured=False
):
    if not vk_info:
        return

    url = vk_info["url"]

    vk_data = get_vk_video_from_post(url)

    news["vk_data"] = vk_data

    player = vk_data.get("player")

    if player:
        news["vk_embed"] = (
            f'<iframe '
            f'src="{html.escape(player)}" '
            f'width="640" '
            f'height="360" '
            f'frameborder="0" '
            f'allowfullscreen></iframe>'
        )
        log.detail(f"VIDEO URL: {player}")

    preview = vk_data.get("preview")

    if not preview:
        return

    use_preview = (
        force_preview_as_featured
        or not news.get("featured_image")
    )

    if not use_preview:
        return

    log.detail("Загрузка VK preview...")

    preview_data, mime_type, extension = (
        download_vk_preview(preview)
    )

    media = upload_media_bytes(
        preview_data,
        "vk_preview" + extension,
        mime_type
    )

    news["featured_media_id"] = media["wp_id"]


def process_images(news):
    """
    Загружает каждое изображение новости в WordPress только один раз.

    Одна и та же картинка может одновременно использоваться как
    изображение записи и входить в галерею. В этом случае повторная
    загрузка в библиотеку WordPress не выполняется: используется тот же
    media ID.
    """
    uploaded_media_ids = {}

    def get_media_id(image_path):
        normalized_path = os.path.normcase(
            os.path.abspath(image_path)
        )

        if normalized_path not in uploaded_media_ids:
            media = upload_media(
                image_path
            )

            uploaded_media_ids[
                normalized_path
            ] = media["wp_id"]

        return uploaded_media_ids[
            normalized_path
        ]

    featured_image = news.get(
        "featured_image"
    )

    if featured_image:
        news["featured_media_id"] = (
            get_media_id(
                featured_image
            )
        )

    gallery_images = news.get(
        "gallery_images",
        []
    )

    gallery_media_ids = []

    for image_path in gallery_images:
        gallery_media_ids.append(
            get_media_id(
                image_path
            )
        )

    news["gallery_media_ids"] = (
        gallery_media_ids
    )


def cleanup_working_files(eml_path):
    """Удаляет временные файлы конкретной итерации."""
    if eml_path:
        try:
            if os.path.isfile(eml_path):
                os.remove(eml_path)
                log.detail(
                    f"Удалён локальный EML: {eml_path}"
                )
        except OSError as error:
            log.problem(
                f"Не удалось удалить {eml_path}", error,
            )

    if os.path.isdir(OUTPUT_DIR):
        for filename in os.listdir(
            OUTPUT_DIR
        ):
            path = os.path.join(
                OUTPUT_DIR,
                filename,
            )

            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError as error:
                log.problem(
                    f"Не удалось удалить {path}", error,
                )


def cleanup_downloaded_eml():
    """
    Удаляет все оставшиеся файлы из downloaded_eml.

    Это страховочная очистка всего каталога после завершения main.py,
    включая завершение с исключением.
    """
    if not os.path.isdir(IMAP_OUTPUT_DIR):
        return

    for filename in os.listdir(
        IMAP_OUTPUT_DIR
    ):
        path = os.path.join(
            IMAP_OUTPUT_DIR,
            filename,
        )

        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError as error:
            log.problem(
                f"Не удалось удалить {path}", error,
            )


def print_image_matching_report(documents, images):
    """
    Показывает привязку картинок до публикации.
    Отчёт информационный и не влияет на обработку новостей.
    """
    matches = []
    matched_by_image = {
        image["filename"]: []
        for image in images
    }

    for document in documents:
        doc_filename = document["filename"]
        matched_images = find_matching_images(
            doc_filename,
            images
        )

        matches.append((doc_filename, [image["filename"] for image in matched_images]))
        for image in matched_images:
            image_filename = image["filename"]
            matched_by_image[image_filename].append(
                doc_filename
            )

    unmatched_images = [
        filename
        for filename, matched_docs in matched_by_image.items()
        if not matched_docs
    ]

    ambiguous_images = {
        filename: matched_docs
        for filename, matched_docs in matched_by_image.items()
        if len(matched_docs) > 1
    }

    log.matching_report(matches, unmatched_images, ambiguous_images)



def process_email(eml_path):
    """Обрабатывает одно EML; ошибка отдельной новости не прерывает цикл."""
    email_data = extract_attachments(eml_path)
    documents = email_data["documents"]
    images = email_data["images"]
    log.attachments_found(len(documents), len(images))

    print_image_matching_report(documents, images)
    news_list = build_news(documents, images)
    log.detail(f"Новостей собрано: {len(news_list)}")

    vk_links = extract_vk_links(email_data, documents)
    vk_by_document = {item["document"]: item for item in vk_links}
    vk_links_from_docx = extract_vk_links_from_docx(documents)
    vk_by_document_from_docx = {item["document"]: item for item in vk_links_from_docx}
    log.vk_links_report(vk_links, vk_links_from_docx)
    log.block(f"ПУБЛИКАЦИЯ на {WP_URL} ...")

    email_ok = True
    for number, news in enumerate(news_list, start=1):
        log.news_started(number, news["title"])
        try:
            log.detail("[1] Загрузка изображений...")
            process_images(news)

            document_filename = news["document_filename"]
            vk_info = vk_by_document_from_docx.get(document_filename)
            force_preview_as_featured = False
            if vk_info:
                log.detail("[2] Поиск VK... найдена VK-ссылка внутри DOCX: "
                           f"{document_filename}")
                log.detail(f"URL: {vk_info['url']}")
                force_preview_as_featured = True
            else:
                vk_info = vk_by_document.get(document_filename)
                if vk_info:
                    log.detail("[2] Поиск VK... найдена VK-ссылка в письме для "
                               f"{document_filename}")
                    log.detail(f"URL: {vk_info['url']}")
                else:
                    log.detail("[2] Поиск VK... VK-ссылка для этой новости не найдена.")

            if vk_info:
                try:
                    process_vk(news, vk_info, force_preview_as_featured)
                except log.LogWriteError:
                    raise
                except Exception as error:
                    log.problem("ПРЕДУПРЕЖДЕНИЕ VK", error)
                    log.detail("Новость будет опубликована без VK-видео/preview.")
                    # Resume with a new INFO header so the continuation does not
                    # accidentally inherit WARNING in the future API reader.
                    log.news_started(number, news["title"], continued=True)

            log.detail("[3] Создание записи...")
            post = create_post(news)
            log.post_result(post["id"], post.get("link"), post.get("status"))
        except log.LogWriteError:
            raise
        except Exception as error:
            email_ok = False
            log.problem(f"Новость #{number} не обработана полностью", error)
            log.detail("Переходим к следующей новости письма.")

    return email_ok


def run_pipeline():
    processed_emails = 0
    failed_emails = 0
    mail_stats = {"failed_downloads": 0}

    while True:
        downloaded = download_news_email(stats=mail_stats)
        if downloaded is None:
            log.detail("Непрочитанных ЗГП-писем для обработки больше нет.")
            break

        uid, eml_path, subject = downloaded
        log.email_started(uid, subject, eml_path)
        email_ok = False
        try:
            email_ok = process_email(eml_path)
        except log.LogWriteError:
            raise
        except Exception as error:
            log.problem(f"Не удалось обработать письмо UID {uid}", error)
            email_ok = False
        finally:
            log.block(f"ЗАВЕРШЕНИЕ ОБРАБОТКИ ПИСЬМА UID {uid}")
            cleanup_working_files(eml_path)

        # Failures here propagate to main(): continuing would leave the mailbox
        # state uncertain. The final ERROR is emitted once, with its traceback.
        if email_ok:
            mark_email_processed(uid)
            processed_emails += 1
        else:
            mark_email_failed(uid)
            failed_emails += 1

    return processed_emails, failed_emails, mail_stats["failed_downloads"]


def main():
    lock_file = acquire_process_lock()
    if lock_file is None:
        # A duplicate run is intentionally silent and does not touch the journal.
        return

    logging_ready = False
    try:
        log.setup()
        logging_ready = True
        log.block("Process lock получен")
        log.detail("Workflow: CHECK INBOX → PROCESS EML → DOC+IMG MATCHING → "
                   "VK PARSE → WORDPRESS PUBLISHING")
        try:
            summary = run_pipeline()
        finally:
            # Both cleanups are attempted even if the first one raises.
            try:
                cleanup_working_files(None)
            finally:
                cleanup_downloaded_eml()

        processed, failed, downloads_failed = summary
        if processed or failed or downloads_failed:
            level = "WARNING" if failed or downloads_failed else "OK"
            log.run_finished(level, processed, failed, downloads_failed)
        elif log.current_path() is not None:
            log.run_finished("WARNING", 0, 0)
        else:
            log.block("Проверка завершена. Новых писем нет.")
    except KeyboardInterrupt as error:
        if logging_ready:
            log.problem("Запуск прерван пользователем", error)
        raise
    except Exception as error:
        if logging_ready:
            log.problem("Работа остановлена из-за ошибки", error, level="ERROR")
        raise
    finally:
        try:
            log.close()
        finally:
            release_process_lock(lock_file)


if __name__ == "__main__":
    main()
