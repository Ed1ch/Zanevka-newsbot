import os

from eml_parser import extract_attachments
from news_parser import (
    get_numeric_only_name,
    names_match,
    normalize_match_words,
)


EML_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "downloaded_eml",
)


def describe_name(filename):
    words = sorted(
        normalize_match_words(filename)
    )

    if words:
        return ", ".join(words)

    number = get_numeric_only_name(
        filename
    )

    if number is not None:
        return f"число: {number}"

    return "(нет значимых слов)"


def inspect_eml(eml_path):
    print()
    print("=" * 80)
    print(f"EML: {os.path.basename(eml_path)}")
    print("=" * 80)

    email_data = extract_attachments(
        eml_path
    )

    documents = email_data["documents"]
    images = email_data["images"]

    print(
        f"Subject: {email_data.get('subject', '')}"
    )
    print(
        f"DOCX найдено: {len(documents)}"
    )
    print(
        f"Изображений найдено: {len(images)}"
    )

    matched_by_image = {
        image["filename"]: []
        for image in images
    }

    for number, document in enumerate(
        documents,
        start=1
    ):
        doc_filename = document["filename"]

        matched_images = [
            image
            for image in images
            if names_match(
                doc_filename,
                image["filename"]
            )
        ]

        print()
        print("-" * 80)
        print(
            f"НОВОСТЬ #{number}: {doc_filename}"
        )
        print(
            "Нормализовано: "
            f"{describe_name(doc_filename)}"
        )

        if not matched_images:
            print("Картинки: НЕ НАЙДЕНЫ")
            continue

        print(
            f"Картинки: {len(matched_images)}"
        )

        for image in matched_images:
            image_filename = image["filename"]

            matched_by_image[
                image_filename
            ].append(doc_filename)

            print(
                f"  ✓ {image_filename}"
            )
            print(
                "    Нормализовано: "
                f"{describe_name(image_filename)}"
            )

    unmatched_images = [
        image_filename
        for image_filename, documents_for_image
        in matched_by_image.items()
        if not documents_for_image
    ]

    ambiguous_images = {
        image_filename: documents_for_image
        for image_filename, documents_for_image
        in matched_by_image.items()
        if len(documents_for_image) > 1
    }

    print()
    print("=" * 80)
    print("ПРОВЕРКА СОПОСТАВЛЕНИЯ")
    print("=" * 80)

    if unmatched_images:
        print("Непривязанные картинки:")

        for image_filename in unmatched_images:
            print(
                f"  ! {image_filename}"
            )
    else:
        print(
            "Непривязанных картинок нет."
        )

    if ambiguous_images:
        print()
        print(
            "ВНИМАНИЕ: картинки, подошедшие "
            "сразу к нескольким DOCX:"
        )

        for image_filename, doc_filenames in (
            ambiguous_images.items()
        ):
            print(
                f"  ! {image_filename}"
            )

            for doc_filename in doc_filenames:
                print(
                    f"      -> {doc_filename}"
                )
    else:
        print(
            "Неоднозначных сопоставлений нет."
        )


def main():
    print("=" * 80)
    print("ТЕСТ СОПОСТАВЛЕНИЯ DOCX И ИЗОБРАЖЕНИЙ")
    print("=" * 80)
    print()
    print(
        "Режим только чтения: WordPress, IMAP "
        "и удаление файлов не используются."
    )
    print(
        f"Папка EML: {EML_DIR}"
    )

    if not os.path.isdir(EML_DIR):
        print()
        print(
            "Папка downloaded_eml не найдена."
        )
        print(
            "Создайте её рядом со скриптом "
            "и положите внутрь .eml файл."
        )
        return

    eml_files = sorted(
        filename
        for filename in os.listdir(EML_DIR)
        if filename.lower().endswith(".eml")
    )

    if not eml_files:
        print()
        print(
            "В downloaded_eml нет файлов .eml."
        )
        return

    for filename in eml_files:
        inspect_eml(
            os.path.join(
                EML_DIR,
                filename
            )
        )

    print()
    print("=" * 80)
    print("ГОТОВО")
    print("=" * 80)


if __name__ == "__main__":
    main()
