"""Диалог регистрации: логин -> проверка -> код приглашения.

Запуск из каталога ``bot`` (импорты в боте абсолютные от его корня)::

    cd bot && python -m pytest tests

Тест без сети: бэкенд подменён заглушкой, события MAX — простыми объектами.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from maxapi.enums.chat_type import ChatType

from core.context import AppContext
from routers import menu as menu_router
from routers import messages as messages_router

MAX_USER_ID = 7
TAKEN = {"taken"}
USER = SimpleNamespace(user_id=MAX_USER_ID, first_name="U", username="u")
answers: list[str] = []
# Что ушло в edit исходного сообщения с кнопкой: [] очищает клавиатуру.
keyboard_edits: list = []


class FakeBackend:
    async def login_available(self, login: str) -> bool:
        return login.strip().lower() not in TAKEN


class Message:
    def __init__(self, text: str) -> None:
        self.body = SimpleNamespace(text=text, attachments=None)
        self.sender = USER
        self.recipient = SimpleNamespace(chat_type=ChatType.DIALOG, chat_id=1)

    async def answer(self, text, attachments=None, format=None) -> None:
        answers.append(text)


class RegisterClick:
    """Нажатие «Зарегистрироваться»: respond идёт через message.answer."""

    class callback:
        payload = "menu:register"
        user = USER

    message = Message("")

    async def answer(self, new_text=None, attachments=None) -> None:
        answers.append(new_text)
        keyboard_edits.append(attachments)


def _ctx() -> AppContext:
    return AppContext(
        bot=None,
        backend=FakeBackend(),
        store=SimpleNamespace(upsert_user=lambda *a, **k: None),
        sessions=None,
        parser=SimpleNamespace(feed=None),
    )


def test_register_asks_login_then_code():
    answers.clear()
    keyboard_edits.clear()
    ctx = _ctx()
    menu = menu_router.build_router(ctx).event_handlers[0].func_event
    message = messages_router.build_router(ctx).event_handlers[0].func_event

    async def flow() -> None:
        await menu(RegisterClick())
        await message(SimpleNamespace(message=Message("Taken")))
        await message(SimpleNamespace(message=Message("MyLogin")))
        await message(SimpleNamespace(message=Message("INV-1")))

    asyncio.run(flow())

    assert answers[0] == "Придумайте логин"
    # Занятый логин не сохраняется, диалог просит другой.
    assert answers[1].startswith("Такой логин уже существует")
    assert answers[2] == "Введите код приглашения"
    assert answers[3] == "Спасибо! Данные приняты"
    assert MAX_USER_ID not in ctx.registration
    # Кнопка «Зарегистрироваться» не остаётся под вопросом о логине.
    assert keyboard_edits == [[]]
