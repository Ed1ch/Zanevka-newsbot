import html
import os
import re
from io import BytesIO

from docx import Document


# Полная VK-ссылка
VK_WALL_RE = re.compile(
    r'https?://[^\s<>"\']*wall-?\d+_\d+',
    re.IGNORECASE
)

# Фрагмент вида wall-188414783_53864
VK_WALL_FRAGMENT_RE = re.compile(
    r'wall(-?\d+)_(\d+)',
    re.IGNORECASE
)


def normalize_name(name):
    name = html.unescape(name)
    name = os.path.splitext(name)[0]
    name = name.strip().lower()
    name = re.sub(r'\s+', ' ', name)
    return name


def html_to_text(value):
    value = re.sub(r'<[^>]+>', ' ', value)
    value = html.unescape(value)
    value = re.sub(r'\s+', ' ', value)
    return value.strip()


def build_search_text(email_data):
    sources = []

    subject = email_data.get("subject", "")
    if subject:
        sources.append({
            "name": "Subject",
            "text": subject
        })

    text = email_data.get("text", "")
    if text:
        sources.append({
            "name": "text/plain",
            "text": text
        })

    html_source = email_data.get("html", "")
    if html_source:
        sources.append({
            "name": "text/html",
            "text": html_to_text(html_source)
        })

    return sources


def find_vk_links(text):
    return list(VK_WALL_RE.finditer(text))


def find_previous_vk_link(text, position):
    matches = list(
        VK_WALL_RE.finditer(
            text,
            0,
            position
        )
    )

    if not matches:
        return None

    return matches[-1]


def find_document_occurrences(text, documents):
    occurrences = []

    normalized_documents = []

    for document in documents:
        filename = document["filename"]
        normalized = normalize_name(filename)

        if not normalized:
            continue

        normalized_documents.append({
            "filename": filename,
            "normalized": normalized,
        })

    # Сначала более длинные имена.
    # Это защищает от частичного совпадения одного имени
    # внутри другого.
    normalized_documents.sort(
        key=lambda x: len(x["normalized"]),
        reverse=True
    )

    for document in normalized_documents:
        pattern = re.compile(
            re.escape(document["normalized"]),
            re.IGNORECASE
        )

        for match in pattern.finditer(text):
            occurrences.append({
                "filename": document["filename"],
                "normalized": document["normalized"],
                "start": match.start(),
                "end": match.end(),
            })

    return occurrences


def extract_vk_links(email_data, documents):
    """
    Старый механизм:
    ищет VK wall-ссылки в Subject / text/plain / text/html
    и связывает их с DOCX по ближайшей предыдущей VK-ссылке.
    """

    sources = build_search_text(email_data)

    unique_pairs = {}
    errors = []

    for source in sources:
        source_name = source["name"]
        text = source["text"]

        if not text:
            continue

        occurrences = find_document_occurrences(
            text,
            documents
        )

        for occurrence in occurrences:
            filename = occurrence["filename"]
            position = occurrence["start"]

            vk_match = find_previous_vk_link(
                text,
                position
            )

            if vk_match is None:
                errors.append({
                    "document": filename,
                    "source": source_name,
                    "message": (
                        f'Найдено имя DOCX "{filename}", '
                        f'но перед ним не найдена VK wall-ссылка.'
                    ),
                })
                continue

            url = vk_match.group(0)

            key = (
                filename.lower(),
                url.lower(),
            )

            if key not in unique_pairs:
                unique_pairs[key] = {
                    "document": filename,
                    "url": url,
                    "source": source_name,
                }

    if errors:
        lines = [
            "Не удалось сопоставить все VK-ссылки "
            "с DOCX."
        ]

        for error in errors:
            lines.append(
                f'\nDOCX: {error["document"]}'
            )
            lines.append(
                f'Источник: {error["source"]}'
            )
            lines.append(
                f'Ошибка: {error["message"]}'
            )

        raise RuntimeError(
            "\n".join(lines)
        )

    return list(unique_pairs.values())


def normalize_vk_url(value):
    """
    Превращает найденный wall-фрагмент в полноценную VK-ссылку.
    Если полноценная ссылка уже есть, оставляет её как есть.
    """

    value = html.unescape(value).strip()

    match = VK_WALL_RE.search(value)

    if match:
        return match.group(0)

    match = VK_WALL_FRAGMENT_RE.search(value)

    if match:
        owner_id = match.group(1)
        post_id = match.group(2)

        return (
            f"https://vk.ru/wall"
            f"{owner_id}_{post_id}"
        )

    return None


def extract_vk_links_from_docx(documents):
    """
    Новый механизм.

    Ищет VK wall-ссылки непосредственно внутри каждого DOCX.

    Проверяются два варианта:

    1. Видимый текст документа.
    2. Настоящие внешние hyperlink-адреса DOCX.

    Второй вариант важен, потому что Word может показывать
    пользователю текст ссылки, а настоящий URL хранить отдельно
    в relationships документа.

    Возвращает:

    [
        {
            "document": "уборка.docx",
            "url": "https://vk.ru/wall-188414783_53864",
            "source": "docx",
        }
    ]
    """

    results = []
    seen = set()

    for document_data in documents:
        filename = document_data["filename"]
        data = document_data["data"]

        document = Document(
            BytesIO(data)
        )

        # ---------------------------------------------------------
        # 1. Поиск VK wall в видимом тексте DOCX
        # ---------------------------------------------------------

        text_parts = []

        for element in document.element.body.iter():
            tag = element.tag

            if not isinstance(tag, str):
                continue

            # w:t = обычный текстовый узел Word
            if tag.endswith("}t"):
                if element.text:
                    text_parts.append(element.text)

        visible_text = "\n".join(text_parts)

        for match in VK_WALL_RE.finditer(visible_text):
            url = normalize_vk_url(
                match.group(0)
            )

            if not url:
                continue

            key = (
                filename.lower(),
                url.lower(),
            )

            if key not in seen:
                seen.add(key)

                results.append({
                    "document": filename,
                    "url": url,
                    "source": "docx",
                })

        # ---------------------------------------------------------
        # 2. Поиск URL настоящих DOCX hyperlinks
        # ---------------------------------------------------------

        for element in document.element.body.iter():
            tag = element.tag

            if not isinstance(tag, str):
                continue

            # w:hyperlink
            if not tag.endswith("}hyperlink"):
                continue

            # r:id
            rel_id = None

            for attribute_name, attribute_value in element.attrib.items():
                if attribute_name.endswith("}id"):
                    rel_id = attribute_value
                    break

            if not rel_id:
                continue

            relationship = document.part.rels.get(rel_id)

            if relationship is None:
                continue

            target = relationship.target_ref

            if not target:
                continue

            url = normalize_vk_url(target)

            if not url:
                continue

            key = (
                filename.lower(),
                url.lower(),
            )

            if key not in seen:
                seen.add(key)

                results.append({
                    "document": filename,
                    "url": url,
                    "source": "docx hyperlink",
                })

    return results