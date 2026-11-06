"""Кнопка «На главную»: списки заданий, карточки ДЗ и ответы команд.

Запуск из каталога ``bot``::

    cd bot && python -m pytest tests
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from maxapi.enums.chat_type import ChatType

from core.context import AppContext
from keyboards import (
    home_keyboard,
    homework_keyboard,
    task_actions,
    tasks_keyboard,
)
from routers import homework as homework_router
from routers import menu as menu_router
from routers import tasks as tasks_router
from services import views

USER = SimpleNamespace(user_id=7, first_name="Иван", username="u")


def payloads(keyboard) -> list[str | None]:
    return [
        button.payload
        for row in keyboard.payload.buttons
        for button in row
    ]


class FakeBackend:
    async def list_tasks(self, token: str) -> list[dict]:
        return []


class FakeSessions:
    async def token(self, user_id: int) -> str:
        return "jwt"

    def role(self, user_id: int) -> str | None:
        return "student"


class FakeStore:
    def upsert_user(self, *args, **kwargs) -> None:
        pass


class Message:
    def __init__(self, text: str, chat_type: ChatType) -> None:
        self.body = SimpleNamespace(text=text, attachments=None)
        self.sender = USER
        self.recipient = SimpleNamespace(chat_type=chat_type, chat_id=1)

    async def answer(self, text, attachments=None, format=None) -> None:
        answers.append((text, attachments))


class Callback:
    """Нажатие кнопки: message здесь — исходное сообщение с клавиатурой."""

    def __init__(
        self, payload: str, chat_type: ChatType = ChatType.DIALOG
    ) -> None:
        self.callback = SimpleNamespace(payload=payload, user=USER)
        self.message = Message("", chat_type)

    async def answer(self, new_text=None, attachments=None) -> None:
        answers.append((new_text, attachments))


answers: list[tuple] = []


def _ctx() -> AppContext:
    return AppContext(
        bot=None,
        backend=FakeBackend(),
        store=FakeStore(),
        sessions=FakeSessions(),
        parser=SimpleNamespace(
            feed=None,
            remember=lambda *a: None,
            pop_pending=lambda uid: None,
        ),
    )


def _keyboard(attachments) -> object:
    assert attachments, "ожидалась клавиатура"
    return attachments[0]


def test_task_keyboards_have_home_button():
    active = [{"id": 1, "title": "Математика"}]
    for keyboard in (
        tasks_keyboard([]),
        tasks_keyboard(active),
        task_actions(1, done=False),
        home_keyboard(),
    ):
        assert "menu:home" in payloads(keyboard)


def test_homework_card_has_home_only_in_dialog():
    assert "menu:home" not in payloads(homework_keyboard(1))
    assert "menu:home" in payloads(homework_keyboard(1, home=True))


def test_today_without_tasks_keeps_home_button():
    text, keyboard = asyncio.run(views.today(FakeBackend(), "jwt"))
    assert text.startswith("На сегодня заданий нет")
    assert "menu:home" in payloads(keyboard)


def test_home_button_returns_main_menu():
    answers.clear()
    ctx = _ctx()
    handler = menu_router.build_router(ctx).event_handlers[0].func_event

    asyncio.run(handler(Callback("menu:home")))

    text, attachments = answers[-1]
    assert text == "🏠 Главное меню"
    assert "menu:today" in payloads(_keyboard(attachments))


def test_add_without_text_has_cancel():
    answers.clear()
    ctx = _ctx()
    handler = tasks_router.build_router(ctx).event_handlers[1].func_event
    event = SimpleNamespace(message=Message("/add", ChatType.DIALOG))

    asyncio.run(handler(event, None))

    text, attachments = answers[-1]
    assert text.startswith("Пришлите текст задания")
    assert "menu:home" in payloads(_keyboard(attachments))


def test_skip_homework_home_button_only_in_dialog():
    answers.clear()
    ctx = _ctx()
    handler = homework_router.build_router(ctx).event_handlers[1].func_event

    asyncio.run(handler(Callback("hw:skip:1", ChatType.DIALOG)))
    asyncio.run(handler(Callback("hw:skip:2", ChatType.CHAT)))

    dialog_text, dialog_attachments = answers[0]
    chat_text, chat_attachments = answers[1]
    assert dialog_text == chat_text == "Ок, пропустил."
    assert "menu:home" in payloads(_keyboard(dialog_attachments))
    assert chat_attachments is None
