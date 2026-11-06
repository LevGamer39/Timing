"""Голосовые сообщения: текст из MAX и распознавание через faster-whisper."""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from maxapi.enums.attachment import AttachmentType

logger = logging.getLogger("vector_bot.voice")


def _plain_text(body: Any) -> str | None:
    """Текст сообщения или готовая транскрипция MAX, без скачивания аудио."""
    if body is None:
        return None
    if body.text:
        return body.text
    for attachment in body.attachments or []:
        transcription = getattr(attachment, "transcription", None)
        if transcription:
            return transcription
    return None


def audio_url(body: Any) -> str | None:
    """URL первого голосового вложения."""
    if body is None:
        return None
    for attachment in body.attachments or []:
        if attachment.type != AttachmentType.AUDIO:
            continue
        payload = getattr(attachment, "payload", None)
        url = getattr(payload, "url", None)
        if url:
            return url
    return None


async def body_text(
    body: Any,
    transcriber: Transcriber | None,
    user_id: int,
    *,
    audio: bool = True,
) -> str | None:
    """Текст сообщения, транскрипция MAX или распознанное голосовое."""
    text = _plain_text(body)
    if text:
        return text
    if not audio or transcriber is None:
        return None
    url = audio_url(body)
    if not url:
        return None
    return await transcriber.transcribe(url, user_id)


class Transcriber:
    """Ленивая модель faster-whisper и распознавание одного аудиофайла."""

    def __init__(
        self,
        model: str,
        model_dir: str,
        device: str,
        compute_type: str,
        language: str,
        temp_dir: str,
        timeout: float,
    ) -> None:
        self._name = model
        self._model_dir = model_dir
        self._device = device
        self._compute_type = compute_type
        self._language = language
        self._temp_dir = Path(temp_dir)
        self._timeout = timeout
        self._model: Any | None = None
        self._load_lock = threading.Lock()
        self._infer_lock = asyncio.Lock()

    async def warmup(self) -> None:
        """Загружает модель заранее, чтобы первое голосовое не ждало."""
        await asyncio.to_thread(self._load)

    async def transcribe(self, url: str, user_id: int) -> str | None:
        """Скачивает аудио, распознаёт его и всегда удаляет временный файл."""
        # random.randint(0, 255) — восемь бит, как в имени файла.
        path = self._temp_dir / (
            f"audio_{user_id}_{int(time.time())}_"
            f"{random.randint(0, 255)}.ogg"
        )
        try:
            self._temp_dir.mkdir(parents=True, exist_ok=True)
            await self._download(url, path)
            async with self._infer_lock:
                text = await asyncio.to_thread(self._recognize, path)
        except Exception:  # noqa: BLE001 — сбой голосового не роняет бота
            logger.exception("Не удалось распознать голосовое %s", path.name)
            return None
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:  # Windows иногда держит файл открытым
                logger.warning(
                    "Не удалось удалить %s", path, exc_info=True
                )
        return text or None

    async def _download(self, url: str, path: Path) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            path.write_bytes(response.content)

    def _recognize(self, path: Path) -> str:
        model = self._load()
        segments, _ = model.transcribe(
            str(path),
            language=self._language,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    def _load(self) -> Any:
        with self._load_lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                logger.info("Загружаю faster-whisper %s", self._name)
                self._model = WhisperModel(
                    self._name,
                    device=self._device,
                    compute_type=self._compute_type,
                    download_root=self._model_dir,
                )
            return self._model
