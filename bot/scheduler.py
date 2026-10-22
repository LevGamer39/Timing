"""Фоновые напоминания и недельная сводка.

Планировщик живёт в процессе бота и работает по списку пользователей,
которые хотя бы раз писали боту. JWT не кэшируется на диск — перед отправкой
он заново выпускается бэкендом по хешу MAX user_id.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

from maxapi import Bot
from maxapi.enums.parse_mode import Format

from api import Backend, BackendError
from config import settings
from keyboards import tasks_keyboard
from sessions import SessionManager
from state import StateStore
from texts import esc, fmt_deadline, summary_block, tasks_block

logger = logging.getLogger("vector_bot.scheduler")


class Scheduler:
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
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        if settings.reminders_enabled:
            self._tasks.append(asyncio.create_task(self._loop_reminders()))
        if settings.weekly_summary_enabled:
            self._tasks.append(asyncio.create_task(self._loop_weekly()))
        if self._tasks:
            logger.info("Планировщик напоминаний запущен")

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    async def _loop_reminders(self) -> None:
        interval = max(5, settings.reminder_interval_minutes) * 60
        while True:
            try:
                await self._send_reminders()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Ошибка в цикле напоминаний")
            await asyncio.sleep(interval)

    async def _loop_weekly(self) -> None:
        while True:
            try:
                if self._weekly_time():
                    await self._send_weekly()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Ошибка в недельной сводке")
            await asyncio.sleep(600)

    # ── Напоминания ───────────────────────────────────────────────────────

    async def _send_reminders(self) -> None:
        today = date.today()
        for user in self.store.users():
            user_id = int(user["max_user_id"])
            chat_id = user.get("chat_id")
            if not chat_id or user.get("role") != "student":
                continue

            token = await self.sessions.token(user_id)
            if token is None:
                continue

            try:
                tasks = await self.backend.list_tasks(token)
            except BackendError as exc:
                logger.warning("Напоминания %s: %s", user_id, exc.detail)
                continue

            sent = 0
            for task in tasks:
                if task.get("status") == "done":
                    continue
                key = self._reminder_key(task, today)
                if key is None or self.store.was_sent(user_id, key):
                    continue

                await self._send_task_reminder(int(chat_id), task)
                self.store.mark_sent(user_id, key, today.isoformat())
                sent += 1
                if sent >= 5:
                    break

            if sent:
                await self.store.save()

    def _reminder_key(self, task: dict[str, Any], today: date) -> str | None:
        deadline = task.get("deadline")
        if not deadline:
            return None
        try:
            due = datetime.fromisoformat(
                str(deadline).replace("Z", "+00:00")
            ).date()
        except ValueError:
            return None

        task_id = task.get("id")
        if due < today:
            return f"{task_id}:overdue:{today}"
        if due == today:
            return f"{task_id}:today:{today}"
        if due == today + timedelta(days=1):
            return f"{task_id}:tomorrow:{today}"
        return None

    async def _send_task_reminder(self, chat_id: int, task: dict[str, Any]) -> None:
        due = fmt_deadline(task.get("deadline"))
        text = (
            "⏰ <b>Напоминание о задании</b>\n"
            f"{tasks_block([task])}\n\n"
            f"Срок: <b>{esc(due)}</b>"
        )
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                attachments=[tasks_keyboard([task])],
                format=Format.HTML,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось отправить напоминание в чат %s", chat_id)

    # ── Недельная сводка ──────────────────────────────────────────────────

    def _weekly_time(self) -> bool:
        now = datetime.now()
        return (
            now.weekday() == settings.weekly_summary_weekday
            and now.hour >= settings.weekly_summary_hour
        )

    async def _send_weekly(self) -> None:
        day = date.today().isoformat()
        for user in self.store.users():
            user_id = int(user["max_user_id"])
            chat_id = user.get("chat_id")
            role = user.get("role")
            if not chat_id or role not in {"student", "parent"}:
                continue
            key = f"weekly:{day}"
            if self.store.was_sent(user_id, key):
                continue

            token = await self.sessions.token(user_id)
            if token is None:
                continue

            text = await self._weekly_text(token, role)
            if not text:
                continue

            try:
                await self.bot.send_message(
                    chat_id=int(chat_id), text=text, format=Format.HTML
                )
            except Exception:  # noqa: BLE001
                logger.exception("Не удалось отправить сводку %s", user_id)
                continue

            self.store.mark_sent(user_id, key, day)
            logger.info("Недельная сводка отправлена %s", user_id)

        await self.store.save()

    async def _weekly_text(self, token: str, role: str) -> str | None:
        if role == "student":
            try:
                summary = await self.backend.my_grade_summary(token)
                tasks = await self.backend.list_tasks(token)
            except BackendError:
                return None
            active = [task for task in tasks if task.get("status") != "done"]
            return (
                "<b>Недельная сводка</b>\n"
                f"{summary_block(summary)}\n\n"
                f"<b>Активные задания</b>\n{tasks_block(active, limit=5)}"
            )

        try:
            children = await self.backend.children(token)
        except BackendError:
            return None
        if not children:
            return None

        lines = ["<b>Недельная сводка по детям</b>"]
        for child in children[:3]:
            try:
                grades = await self.backend.child_grades(token, int(child["id"]))
                tasks = await self.backend.child_tasks(token, int(child["id"]))
            except BackendError:
                continue
            average = (
                sum(float(grade["value"]) for grade in grades) / len(grades)
                if grades
                else None
            )
            active = [task for task in tasks if task.get("status") != "done"]
            lines.append(f"\n<b>{esc(child.get('first_name', ''))}</b>")
            lines.append(
                "Средний балл: "
                + (f"{average:.2f}" if average is not None else "нет оценок")
            )
            lines.append(f"Активных заданий: {len(active)}")
        return "\n".join(lines)
