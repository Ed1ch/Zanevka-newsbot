if __name__ == "__main__":
    from prelaunch import main as launch
    launch()
    raise SystemExit

import html
import mimetypes
import os
from pathlib import Path
import requests
import log

from config import (
    OUTPUT_DIR,
    IMAP_OUTPUT_DIR,
    WP_URL,
)

from mail_downloader import (
    download_news_batch,
    mark_email_unseen,
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
)

from image_manager import (
    upload_media,
    upload_media_bytes,
    get_default_media_id,
)

from integrity_check import check_integrity

from wordpress import create_post

from vk_video import (
    get_vk_video_from_post,
)


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
        if not news.get("featured_image"):
            raise RuntimeError("VK не вернул preview для новости без картинок.")
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


def process_batch(downloaded, *, defer_if_incomplete, delayed_publish_file):
    """Prepare all EML together; only then allow WordPress side effects."""
    documents = []
    images = []
    emails = []
    seen_names = set()
    for uid, eml_path, subject in downloaded:
        log.email_started(uid, subject, eml_path)
        email_data = extract_attachments(eml_path)
        emails.append(email_data)
        log.attachments_found(len(email_data["documents"]), len(email_data["images"]))
        for kind, target in (("documents", documents), ("images", images)):
            for item in email_data[kind]:
                key = item["filename"].casefold()
                if key in seen_names:
                    log.block(f"Дубль вложения пропущен: {item['filename']}", level="WARNING")
                    log.detail(f"UID {uid}: сохранено первое вложение с этим именем.")
                    continue
                seen_names.add(key)
                target.append(item)

    news_list = build_news(documents, images)
    log.detail(f"Новостей собрано в партии: {len(news_list)}")

    # Keep email text boundaries: a link in one EML cannot leak into another.
    vk_links = []
    for email_data in emails:
        vk_links.extend(extract_vk_links(email_data, documents))
    vk_by_document = {item["document"]: item for item in vk_links}
    vk_links_from_docx = extract_vk_links_from_docx(documents)
    vk_by_document_from_docx = {item["document"]: item for item in vk_links_from_docx}
    log.vk_links_report(vk_links, vk_links_from_docx)

    report = check_integrity(documents, images, set(vk_by_document) | set(vk_by_document_from_docx))
    log.matching_report(report["matches"], report["unmatched_images"], report["ambiguous_images"])
    if report["documents_without_media"]:
        log.detail("DOCX без картинок и без VK:")
        for filename in report["documents_without_media"]:
            log.detail(f"  ! {filename}")
    if defer_if_incomplete and report["incomplete"]:
        # Only a successful UNSEEN operation for every member permits the flag.
        for uid, _, _ in downloaded:
            mark_email_unseen(uid)
        with Path(delayed_publish_file).open("x", encoding="utf-8"):
            pass
        log.block("ПУБЛИКАЦИЯ ОТЛОЖЕНА", level="WARNING")
        log.detail("Неполное соответствие картинок и DOCX. Публикация не начиналась.")
        log.detail(f"Возвращено UNSEEN писем: {len(downloaded)}")
        log.detail("Повторная обработка — при следующем запуске (по расписанию через 2 часа).")
        return None

    log.block(f"ПУБЛИКАЦИЯ ПАРТИИ на {WP_URL} ...")
    default_media_id = None
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
                    if not news.get("featured_image"):
                        raise
                    log.problem("ПРЕДУПРЕЖДЕНИЕ VK", error)
                    log.detail("Новость будет опубликована без VK-видео/preview.")
                    # Resume with a new INFO header so the continuation does not
                    # accidentally inherit WARNING in the future API reader.
                    log.news_started(number, news["title"], continued=True)

            if vk_info and not news.get("featured_image") and not news.get("featured_media_id"):
                raise RuntimeError("Для новости с VK не получено изображение preview.")
            if not news.get("featured_image") and not vk_info:
                if default_media_id is None:
                    default_media_id = get_default_media_id()
                news["featured_media_id"] = default_media_id
                log.detail(f"Стандартная картинка из медиатеки: ID {default_media_id}")

            log.detail("[3] Создание записи...")
            post = create_post(news)
            log.post_result(post["id"], post.get("link"), post.get("status"))
        except log.LogWriteError:
            raise
        except Exception as error:
            email_ok = False
            log.problem(f"Новость #{number} не обработана полностью", error)
            log.detail("Переходим к следующей новости партии.")

    return email_ok


def run_pipeline(*, defer_if_incomplete, delayed_publish_file):
    downloaded = download_news_batch()
    if not downloaded:
        return 0, 0, 0, 0

    batch_ok = process_batch(
        downloaded,
        defer_if_incomplete=defer_if_incomplete,
        delayed_publish_file=delayed_publish_file,
    )
    if batch_ok is None:
        return 0, 0, 0, len(downloaded)

    log.block("ЗАВЕРШЕНИЕ ОБРАБОТКИ ПАРТИИ")
    for uid, _, _ in downloaded:
        if batch_ok:
            mark_email_processed(uid)
        else:
            mark_email_failed(uid)
    return (len(downloaded), 0, 0, 0) if batch_ok else (0, len(downloaded), 0, 0)


def main(*, defer_if_incomplete=True, delayed_publish_file=None):
    """Internal pipeline entry; external callers use prelaunch.main()."""
    delayed_publish_file = Path(delayed_publish_file) if delayed_publish_file is not None else (
        Path(__file__).resolve().parent / "flags" / "delayed.publish"
    )
    logging_ready = False
    try:
        logging_ready = True
        log.block("Process lock получен")
        log.detail("Режим: " + ("первый проход, отсрочка разрешена" if defer_if_incomplete else
                                "повторный проход, отсрочка запрещена"))
        log.detail("Workflow: CHECK INBOX → DOWNLOAD BATCH → DOC+IMG MATCHING → "
                   "VK PARSE → INTEGRITY CHECK → WORDPRESS PUBLISHING")
        try:
            if not defer_if_incomplete:
                delayed_publish_file.unlink()
                log.detail("Одноразовый delayed.publish погашен.")
            summary = run_pipeline(
                defer_if_incomplete=defer_if_incomplete,
                delayed_publish_file=delayed_publish_file,
            )
        finally:
            # Both cleanups are attempted even if the first one raises.
            try:
                cleanup_working_files(None)
            finally:
                cleanup_downloaded_eml()

        processed, failed, downloads_failed, delayed = summary
        if delayed:
            log.block("ЗАПУСК ЗАВЕРШЁН: ПУБЛИКАЦИЯ ОТЛОЖЕНА", level="WARNING")
            log.detail(f"Писем отложено: {delayed}")
            log.detail('Для форсированной публикации нажмите кнопку "Запустить сейчас"')
        elif processed or failed or downloads_failed:
            level = "WARNING" if failed or downloads_failed else "OK"
            log.run_finished(level, processed, failed, downloads_failed)
        elif log.has_problems():
            log.run_finished("WARNING", 0, 0)
        else:
            log.block("Проверка завершена. Новых писем нет.")
        return not any(summary) and defer_if_incomplete and not log.has_problems()
    except KeyboardInterrupt as error:
        if logging_ready:
            log.problem("Запуск прерван пользователем", error)
        raise
    except Exception as error:
        if logging_ready:
            log.problem("Работа остановлена из-за ошибки", error, level="ERROR")
        raise
