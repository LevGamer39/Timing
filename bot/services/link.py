"""Привязка MAX к аккаунту, созданному на сайте."""

from __future__ import annotations

from typing import Any

from core.context import AppContext
from core.security import hash_user_id
from core.ui import error_text, event_user, respond
from keyboards import main_menu
from services.backend import BackendError

LOGIN = "login"
PASSWORD = "password"


def begin(ctx: AppContext, user_id: int) -> None:
    ctx.link[user_id] = {"step": LOGIN}


async def handle_text(
    ctx: AppContext, event: Any, user: Any, text: str
) -> bool:
    state = ctx.link.get(user.user_id)
    if state is None:
        return False
    answer = text.strip()

    if state.get("step") == LOGIN:
        if not answer or any(c.isspace() for c in answer):
            await respond(event, "Логин не должен содержать пробелов.")
            return True
        state["login"] = answer
        state["step"] = PASSWORD
        await respond(event, "Введите пароль от аккаунта на сайте.")
        return True

    try:
        payload = await ctx.backend.link_web_account(
            hash_user_id(user.user_id), state.get("login", ""), answer
        )
    except BackendError as exc:
        ctx.link.pop(user.user_id, None)
        await respond(event, error_text(exc, "Не удалось привязать"))
        return True

    ctx.link.pop(user.user_id, None)
    token = str(payload["access_token"])
    ctx.sessions.remember(user.user_id, token, payload)
    await ctx.store.save()
    await respond(
        event,
        "Готово! Аккаунт привязан к MAX. Теперь сюда приходят задания "
        "и оценки, а на сайт можно входить по коду из /code.",
        keyboard=main_menu(payload.get("role")),
    )
    return True
