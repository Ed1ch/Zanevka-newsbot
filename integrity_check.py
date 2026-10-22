"""Pure analysis of the batch; no IMAP, log, flag or WordPress operations."""

from news_parser import find_matching_images


def check_integrity(documents, images, vk_documents=()):
    """Use existing matching rules and exempt documents with an associated VK."""
    matches = []
    matched_by_image = {image["filename"]: [] for image in images}
    documents_without_media = []
    vk_names = {name.casefold() for name in vk_documents}
    for document in documents:
        filename = document["filename"]
        matched = find_matching_images(filename, images)
        matches.append((filename, [image["filename"] for image in matched]))
        for image in matched:
            matched_by_image[image["filename"]].append(filename)
        if not matched and filename.casefold() not in vk_names:
            documents_without_media.append(filename)
    unmatched = [name for name, docs in matched_by_image.items() if not docs]
    ambiguous = {name: docs for name, docs in matched_by_image.items() if len(docs) > 1}
    return {
        "matches": matches,
        "unmatched_images": unmatched,
        "ambiguous_images": ambiguous,
        "documents_without_media": documents_without_media,
        "incomplete": bool(unmatched or documents_without_media),
    }
