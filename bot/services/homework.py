"""Парсер домашних заданий в чатах класса."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from maxapi import Bot
from maxapi.enums.parse_mode import Format
from maxapi.types.updates.message_created import MessageCreated

from config import settings
from core.state import StateStore
from core.ui import is_dialog
from keyboards import homework_keyboard
from services.ai import looks_like_homework, parse_batch, parse_task
from services.voice import Transcriber, audio_url, body_text
from texts import PROCESSING_TEXT, esc

logger = logging.getLogger("timing_bot.homework")

MAX_BUFFER = 50


def _user_id(message: Any) -> int:
    sender = getattr(message, "sender", None)
    return int(getattr(sender, "user_id", 0) or 0)


class HomeworkParser:
    """Буферизация сообщений и пакетный разбор ДЗ."""

    def __init__(
        self,
        bot: Bot,
        store: StateStore,
        transcriber: Transcriber | None = None,
    ) -> None:
        self._bot = bot
        self._store = store
        self._transcriber = transcriber
        self._buffers: dict[int, list[dict[str, str]]] = {}
        self._loops: dict[int, asyncio.Task[None]] = {}
        self._pending: dict[int, dict[str, Any]] = {}
        self._counter = 0
        # В ответе MAX может не быть вложений, поэтому расшифровки
        # голосовых храним по mid до востребования.
        self._texts: dict[str, str] = {}

    def remember(self, mid: str | None, text: str | None) -> None:
        """Запоминает текст сообщения по mid для ответов командой /hw."""
        if not mid or not text:
            return
        self._texts[mid] = text
        while len(self._texts) > MAX_BUFFER * 4:
            self._texts.pop(next(iter(self._texts)))

    async def enable(self, chat_id: int, title: str | None = None) -> None:
        self._store.set_homework_chat(chat_id, True, title)
        self._ensure_loop(chat_id)
        await self._store.save()

    async def disable(self, chat_id: int) -> None:
        self._store.set_homework_chat(chat_id, False)
        loop = self._loops.pop(chat_id, None)
        if loop:
            loop.cancel()
        self._buffers.pop(chat_id, None)
        await self._store.save()

    def _ensure_loop(self, chat_id: int) -> None:
        if not settings.parser_enabled:
            return
        existing = self._loops.get(chat_id)
        if existing and not existing.done():
            return
        self._loops[chat_id] = asyncio.create_task(self._loop(chat_id))

    async def start_existing(self) -> None:
        for chat_id in self._store.homework_chats():
            self._ensure_loop(chat_id)

    async def stop(self) -> None:
        for task in self._loops.values():
            task.cancel()
        self._loops.clear()

    async def feed(self, event: MessageCreated) -> None:
        if not settings.parser_enabled:
            return
        chat_id = event.message.recipient.chat_id
        if chat_id is None or not self._store.is_homework_chat(chat_id):
            return

        text = await body_text(
            event.message.body,
            self._transcriber,
            _user_id(event.message),
            audio=settings.parser_voice_enabled,
        )
        self.remember(getattr(event.message.body, "mid", None), text)
        if not text or not looks_like_homework(text):
            return

        buffer = self._buffers.setdefault(chat_id, [])
        if any(item["text"] == text for item in buffer):
            return
        buffer.append(
            {
                "text": text,
                "mid": event.message.body.mid if event.message.body else "",
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
        del buffer[:-MAX_BUFFER]
        logger.info("ДЗ-буфер чата %s: %d сообщений", chat_id, len(buffer))

    async def feed_edited(self, event: Any) -> None:
        """При редактировании сообщения заменяет текст в буфере."""

        message = event.message
        chat_id = message.recipient.chat_id
        if chat_id is None:
            return
        buffer = self._buffers.get(chat_id)
        if not buffer or message.body is None:
            return

        text = await body_text(
            message.body,
            self._transcriber,
            _user_id(message),
            audio=settings.parser_voice_enabled,
        )
        self.remember(message.body.mid, text)
        if not text:
            return
        for item in buffer:
            if item["mid"] == message.body.mid:
                item["text"] = text
                return
        if looks_like_homework(text):
            buffer.append(
                {
                    "text": text,
                    "mid": message.body.mid,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )

    async def _fetch(self, mid: str) -> Any | None:
        """Догружает сообщение по mid: в ответе бывает только он."""
        if self._bot is None:
            return None
        try:
            found = await self._bot.get_messages(message_ids=[mid])
        except Exception:  # noqa: BLE001 — останется подсказка
            logger.warning("Не удалось догрузить %s", mid, exc_info=True)
            return None
        if not found.messages:
            return None
        return found.messages[0].body

    async def _linked_source(self, message: Any) -> tuple[Any, bool]:
        """Тело ответа и признак, что /hw отправлен ответом."""
        linked = (
            getattr(message.link, "message", None) if message.link else None
        )
        if linked is None:
            return message.body, False
        if linked.text or audio_url(linked):
            return linked, True
        mid = getattr(linked, "mid", None) or ""
        # Сначала своя расшифровка: голосовое бот уже слышал при отправке.
        cached = self._texts.get(mid)
        if cached:
            return SimpleNamespace(text=cached, attachments=[]), True
        full = await self._fetch(mid) if mid else None
        return (full if full is not None else linked), True

    async def manual(self, event: MessageCreated) -> None:
        message = event.message
        # Ответ на сообщение разбираем целиком (текст или голосовое);
        # иначе берём аргументы самой команды /hw.
        source, is_reply = await self._linked_source(message)
        announced = bool(audio_url(source))
        if announced:
            # Голосовое распознаётся дольше текста — отвечаем сразу.
            await message.answer(PROCESSING_TEXT)
        text = await body_text(
            source,
            self._transcriber,
            _user_id(message),
        ) or ""
        if not is_reply and text.lower().startswith("/hw"):
            text = text[3:].strip()
        if not text:
            await message.answer(
                "Ответьте командой /hw на сообщение с заданием "
                "(текстом или голосовым)."
            )
            return

        if not announced:
            await message.answer(PROCESSING_TEXT)
        parsed = await parse_task(text)
        parsed["chat_id"] = message.recipient.chat_id
        await self._send_card(parsed, source=text, home=is_dialog(event))

    async def _loop(self, chat_id: int) -> None:
        interval = max(1, settings.parser_batch_interval_minutes) * 60
        while True:
            await asyncio.sleep(interval)
            try:
                await self.flush(chat_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — цикл не должен умирать
                logger.exception("Ошибка разбора чата %s", chat_id)

    async def flush(self, chat_id: int) -> None:
        buffer = self._buffers.get(chat_id)
        if not buffer:
            return
        batch = buffer[: settings.parser_max_batch]
        del buffer[: len(batch)]
        texts = [item["text"] for item in batch]
        logger.info("Разбор %d сообщений чата %s", len(texts), chat_id)

        found = await parse_batch(texts)
        # Раньше отправлялись только первые пять найденных заданий, остальные
        # молча терялись вместе с очищенным буфером.
        for item in found:
            item["chat_id"] = chat_id
            await self._send_card(item, source=item.get("source", ""))

    async def _send_card(
        self, item: dict[str, Any], source: str, home: bool = False
    ) -> None:
        chat_id = item.get("chat_id")
        if chat_id is None:
            return

        self._counter += 1
        uid = self._counter
        self._pending[uid] = item
        if len(self._pending) > 200:
            for old in sorted(self._pending)[:50]:
                self._pending.pop(old, None)

        subject = item.get("subject") or "без предмета"
        deadline = item.get("deadline") or "без срока"
        lines = [
            "📚 <b>Похоже на домашнее задание</b>",
            f"<b>{esc(item.get('title', 'Задание'))}</b>",
            f"Предмет: {esc(subject)}",
            f"Срок: {esc(deadline)}",
        ]
        if source and source != item.get("title"):
            lines.append(f"<i>«{esc(source[:300])}»</i>")

        try:
            await self._bot.send_message(
                chat_id=int(chat_id),
                text="\n".join(lines),
                attachments=[homework_keyboard(uid, home=home)],
                format=Format.HTML,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось отправить карточку ДЗ в чат %s", chat_id)

    def pop_pending(self, uid: int) -> dict[str, Any] | None:
        return self._pending.pop(uid, None)
