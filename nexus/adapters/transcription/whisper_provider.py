"""OpenAI Whisper-based transcription provider."""

import asyncio
import logging
from pathlib import Path
from typing import Any

from nexus.adapters.transcription.base import (
    TranscriptionInput,
    TranscriptionProvider,
    TranscriptionResult,
    TranscriptionSegment,
)

logger = logging.getLogger(__name__)

# Formats accepted by the Whisper API (as of 2024)
SUPPORTED_FORMATS = frozenset(
    {"mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm", "ogg", "ogg_vorbis"}
)


class WhisperTranscriptionProvider(TranscriptionProvider):
    """Transcription provider backed by OpenAI's Whisper API.

    Args:
        api_key: OpenAI API key.  If omitted, the ``OPENAI_API_KEY``
            environment variable is used.
        model: Whisper model identifier (default: ``"whisper-1"``).
        extra_kwargs: Additional keyword arguments forwarded to
            ``openai.AsyncOpenAI.audio.transcriptions.create``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "whisper-1",
        **extra_kwargs: Any,
    ) -> None:
        try:
            import openai  # noqa: F401 — checked at construction time
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "openai package is required for WhisperTranscriptionProvider. "
                "Install it with: pip install nexus-arc[openai]"
            ) from exc

        self._api_key = api_key
        self._model = model
        self._extra_kwargs: dict[str, Any] = extra_kwargs

    # ------------------------------------------------------------------
    # TranscriptionProvider interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "whisper"

    async def check_availability(self) -> bool:
        """Return True if the OpenAI API can be reached."""
        try:
            import openai

            client = openai.AsyncOpenAI(api_key=self._api_key)
            await client.models.retrieve(self._model)
            return True
        except Exception as exc:
            logger.warning("WhisperTranscriptionProvider not available: %s", exc)
            return False

    async def transcribe(self, audio_input: TranscriptionInput) -> TranscriptionResult:
        """Transcribe *audio_input* using the Whisper API.

        Supports:
        - ``Path`` — file is read from disk
        - ``bytes`` — used directly as in-memory audio
        - ``str``  — treated as a URL and fetched before sending

        Args:
            audio_input: Input specification.

        Returns:
            :class:`TranscriptionResult`.

        Raises:
            ValueError: If the audio format is not supported.
            RuntimeError: On upstream API errors.
        """
        import openai

        audio_bytes, filename = await self._resolve_source(audio_input)

        client = openai.AsyncOpenAI(api_key=self._api_key)

        kwargs: dict[str, Any] = dict(self._extra_kwargs)
        if audio_input.language:
            kwargs["language"] = audio_input.language
        # Request verbose_json to get segment-level detail when available
        kwargs["response_format"] = "verbose_json"

        logger.debug("WhisperTranscriptionProvider: submitting %s to %s", filename, self._model)

        try:
            response = await client.audio.transcriptions.create(
                model=self._model,
                file=(filename, audio_bytes),
                **kwargs,
            )
        except openai.OpenAIError as exc:
            raise RuntimeError(f"Whisper API error: {exc}") from exc

        segments = []
        raw_segments = getattr(response, "segments", None) or []
        for seg in raw_segments:
            segments.append(
                TranscriptionSegment(
                    start=float(seg.get("start", 0)),
                    end=float(seg.get("end", 0)),
                    text=seg.get("text", ""),
                )
            )

        return TranscriptionResult(
            text=response.text,
            language=getattr(response, "language", audio_input.language),
            duration_seconds=getattr(response, "duration", None),
            segments=segments,
            provider_used=self.name,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _resolve_source(self, audio_input: TranscriptionInput) -> tuple[bytes, str]:
        """Return (bytes, filename) from a Path, bytes, or URL source."""
        source = audio_input.source

        if isinstance(source, Path):
            ext = source.suffix.lstrip(".")
            if ext and ext.lower() not in SUPPORTED_FORMATS:
                raise ValueError(
                    f"Unsupported audio format {ext!r}. " f"Supported: {sorted(SUPPORTED_FORMATS)}"
                )
            return source.read_bytes(), source.name

        if isinstance(source, bytes):
            fmt_hint = audio_input.format or "auto"
            fmt = "mp3" if fmt_hint == "auto" else fmt_hint.lower()
            if fmt not in SUPPORTED_FORMATS:
                raise ValueError(
                    f"Unsupported audio format {fmt!r}. " f"Supported: {sorted(SUPPORTED_FORMATS)}"
                )
            return source, f"audio.{fmt}"

        if isinstance(source, str):
            # Restrict to http/https and block private/loopback IPs to prevent SSRF
            import ipaddress
            import socket
            import urllib.parse
            import urllib.request

            parsed = urllib.parse.urlparse(source)
            if parsed.scheme not in ("http", "https"):
                raise ValueError(
                    f"Unsupported URL scheme {parsed.scheme!r}. Only 'http' and 'https' are allowed."
                )
            hostname = parsed.hostname or ""
            try:
                ip = ipaddress.ip_address(socket.gethostbyname(hostname))
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    raise ValueError(
                        f"Requests to private/internal addresses are not allowed: {hostname!r}"
                    )
            except socket.gaierror as exc:
                raise ValueError(f"Unable to resolve hostname: {hostname!r}") from exc
            filename = source.split("/")[-1].split("?")[0] or "audio.mp3"

            def _fetch() -> bytes:
                req = urllib.request.Request(source)  # noqa: S310
                with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                    return resp.read()

            data = await asyncio.to_thread(_fetch)
            return data, filename

        raise TypeError(f"Unsupported source type: {type(source)}")
