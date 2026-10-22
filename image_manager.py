import os
import re
from urllib.parse import unquote, urlsplit

import requests

from config import (
    WP_URL,
    WP_USERNAME,
    WP_APP_PASSWORD,
    WP_API_TIMEOUT,
)


TRANSLITERATION = {
    "а": "a", "б": "b", "в": "v", "г": "g",
    "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "j", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",

    "А": "A", "Б": "B", "В": "V", "Г": "G",
    "Д": "D", "Е": "E", "Ё": "E", "Ж": "Zh",
    "З": "Z", "И": "I", "Й": "J", "К": "K",
    "Л": "L", "М": "M", "Н": "N", "О": "O",
    "П": "P", "Р": "R", "С": "S", "Т": "T",
    "У": "U", "Ф": "F", "Х": "H", "Ц": "C",
    "Ч": "Ch", "Ш": "Sh", "Щ": "Sch",
    "Ъ": "", "Ы": "Y", "Ь": "", "Э": "E",
    "Ю": "Yu", "Я": "Ya",
}


def transliterate_filename(filename):

    result = "".join(
        TRANSLITERATION.get(
            char,
            char
        )
        for char in filename
    )

    result = re.sub(
        r"[^A-Za-z0-9._-]",
        "_",
        result
    )

    return result


def get_auth():

    return (
        WP_USERNAME,
        WP_APP_PASSWORD
    )


def get_default_media_id():
    """Find the existing exact filename, including paginated search results."""
    filename = "s600xu_2x.webp"
    page = 1
    while True:
        response = requests.get(
            f"{WP_URL}/wp-json/wp/v2/media",
            auth=get_auth(),
            params={"search": os.path.splitext(filename)[0], "per_page": 100,
                    "page": page, "orderby": "id", "order": "asc", "media_type": "image"},
            timeout=WP_API_TIMEOUT,
        )
        response.raise_for_status()
        items = response.json()
        if not isinstance(items, list):
            raise RuntimeError("WordPress вернул некорректный список медиа.")
        for media in items:
            details = media.get("media_details") or {}
            names = {
                os.path.basename(unquote(urlsplit(media.get("source_url") or "").path)),
                os.path.basename(details.get("file") or ""),
                details.get("original_image") or "",
            }
            if filename in names and isinstance(media.get("id"), int) and media["id"] > 0:
                return media["id"]
        total_pages = response.headers.get("X-WP-TotalPages")
        if (total_pages is not None and page >= int(total_pages)) or not items or (
            total_pages is None and len(items) < 100
        ):
            break
        page += 1
    raise RuntimeError(f"Стандартная картинка {filename} не найдена в медиатеке WordPress.")


def upload_media(
    image_path,
    filename=None
):

    if filename is None:
        filename = os.path.basename(
            image_path
        )

    safe_filename = transliterate_filename(
        filename
    )

    url = (
        f"{WP_URL}/wp-json/wp/v2/media"
    )

    with open(image_path, "rb") as f:

        response = requests.post(
            url,
            auth=get_auth(),
            headers={
                "Content-Disposition":
                    f'attachment; filename="{safe_filename}"'
            },
            files={
                "file": (
                    safe_filename,
                    f,
                    "image/jpeg"
                )
            },
            timeout=WP_API_TIMEOUT
        )

    response.raise_for_status()

    data = response.json()

    return {
        "wp_id": data["id"],
        "url": data.get("source_url"),
        "filename": safe_filename,
    }


def upload_media_bytes(
    data,
    filename,
    mime_type="image/jpeg"
):

    safe_filename = transliterate_filename(
        filename
    )

    url = (
        f"{WP_URL}/wp-json/wp/v2/media"
    )

    response = requests.post(
        url,
        auth=get_auth(),
        headers={
            "Content-Disposition":
                f'attachment; filename="{safe_filename}"'
        },
        files={
            "file": (
                safe_filename,
                data,
                mime_type
            )
        },
        timeout=WP_API_TIMEOUT
    )

    response.raise_for_status()

    result = response.json()

    return {
        "wp_id": result["id"],
        "url": result.get("source_url"),
        "filename": safe_filename,
    }
