import os
from email import policy
from email.parser import BytesParser

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
}

def save_image(image, doc_base, output_dir="extracted_images"):
    """
    Сохраняет изображение на диск.

    image:
        {
            "filename": "...",
            "data": b"..."
        }

    doc_base используется для формирования имени файла.
    """

    os.makedirs(output_dir, exist_ok=True)

    filename = image["filename"]
    data = image["data"]

    path = os.path.join(
        output_dir,
        filename
    )

    with open(path, "wb") as f:
        f.write(data)

    return path


def extract_attachments(eml_path):
    """
    Извлекает из EML:

        documents
        images
        text
        html
        subject
    """

    with open(eml_path, "rb") as f:
        msg = BytesParser(
            policy=policy.default.clone(raise_on_defect=True)
        ).parse(f)

    documents = []
    images = []

    text_parts = []
    html_parts = []

    subject = msg.get(
        "Subject",
        ""
    )

    for part in msg.walk():

        filename = part.get_filename()

        # ---------------------------------
        # TEXT PLAIN
        # ---------------------------------

        if part.get_content_type() == "text/plain":

            try:
                content = part.get_content()
            except Exception as error:
                raise ValueError("Не удалось разобрать text/plain в EML.") from error

            if content:
                text_parts.append(
                    content
                )

            continue

        # ---------------------------------
        # TEXT HTML
        # ---------------------------------

        if part.get_content_type() == "text/html":

            try:
                content = part.get_content()
            except Exception as error:
                raise ValueError("Не удалось разобрать text/html в EML.") from error

            if content:
                html_parts.append(
                    content
                )

            continue

        # ---------------------------------
        # ATTACHMENTS
        # ---------------------------------

        if not filename:
            continue

        filename = os.path.basename(
            filename
        )

        extension = os.path.splitext(
            filename
        )[1].lower()

        data = part.get_payload(
            decode=True
        )

        if not data:
            if extension == ".docx" or extension in IMAGE_EXTENSIONS:
                raise ValueError(f"Пустое или повреждённое вложение: {filename}")
            continue

        # ---------------------------------
        # DOCX
        # ---------------------------------

        if extension == ".docx":

            documents.append({
                "filename": filename,
                "data": data,
            })

        # ---------------------------------
        # IMAGES
        # ---------------------------------

        elif extension in IMAGE_EXTENSIONS:

            images.append({
                "filename": filename,
                "data": data,
            })

    return {
        "documents": documents,
        "images": images,
        "text": "\n".join(text_parts),
        "html": "\n".join(html_parts),
        "subject": subject,
    }
