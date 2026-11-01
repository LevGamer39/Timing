"""Свободный текст, голосовые и неизвестные команды."""

from __future__ import annotations

from maxapi import Router
from maxapi.types.updates.message_created import MessageCreated

from core.context import AppContext
from core.ui import (
    event_user,
    is_dialog,
    message_text,
    require_token,
    respond,
    role_of,
)
from keyboards import main_menu
from services import registration, link
from services.ai_chat import answer as ai_answer
from services.tasks import create_from_text
from texts import HELP_TEXT


def build_router(ctx: AppContext) -> Router:
    router = Router("messages")

    @router.message_created()
    async def message(event: MessageCreated) -> None:
        text = message_text(event)
        if not text:
            if is_dialog(event):
                await respond(
                    event,
                    "Не удалось распознать голосовое. Напишите задание текстом.",
                )
            return

        if not is_dialog(event):
            await ctx.parser.feed(event)
            return

        if text.lstrip().startswith("/"):
            await respond(
                event,
                "Неизвестная команда.\n\n" + HELP_TEXT,
                keyboard=main_menu(role_of(ctx, event)),
            )
            return

        # Регистрация идёт до require_token: у нового пользователя JWT ещё
        # нет, а логин и код приглашения нужно собрать именно у него.
        user = event_user(event)
        if user is not None and await link.handle_text(
            ctx, event, user, text
        ):
            return
        if user is not None and await registration.handle_text(
            ctx, event, user, text
        ):
            return

        token = await require_token(ctx, event)
        if token is None:
            return

        if user is not None and user.user_id in ctx.ai_mode:
            await ai_answer(ctx, event, token, text)
            return
        await create_from_text(ctx, event, token, text)

    return router
