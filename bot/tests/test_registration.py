"""Диалог регистрации: логин -> инвайт-код -> подтверждение имени -> бэкенд.

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
from routers import registration as registration_router

MAX_USER_ID = 7
TAKEN = {"taken"}
CLASS_CODE = "CLASS-CODE"
TEACHER_CODE = "TEACHER-CODE"
USER = SimpleNamespace(
    user_id=MAX_USER_ID,
    first_name="Иван",
    last_name="Иванов",
    username="u",
)
answers: list[str] = []
# Что ушло в edit исходного сообщения с кнопкой: [] очищает клавиатуру.
keyboard_edits: list = []
registered: list[dict] = []


class FakeBackend:
    async def login_available(self, login: str) -> bool:
        return login.strip().lower() not in TAKEN

    async def invite_code_info(self, code: str) -> dict:
        if code == CLASS_CODE:
            return {"valid": True, "role": "student", "class_name": "10А"}
        if code == TEACHER_CODE:
            return {"valid": True, "role": "teacher", "class_name": None}
        return {"valid": False, "role": None, "class_name": None}

    async def max_register(
        self,
        hashed: str,
        *,
        login: str,
        invite_code: str,
        first_name: str,
        last_name: str,
    ) -> dict:
        registered.append(
            {
                "hashed": hashed,
                "login": login,
                "invite_code": invite_code,
                "first_name": first_name,
                "last_name": last_name,
            }
        )
        return {
            "access_token": "jwt",
            "role": "student",
            "first_name": first_name,
            "last_name": last_name,
        }


class FakeSessions:
    def __init__(self) -> None:
        self.remembered: list[tuple] = []

    def remember(self, user_id: int, token: str, payload: dict) -> None:
        self.remembered.append((user_id, token, payload))

    def role(self, user_id: int) -> str | None:
        return None


class FakeStore:
    def upsert_user(self, *args, **kwargs) -> None:
        pass

    async def save(self) -> None:
        pass


class Message:
    def __init__(self, text: str) -> None:
        self.body = SimpleNamespace(text=text, attachments=None)
        self.sender = USER
        self.recipient = SimpleNamespace(chat_type=ChatType.DIALOG, chat_id=1)

    async def answer(self, text, attachments=None, format=None) -> None:
        answers.append(text)


class Callback:
    """Нажатие кнопки: event_user берёт автора из message.sender."""

    def __init__(self, payload: str) -> None:
        self.callback = SimpleNamespace(payload=payload, user=USER)
        self.message = Message("")

    async def answer(self, new_text=None, attachments=None) -> None:
        answers.append(new_text)
        keyboard_edits.append(attachments)


def _ctx() -> AppContext:
    return AppContext(
        bot=None,
        backend=FakeBackend(),
        store=FakeStore(),
        sessions=FakeSessions(),
        parser=SimpleNamespace(feed=None),
    )


def test_register_full_dialog():
    answers.clear()
    keyboard_edits.clear()
    registered.clear()
    ctx = _ctx()
    menu = menu_router.build_router(ctx).event_handlers[0].func_event
    message = messages_router.build_router(ctx).event_handlers[0].func_event
    button = registration_router.build_router(ctx).event_handlers[0].func_event

    async def flow() -> None:
        await menu(Callback("menu:register"))
        # Занятый логин не сохраняется, диалог просит другой.
        await message(SimpleNamespace(message=Message("Taken")))
        await message(SimpleNamespace(message=Message("MyLogin")))
        # Несуществующий код приглашения диалог не принимает.
        await message(SimpleNamespace(message=Message("NOPE")))
        await message(SimpleNamespace(message=Message(CLASS_CODE)))
        await button(Callback("reg:name:edit"))
        await message(SimpleNamespace(message=Message("Анна Мария\nПетрова")))
        await button(Callback("reg:name:ok"))

    asyncio.run(flow())

    assert answers[0] == "Придумайте логин"
    assert answers[1].startswith("Такой логин уже существует")
    assert answers[2] == "Введите код приглашения"
    assert answers[3].startswith("Такого кода приглашения нет")
    assert "Имя: Иван" in answers[4]
    assert answers[5].startswith("Отправьте имя и фамилию двумя строками")
    # После ручной правки бот переспрашивает и показывает разобранные поля.
    assert "Имя: Анна Мария" in answers[6]
    assert "Фамилия: Петрова" in answers[6]
    assert answers[7].startswith("Готово, Анна Мария!")

    assert len(registered) == 1
    assert registered[0]["login"] == "MyLogin"
    assert registered[0]["invite_code"] == CLASS_CODE
    assert registered[0]["first_name"] == "Анна Мария"
    assert registered[0]["last_name"] == "Петрова"
    assert len(registered[0]["hashed"]) == 64
    assert ctx.sessions.remembered[0][0] == MAX_USER_ID
    assert ctx.sessions.remembered[0][1] == "jwt"
    assert MAX_USER_ID not in ctx.registration
    # Кнопка «Зарегистрироваться» не остаётся под вопросом о логине.
    assert keyboard_edits == [[]]


def test_register_accepts_teacher_code_and_keeps_name_from_max():
    answers.clear()
    keyboard_edits.clear()
    registered.clear()
    ctx = _ctx()
    menu = menu_router.build_router(ctx).event_handlers[0].func_event
    message = messages_router.build_router(ctx).event_handlers[0].func_event
    button = registration_router.build_router(ctx).event_handlers[0].func_event

    async def flow() -> None:
        await menu(Callback("menu:register"))
        await message(SimpleNamespace(message=Message("new-teacher")))
        await message(SimpleNamespace(message=Message(TEACHER_CODE)))
        await button(Callback("reg:name:ok"))

    asyncio.run(flow())

    assert "Имя: Иван" in answers[2]
    assert answers[3].startswith("Готово, Иван!")
    assert registered[0]["invite_code"] == TEACHER_CODE
    assert registered[0]["first_name"] == "Иван"
    assert registered[0]["last_name"] == "Иванов"


def test_register_rejects_single_line_name():
    answers.clear()
    ctx = _ctx()
    message = messages_router.build_router(ctx).event_handlers[0].func_event

    async def flow() -> None:
        ctx.registration[MAX_USER_ID] = {
            "step": "name_edit",
            "login": "login",
            "invite_code": CLASS_CODE,
        }
        await message(SimpleNamespace(message=Message("Иван Иванов")))

    asyncio.run(flow())

    assert answers[-1].startswith("Нужны ровно две строки")
