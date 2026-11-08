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
from routers import common as common_router
from routers import homework as homework_router
from routers import menu as menu_router
from routers import tasks as tasks_router
from services import views
from texts import messages

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
    def __init__(self, role: str = "student") -> None:
        self._role = role

    async def token(self, user_id: int) -> str:
        return "jwt"

    def role(self, user_id: int) -> str | None:
        return self._role


class FakeStore:
    def upsert_user(self, *args, **kwargs) -> None:
        pass

    async def save(self) -> None:
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


def _ctx(role: str = "student") -> AppContext:
    return AppContext(
        bot=None,
        backend=FakeBackend(),
        store=FakeStore(),
        sessions=FakeSessions(role),
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


def test_help_button_shows_help_of_role(monkeypatch):
    monkeypatch.setattr(
        messages, "settings", SimpleNamespace(app_domain="example.org")
    )
    for role, expected in (
        ("student", messages.HELP_TEXT),
        ("teacher", messages.TEACHER_HELP_TEXT),
        ("admin", messages.ADMIN_HELP_TEXT),
    ):
        answers.clear()
        handler = menu_router.build_router(_ctx(role)).event_handlers[0]
        asyncio.run(handler.func_event(Callback("menu:help")))
        text = answers[-1][0]
        assert text.startswith(expected)
        assert text.endswith(
            "Больше функций доступно онлайн https://example.org"
        )


def test_start_greets_by_role():
    roles = ("student", "parent", "teacher", "admin")
    assert len({messages.greeting(role, "Иван") for role in roles}) == 4

    for role in roles:
        answers.clear()
        handler = common_router.build_router(_ctx(role)).event_handlers[4]
        event = SimpleNamespace(message=Message("/start", ChatType.DIALOG))
        asyncio.run(handler.func_event(event))
        assert answers[-1][0] == messages.greeting(role, USER.first_name)
