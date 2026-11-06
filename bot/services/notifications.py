"""Доставка уведомлений из API в MAX.

Очередь живёт в бэкенде: события (приём работы, ожидание проверки, сводка)
создаются там, где происходят. Бот забирает свои строки и подтверждает
доставку — иначе он не узнал бы о событиях, к которым не имеет доступа.

Напоминания о сроках остаются в ``ReminderScheduler``: бот видит их сам,
и дублировать их в очередь незачем.
"""

from __future__ import annotations

import asyncio
import logging

from maxapi import Bot
from maxapi.enums.parse_mode import Format

from core.sessions import SessionManager
from core.state import StateStore
from services.backend import Backend, BackendError
from texts import esc

logger = logging.getLogger("timing_bot.notifications")


class NotificationRelay:
    """Раз в минуту забирает очередь MAX и отправляет её в чаты."""

    def __init__(
        self,
        bot: Bot,
        backend: Backend,
        sessions: SessionManager,
        store: StateStore,
    ) -> None:
        self.bot = bot
        self.backend = backend
        self.sessions = sessions
        self.store = store
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("Доставка уведомлений запущена")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.deliver()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Ошибка доставки уведомлений")
            await asyncio.sleep(60)

    async def deliver(self) -> None:
        for user in self.store.users():
            chat_id = user.get("chat_id")
            if not chat_id:
                continue
            max_user_id = int(user["max_user_id"])
            token = await self.sessions.token(max_user_id)
            if token is None:
                continue
            try:
                items = await self.backend.pending_notifications(token)
            except BackendError as exc:
                logger.warning("Очередь %s: %s", max_user_id, exc.detail)
                continue
            if not items:
                continue

            delivered: list[int] = []
            for item in items:
                text = (
                    f"<b>{esc(item.get('title', ''))}</b>\n"
                    f"{esc(item.get('body', ''))}"
                )
                try:
                    await self.bot.send_message(
                        chat_id=int(chat_id),
                        text=text,
                        format=Format.HTML,
                    )
                except Exception:  # noqa: BLE001
                    # Не смогли — строка останется в очереди на потом.
                    logger.exception(
                        "Не удалось отправить уведомление %s", item.get("id")
                    )
                    continue
                delivered.append(int(item["id"]))

            if not delivered:
                continue
            try:
                await self.backend.mark_delivered(token, delivered)
            except BackendError as exc:
                logger.warning("Квитанция %s: %s", max_user_id, exc.detail)
