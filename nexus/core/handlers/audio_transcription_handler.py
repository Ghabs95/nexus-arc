"""Audio transcription handlers extracted from telegram_bot."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from telegram.ext import ContextTypes


@dataclass
class AudioTranscriptionDeps:
    logger: Any
    transcribe_audio: Callable[[str], str | None]


async def transcribe_telegram_voice(
    voice_file_id: str,
    context: ContextTypes.DEFAULT_TYPE,
    deps: AudioTranscriptionDeps,
) -> str | None:
    """Download Telegram voice file and return transcribed text."""
    temp_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_file:
            temp_path = tmp_file.name

        new_file = await context.bot.get_file(voice_file_id)
        await new_file.download_to_drive(temp_path)

        deps.logger.info("🎧 Transcribing audio with orchestrator...")
        text = deps.transcribe_audio(temp_path)

        if text:
            cleaned = str(text).strip()
            deps.logger.info("✅ Transcription successful (%s chars)", len(cleaned))
            return cleaned

        deps.logger.error("❌ Transcription failed")
        return None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                deps.logger.warning("Failed to clean temp audio file: %s", temp_path)
