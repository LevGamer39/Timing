"""Обработчики событий MAX-бота.

Всё взаимодействие с платформой идёт через ``maxapi``: типизированные
события, фильтры ``Command``/``CommandStart`` и штатные клавиатуры.
Собственных HTTP-вызовов к MAX API здесь нет.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from maxapi import Bot, Dispatcher, F
from maxapi.enums.chat_type import ChatType
from maxapi.enums.parse_mode import Format
from maxapi.filters.command import Command, CommandStart
from maxapi.types.updates.bot_added import BotAdded
from maxapi.types.updates.bot_removed import BotRemoved
from maxapi.types.updates.bot_started import BotStarted
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated
from maxapi.types.updates.message_edited import MessageEdited
from maxapi.types.updates.user_added import UserAdded
from maxapi.types.updates.user_removed import UserRemoved

import ai
from api import Backend, BackendError
from keyboards import main_menu, task_actions, tasks_keyboard
from parser import HomeworkParser
from sessions import SessionManager
from state import StateStore
from texts import (
    averages_block,
    children_block,
    esc,
    fmt_deadline,
    grades_block,
    schedule_block,
    summary_block,
    task_line,
    tasks_block,
)

logger = logging.getLogger("vector_bot.handlers")

HELP_TEXT = (
    "<b>Что я умею</b>\n"
    "• «На сегодня» — задания на день и просрочки\n"
    "• «Добавить» — пришлите текст или голосовое, я разберу его\n"
    "• «Оценки» и «Сводка» — успеваемость\n"
    "• «Расписание» — уроки на сегодня\n"
    "• «ИИ-чат» — свободный вопрос помощнику\n\n"
    "<b>Команды</b>\n"
    "/today /add /grades /summary /schedule /ai /done\n"
    "/hw on — включить парсер ДЗ в чате класса\n"
    "/hw off — выключить парсер\n"
    "/hw — разобрать сообщение, на которое вы ответили\n"
    "/cancel — выйти из режима ИИ-чата"
)

UNLINKED_TEXT = (
    "Аккаунт MAX ещё не привязан к VectorVK.\n"
    "Войдите в личный кабинет и привяжите MAX в настройках профиля, "
    "после этого отправьте /start."
)


class BotHandlers:
    def __init__(
        self,
        bot: Bot,
        dp: Dispatcher,
        backend: Backend,
        store: StateStore,
        sessions: SessionManager,
        parser: HomeworkParser,
    ) -> None:
        self.bot = bot
        self.dp = dp
        self.backend = backend
        self.store = store
        self.sessions = sessions
        self.parser = parser
        # Режим свободного текста: для ИИ-чата, чтобы не путать с задачами.
        self._ai_mode: set[int] = set()
        self._notified_unlinked: set[int] = set()

    # ── Регистрация ───────────────────────────────────────────────────────

    def register(self) -> None:
        dp = self.dp

        dp.bot_started()(self.on_bot_started)
        dp.bot_added()(self.on_bot_added)
        dp.bot_removed()(self.on_bot_removed)
        dp.user_added()(self.on_user_added)
        dp.user_removed()(self.on_user_removed)
        dp.message_edited()(self.on_message_edited)
        dp.message_callback()(self.on_callback)

        dp.message_created(CommandStart())(self.on_start)
        dp.message_created(Command("help"))(self.on_help)
        dp.message_created(Command("today"))(self.on_today)
        dp.message_created(Command("add"))(self.on_add)
        dp.message_created(Command("grades"))(self.on_grades)
        dp.message_created(Command("summary"))(self.on_summary)
        dp.message_created(Command("schedule"))(self.on_schedule)
        dp.message_created(Command("ai"))(self.on_ai)
        dp.message_created(Command("done"))(self.on_done)
        dp.message_created(Command("hw"))(self.on_hw_command)
        dp.message_created(Command("cancel"))(self.on_cancel)

        # Свободный текст и голосовые — последними, после команд.
        dp.message_created(F.message.body.text)(self.on_text)
        dp.message_created()(self.on_voice)

    # ── Базовые события ───────────────────────────────────────────────────

    async def on_bot_started(self, event: BotStarted) -> None:
        user_id = event.user.user_id
        self._remember(user_id, event.chat_id, event.user)
        token = await self.sessions.token(user_id)
        if token:
            text = (
                f"Привет, {esc(event.user.first_name)}! 👋\n"
                "Я помогу не забыть про домашку."
            )
        else:
            text = f"Привет, {esc(event.user.first_name)}! 👋\n\n{UNLINKED_TEXT}"
        await self.bot.send_message(
            chat_id=event.chat_id,
            text=text,
            attachments=[main_menu(self.sessions.role(user_id))],
            format=Format.HTML,
        )
        await self.store.save()

    async def on_start(self, event: MessageCreated) -> None:
        user = event.message.sender
        if user is None:
            return
        user_id = user.user_id
        chat_id = event.message.recipient.chat_id
        self._remember(user_id, chat_id, user)
        token = await self.sessions.token(user_id)
        if token:
            text = (
                f"Привет, {esc(user.first_name)}! 👋\n"
                "Я помогу не забыть про домашку."
            )
        else:
            text = UNLINKED_TEXT
        await self._reply(
            event, text, keyboard=main_menu(self.sessions.role(user_id))
        )
        await self.store.save()

    async def on_help(self, event: MessageCreated) -> None:
        await self._reply(event, HELP_TEXT, keyboard=main_menu(self._role(event)))

    async def on_cancel(self, event: MessageCreated) -> None:
        user_id = self._user_id(event)
        if user_id:
            self._ai_mode.discard(user_id)
        await self._reply(event, "Вышли из ИИ-чата.", keyboard=main_menu(self._role(event)))

    async def on_bot_added(self, event: BotAdded) -> None:
        if event.is_channel:
            return
        title = None
        try:
            chat = await event.fetch_chat()
            title = chat.title if chat else None
        except Exception:  # noqa: BLE001
            pass
        await self.parser.enable(event.chat_id, title)
        await self.bot.send_message(
            chat_id=event.chat_id,
            text=(
                "Я подключён к чату класса и буду искать домашние задания.\n"
                "Автопоиск уже включён. /hw off — выключить, "
                "/hw — разобрать сообщение, на которое вы ответили."
            ),
            format=Format.HTML,
        )

    async def on_bot_removed(self, event: BotRemoved) -> None:
        await self.parser.disable(event.chat_id)

    async def on_user_added(self, event: UserAdded) -> None:
        logger.info("В чат %s добавлен пользователь %s", event.chat_id, event.user.user_id)

    async def on_user_removed(self, event: UserRemoved) -> None:
        logger.info("Из чата %s удалён пользователь %s", event.chat_id, event.user.user_id)

    async def on_message_edited(self, event: MessageEdited) -> None:
        await self.parser.feed_edited(event)

    # ── Команды ───────────────────────────────────────────────────────────

    async def on_today(self, event: MessageCreated) -> None:
        token, error = await self._token(event)
        if token is None:
            await self._unlinked(event, error)
            return

        tasks = await self._safe(event, self.backend.list_tasks(token))
        if tasks is None:
            return
        active = [task for task in tasks if task.get("status") != "done"]
        due = [task for task in active if self._is_due(task)]
        shown = due or active

        if not shown:
            await self._reply(event, "На сегодня заданий нет 🎉")
            return
        title = "На сегодня и просрочено" if due else "Активные задания"
        await self._reply(
            event,
            f"<b>{title}</b>\n{tasks_block(shown)}",
            keyboard=tasks_keyboard(shown),
        )

    async def on_add(self, event: MessageCreated, args: list[str] | None = None) -> None:
        text = " ".join(args or []).strip()
        if not text:
            await self._reply(
                event,
                "Пришлите текст задания: «Математика, №42, к пятнице» "
                "или отправьте голосовое.",
            )
            return
        await self._create_from_text(event, text)

    async def on_grades(self, event: MessageCreated) -> None:
        token, error = await self._token(event)
        if token is None:
            await self._unlinked(event, error)
            return
        averages = await self._safe(event, self.backend.my_averages(token))
        if averages is None:
            return
        grades = await self._safe(event, self.backend.my_grades(token))
        if grades is None:
            return
        text = (
            "<b>Средние баллы</b>\n"
            f"{averages_block(averages)}\n\n"
            "<b>Последние оценки</b>\n"
            f"{grades_block(grades)}"
        )
        await self._reply(event, text)

    async def on_summary(self, event: MessageCreated) -> None:
        token, error = await self._token(event)
        if token is None:
            await self._unlinked(event, error)
            return
        role = self.sessions.role(self._user_id(event) or 0)

        if role == "parent":
            children = await self._safe(event, self.backend.children(token))
            if children is None:
                return
            lines = ["<b>Сводка по детям</b>", children_block(children)]
            for child in children[:3]:
                grades = await self._safe(
                    event, self.backend.child_grades(token, int(child["id"]))
                )
                if grades is None:
                    continue
                if grades:
                    average = sum(float(g["value"]) for g in grades) / len(grades)
                    lines.append(
                        f"\n<b>{esc(child['first_name'])}</b>: "
                        f"средний балл {average:.2f}, оценок {len(grades)}"
                    )
                else:
                    lines.append(f"\n<b>{esc(child['first_name'])}</b>: оценок нет")
            await self._reply(event, "\n".join(lines))
            return

        summary = await self._safe(event, self.backend.my_grade_summary(token))
        if summary is None:
            return
        await self._reply(event, f"<b>Ваша сводка</b>\n{summary_block(summary)}")

    async def on_schedule(self, event: MessageCreated) -> None:
        token, error = await self._token(event)
        if token is None:
            await self._unlinked(event, error)
            return
        entries = await self._safe(event, self.backend.my_schedule(token))
        if entries is None:
            return
        await self._reply(event, f"<b>Расписание</b>\n{schedule_block(entries)}")

    async def on_ai(self, event: MessageCreated, args: list[str] | None = None) -> None:
        user_id = self._user_id(event)
        text = " ".join(args or []).strip()
        if not text:
            if user_id:
                self._ai_mode.add(user_id)
            await self._reply(event, "Напишите вопрос — отвечу как ИИ-помощник.")
            return
        await self._ai_reply(event, text)

    async def on_done(self, event: MessageCreated) -> None:
        token, error = await self._token(event)
        if token is None:
            await self._unlinked(event, error)
            return
        tasks = await self._safe(event, self.backend.list_tasks(token))
        if tasks is None:
            return
        active = [task for task in tasks if task.get("status") != "done"]
        if not active:
            await self._reply(event, "Активных заданий нет 🎉")
            return
        await self._reply(
            event,
            f"<b>Отметьте выполненное</b>\n{tasks_block(active, limit=6)}",
            keyboard=tasks_keyboard(active),
        )

    async def on_hw_command(
        self, event: MessageCreated, args: list[str] | None = None
    ) -> None:
        mode = (args[0] if args else "").lower()
        chat_id = event.message.recipient.chat_id
        if chat_id is None:
            return

        if mode == "on":
            if not await self._is_chat_admin(event, chat_id):
                await self._reply(event, "Включать парсер может только админ чата.")
                return
            await self.parser.enable(chat_id)
            await self._reply(
                event,
                "Парсер ДЗ включён. Буду присылать найденные задания "
                f"раз в {self.parser_interval()} мин.",
            )
        elif mode == "off":
            if not await self._is_chat_admin(event, chat_id):
                await self._reply(event, "Выключать парсер может только админ чата.")
                return
            await self.parser.disable(chat_id)
            await self._reply(event, "Парсер ДЗ выключен.")
        else:
            await self.parser.manual(event)

    # ── Свободный текст и голос ───────────────────────────────────────────

    async def on_text(self, event: MessageCreated) -> None:
        body = event.message.body
        if body is None:
            return
        text = self._text(event)
        if not text:
            return
        if text.lstrip().startswith("/"):
            await self._reply(
                event,
                "Неизвестная команда. " + HELP_TEXT,
                keyboard=main_menu(self._role(event)),
            )
            return

        chat_type = event.message.recipient.chat_type
        if chat_type != ChatType.DIALOG:
            # В групповом чате свободный текст — это сообщение для парсера.
            await self.parser.feed(event)
            return

        user_id = self._user_id(event)
        if user_id and user_id in self._ai_mode:
            await self._ai_reply(event, text)
            return

        await self._create_from_text(event, text)

    # ── Callback-кнопки ───────────────────────────────────────────────────

    async def on_voice(self, event: MessageCreated) -> None:
        """Голосовые и прочие сообщения без текста.

        MAX присылает распознанный текст в поле ``transcription``. Если его
        нет, честно просим написать текстом, а не делаем вид, что поняли.
        """

        text = self._text(event)
        chat_type = event.message.recipient.chat_type

        if chat_type != ChatType.DIALOG:
            await self.parser.feed(event)
            return

        if not text:
            await self._reply(
                event,
                "Не удалось распознать голосовое. Напишите задание текстом.",
            )
            return

        user_id = self._user_id(event)
        if user_id and user_id in self._ai_mode:
            await self._ai_reply(event, text)
            return
        await self._create_from_text(event, text)

    async def on_callback(self, event: MessageCallback) -> None:
        payload = event.callback.payload or ""
        user_id = event.callback.user.user_id
        chat_id = event.message.recipient.chat_id if event.message else None
        if chat_id is None:
            await event.ack("Нет чата для ответа")
            return

        if payload.startswith("menu:"):
            await self._handle_menu(event, payload[5:], user_id, chat_id)
        elif payload.startswith("task:done:"):
            await self._complete_task(event, int(payload.rsplit(":", 1)[1]), user_id)
        elif payload.startswith("task:del:"):
            await self._delete_task(event, int(payload.rsplit(":", 1)[1]), user_id)
        elif payload.startswith("hw:add:"):
            await self._add_homework(event, int(payload.rsplit(":", 1)[1]), user_id)
        elif payload.startswith("hw:skip:"):
            self.parser.pop_pending(int(payload.rsplit(":", 1)[1]))
            await event.answer(new_text="Ок, пропустил.")
        else:
            await event.ack()

    async def _handle_menu(
        self, event: MessageCallback, action: str, user_id: int, chat_id: int
    ) -> None:
        self._remember(user_id, chat_id, event.callback.user)
        if action == "today":
            await self.on_menu_today(event, user_id)
        elif action == "add":
            await event.answer(
                new_text="Пришлите текст задания или голосовое сообщение."
            )
        elif action == "ai":
            self._ai_mode.add(user_id)
            await event.answer(new_text="ИИ-чат включён. Напишите вопрос. /cancel — выход.")
        elif action == "grades":
            await self.on_menu_grades(event, user_id)
        elif action == "summary":
            await self.on_menu_summary(event, user_id)
        elif action == "schedule":
            await self.on_menu_schedule(event, user_id)
        elif action == "children":
            await self.on_menu_children(event, user_id)
        elif action == "classes":
            await self.on_menu_classes(event, user_id)
        elif action == "help":
            await event.answer(new_text=HELP_TEXT, attachments=[main_menu(self.sessions.role(user_id))])
        else:
            await event.ack()

    # Меню повторяет команды, но отвечает редактированием сообщения.
    async def on_menu_today(self, event: MessageCallback, user_id: int) -> None:
        token = await self.sessions.token(user_id)
        if token is None:
            await event.answer(new_text=UNLINKED_TEXT)
            return
        tasks = await self.backend.list_tasks(token)
        active = [task for task in tasks if task.get("status") != "done"]
        due = [task for task in active if self._is_due(task)]
        shown = due or active
        if not shown:
            await event.answer(new_text="На сегодня заданий нет 🎉")
            return
        await event.answer(
            new_text=f"<b>Задания</b>\n{tasks_block(shown)}",
            attachments=[tasks_keyboard(shown)],
        )

    async def on_menu_grades(self, event: MessageCallback, user_id: int) -> None:
        token = await self.sessions.token(user_id)
        if token is None:
            await event.answer(new_text=UNLINKED_TEXT)
            return
        averages = await self.backend.my_averages(token)
        grades = await self.backend.my_grades(token)
        await event.answer(
            new_text=(
                f"<b>Средние баллы</b>\n{averages_block(averages)}\n\n"
                f"<b>Последние оценки</b>\n{grades_block(grades)}"
            )
        )

    async def on_menu_summary(self, event: MessageCallback, user_id: int) -> None:
        token = await self.sessions.token(user_id)
        if token is None:
            await event.answer(new_text=UNLINKED_TEXT)
            return
        if self.sessions.role(user_id) == "parent":
            children = await self.backend.children(token)
            await event.answer(new_text=children_block(children))
            return
        summary = await self.backend.my_grade_summary(token)
        await event.answer(new_text=summary_block(summary))

    async def on_menu_schedule(self, event: MessageCallback, user_id: int) -> None:
        token = await self.sessions.token(user_id)
        if token is None:
            await event.answer(new_text=UNLINKED_TEXT)
            return
        entries = await self.backend.my_schedule(token)
        await event.answer(new_text=schedule_block(entries))

    async def on_menu_children(self, event: MessageCallback, user_id: int) -> None:
        token = await self.sessions.token(user_id)
        if token is None:
            await event.answer(new_text=UNLINKED_TEXT)
            return
        children = await self.backend.children(token)
        await event.answer(new_text=children_block(children))

    async def on_menu_classes(self, event: MessageCallback, user_id: int) -> None:
        token = await self.sessions.token(user_id)
        if token is None:
            await event.answer(new_text=UNLINKED_TEXT)
            return
        classes = await self.backend.my_classes(token)
        if not classes:
            await event.answer(new_text="Классов пока нет.")
            return
        text = "\n".join(
            f"• {esc(item.get('name', 'Класс'))} — "
            f"{item.get('students_count', 0)} учеников"
            for item in classes
        )
        await event.answer(new_text=f"<b>Мои классы</b>\n{text}")

    async def _complete_task(
        self, event: MessageCallback, task_id: int, user_id: int
    ) -> None:
        token = await self.sessions.token(user_id)
        if token is None:
            await event.answer(new_text=UNLINKED_TEXT)
            return
        try:
            task = await self.backend.complete_task(token, task_id)
        except BackendError as exc:
            await event.answer(new_text=f"Не получилось: {esc(exc.detail)}")
            return
        await event.answer(new_text=f"✅ Выполнено: {task_line(task)}")

    async def _delete_task(
        self, event: MessageCallback, task_id: int, user_id: int
    ) -> None:
        token = await self.sessions.token(user_id)
        if token is None:
            await event.answer(new_text=UNLINKED_TEXT)
            return
        try:
            await self.backend.delete_task(token, task_id)
        except BackendError as exc:
            await event.answer(new_text=f"Не получилось: {esc(exc.detail)}")
            return
        await event.answer(new_text="🗑 Задание удалено.")

    async def _add_homework(
        self, event: MessageCallback, uid: int, user_id: int
    ) -> None:
        item = self.parser.pop_pending(uid)
        if item is None:
            await event.answer(new_text="Карточка устарела. Отметьте задание заново.")
            return
        token = await self.sessions.token(user_id)
        if token is None:
            await event.answer(new_text=UNLINKED_TEXT)
            return
        payload = self._task_payload(
            {
                "title": item.get("title", "Домашнее задание"),
                "subject": item.get("subject"),
                "deadline": item.get("deadline"),
                "priority": item.get("priority", "medium"),
            },
            source=item.get("source", ""),
        )
        try:
            task = await self.backend.create_task(token, payload)
        except BackendError as exc:
            await event.answer(new_text=f"Не получилось: {esc(exc.detail)}")
            return
        await event.answer(new_text=f"✅ Добавлено: {task_line(task)}")

    # ── Создание задачи из текста ─────────────────────────────────────────

    async def _create_from_text(self, event: MessageCreated, text: str) -> None:
        token, error = await self._token(event)
        if token is None:
            await self._unlinked(event, error)
            return

        await self._reply(event, "Разбираю задание…")
        parsed = await ai.parse_task(text)
        if not parsed.get("is_homework"):
            await self._reply(
                event,
                "Не похоже на задание. Если это всё-таки ДЗ — начните текст "
                "со слова «задание».",
            )
            return

        payload = self._task_payload(parsed, source=text)
        try:
            task = await self.backend.create_task(token, payload)
        except BackendError as exc:
            await self._reply(event, f"Не получилось создать задание: {esc(exc.detail)}")
            return

        details = [
            "✅ <b>Задание добавлено</b>",
            task_line(task),
            f"Срок: {esc(fmt_deadline(task.get('deadline')))}",
        ]
        if task.get("subject"):
            details.append(f"Предмет: {esc(task['subject'])}")
        await self._reply(
            event,
            "\n".join(details),
            keyboard=task_actions(int(task["id"]), done=False),
        )

    # ── ИИ-чат ────────────────────────────────────────────────────────────

    async def _ai_reply(self, event: MessageCreated, text: str) -> None:
        user_id = self._user_id(event)
        token, _ = await self._token(event)
        if token and user_id:
            try:
                response = await self.backend.ai_chat(token, text)
                answer = response.get("reply") or "Не смог ответить."
                await self._reply(event, esc(answer))
                return
            except BackendError as exc:
                logger.warning("ИИ бэкенда недоступен: %s", exc.detail)

        context = ""
        if token:
            try:
                tasks = await self.backend.list_tasks(token)
                active = [t for t in tasks if t.get("status") != "done"][:5]
                context = "Активные задания:\n" + tasks_block(active)
            except BackendError:
                context = ""
        answer = await ai.chat_reply(text, context)
        await self._reply(event, esc(answer))

    # ── Вспомогательное ───────────────────────────────────────────────────

    def _remember(self, user_id: int, chat_id: int, user: Any) -> None:
        known = self.store.user(user_id) is not None
        self.store.upsert_user(
            user_id,
            chat_id=chat_id,
            first_name=getattr(user, "first_name", None),
            username=getattr(user, "username", None),
        )
        if not known:
            logger.info("Новый MAX-пользователь %s (chat %s)", user_id, chat_id)

    def _user_id(self, event: MessageCreated) -> int | None:
        user = event.message.sender
        return user.user_id if user else None

    def _role(self, event: MessageCreated) -> str | None:
        user_id = self._user_id(event)
        return self.sessions.role(user_id) if user_id else None

    async def _token(
        self, event: MessageCreated
    ) -> tuple[str | None, str | None]:
        user_id = self._user_id(event)
        if user_id is None:
            return None, None
        chat_id = event.message.recipient.chat_id or 0
        self._remember(user_id, chat_id, event.message.sender)
        token = await self.sessions.token(user_id)
        return token, None

    async def _unlinked(self, event: MessageCreated, error: str | None) -> None:
        user_id = self._user_id(event)
        if user_id is not None:
            if user_id in self._notified_unlinked:
                return
            self._notified_unlinked.add(user_id)
        await self._reply(event, UNLINKED_TEXT)

    async def _reply(
        self,
        event: MessageCreated,
        text: str,
        keyboard: Any | None = None,
    ) -> None:
        await event.message.answer(
            text=text,
            attachments=[keyboard] if keyboard is not None else None,
            format=Format.HTML,
        )

    async def _safe(self, event: MessageCreated, coro: Any) -> Any:
        try:
            return await coro
        except BackendError as exc:
            await self._reply(event, f"Бэкенд недоступен: {esc(exc.detail)}")
            return None

    @staticmethod
    def _is_due(task: dict[str, Any]) -> bool:
        deadline = task.get("deadline")
        if not deadline:
            return False
        try:
            parsed = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed.date() <= date.today()

    @staticmethod
    def _task_payload(parsed: dict[str, Any], source: str) -> dict[str, Any]:
        deadline = parsed.get("deadline")
        if deadline:
            deadline = f"{deadline}T23:59:00+00:00"
        return {
            "title": (parsed.get("title") or "Домашнее задание")[:120],
            "subject": parsed.get("subject") or None,
            "description": (source or "")[:1000] or None,
            "deadline": deadline,
            "priority": parsed.get("priority") or "medium",
        }

    async def _is_chat_admin(self, event: MessageCreated, chat_id: int) -> bool:
        user_id = self._user_id(event)
        if user_id is None:
            return False
        try:
            admins = await self.bot.get_list_admin_chat(chat_id=chat_id)
        except Exception:  # noqa: BLE001
            return False
        return any(
            member.user_id == user_id and (member.is_admin or member.is_owner)
            for member in admins.members
        )

    @staticmethod
    def parser_interval() -> int:
        from config import settings

        return settings.parser_batch_interval_minutes

    @staticmethod
    def _text(event: MessageCreated) -> str | None:
        body = event.message.body
        if body is None:
            return None
        if body.text:
            return body.text
        for attachment in body.attachments or []:
            transcription = getattr(attachment, "transcription", None)
            if transcription:
                return transcription
        return None


def register_handlers(
    dp: Dispatcher,
    bot: Bot,
    backend: Backend,
    store: StateStore,
    sessions: SessionManager,
    parser: HomeworkParser,
) -> None:
    """Точка регистрации всех обработчиков."""

    BotHandlers(bot, dp, backend, store, sessions, parser).register()
