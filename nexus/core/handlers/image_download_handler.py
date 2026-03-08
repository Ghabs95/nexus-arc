"""Image download handler for Telegram photo messages."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from nexus.core.models import ImageAttachment


@dataclass
class ImageDownloadDeps:
    logger: Any


async def download_telegram_photo(
    photo_file_id: str,
    context: Any,
    deps: ImageDownloadDeps,
    index: int = 0,
) -> ImageAttachment | None:
    """Retrieve metadata for a Telegram photo and return an ImageAttachment."""
    try:
        new_file = await context.bot.get_file(photo_file_id)
        file_path = getattr(new_file, "file_path", "") or ""
        filename = os.path.basename(file_path) or f"photo_{index + 1}.jpg"
        deps.logger.info("📷 Photo attachment captured: %s (file_id=%s)", filename, photo_file_id)
        return ImageAttachment(
            file_id=photo_file_id,
            filename=filename,
            mime_type="image/jpeg",
        )
    except Exception as exc:
        deps.logger.warning("Failed to get Telegram photo info: %s", exc)
        return None


async def collect_telegram_photos(
    message: Any,
    context: Any,
    deps: ImageDownloadDeps,
) -> list[ImageAttachment]:
    """Collect image attachments from a Telegram message.

    A Telegram photo message carries a list of PhotoSize objects at different
    resolutions.  We always capture the highest-resolution version (last item).
    """
    photos = getattr(message, "photo", None)
    if not photos:
        return []
    largest = photos[-1]
    attachment = await download_telegram_photo(
        photo_file_id=largest.file_id,
        context=context,
        deps=deps,
        index=0,
    )
    return [attachment] if attachment else []
