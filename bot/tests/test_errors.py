"""403 — это не сбой бэкенда: бот отвечает «Этот запрос вам не доступен»."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from core.ui import FORBIDDEN_TEXT, error_text
from services import ai_chat, views
from services.backend import BackendError

answers: list[str] = []


class Message:
    def __init__(self) -> None:
        self.sender = SimpleNamespace(user_id=7, first_name="Иван")

    async def answer(self, text, attachments=None, format=None) -> None:
        answers.append(text)


EVENT = SimpleNamespace(message=Message())


class ForbiddenBackend:
    async def ai_chat(self, token: str, message: str) -> None:
        raise BackendError(403, "Access denied")


class Sessions:
    def role(self, user_id: int) -> str:
        return "teacher"


CTX = SimpleNamespace(backend=ForbiddenBackend(), sessions=Sessions())


async def _forbidden() -> None:
    raise BackendError(403, "Access denied")


def test_forbidden_is_not_reported_as_outage():
    exc = BackendError(403, "Access denied")
    assert error_text(exc, "Бэкенд недоступен") == FORBIDDEN_TEXT
    assert FORBIDDEN_TEXT == "Этот запрос вам не доступен"


def test_other_errors_keep_prefix_and_escape():
    exc = BackendError(500, "<boom>")
    text = error_text(exc, "Бэкенд недоступен")
    assert text == "Бэкенд недоступен: &lt;boom&gt;"


def test_safe_view_hides_access_denied():
    answers.clear()
    assert asyncio.run(views.safe_view(EVENT, _forbidden())) is None
    assert answers == [FORBIDDEN_TEXT]


def test_ai_chat_does_not_fall_back_on_forbidden(monkeypatch):
    def no_local_model(*args, **kwargs):
        raise AssertionError("403 не должен доходить до локальной модели")

    monkeypatch.setattr(
        ai_chat, "ai", SimpleNamespace(chat_reply=no_local_model)
    )
    answers.clear()
    asyncio.run(ai_chat.answer(CTX, EVENT, "jwt", "привет"))
    assert answers == [FORBIDDEN_TEXT]
