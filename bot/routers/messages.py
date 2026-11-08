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
from services.voice import audio_url
from texts import PARENT_CANNOT_ADD_TEXT, PROCESSING_TEXT, help_text


def build_router(ctx: AppContext) -> Router:
    router = Router("messages")

    @router.message_created()
    async def message(event: MessageCreated) -> None:
        # В групповых чатах голосовые распознаёт парсер ДЗ, и только если
        # для чата это включено. Личный чат — всегда.
        if not is_dialog(event):
            await ctx.parser.feed(event)
            return

        user = event_user(event)
        busy = user is not None and (
            user.user_id in ctx.registration or user.user_id in ctx.link
        )
        announced = False
        if not busy and audio_url(event.message.body):
            # Голосовое распознаётся дольше текста — отвечаем сразу.
            await respond(event, PROCESSING_TEXT)
            announced = True

        text = await message_text(ctx, event)
        if not text:
            await respond(
                event,
                "Не удалось распознать голосовое. Напишите задание текстом.",
            )
            return
        ctx.parser.remember(getattr(event.message.body, "mid", None), text)

        if text.lstrip().startswith("/"):
            role = role_of(ctx, event)
            await respond(
                event,
                "Неизвестная команда.\n\n" + help_text(role),
                keyboard=main_menu(role),
            )
            return

        # Регистрация идёт до require_token: у нового пользователя JWT ещё
        # нет, а логин и код приглашения нужно собрать именно у него.
        if user is not None and await link.handle_text(
            ctx, event, user, text
        ):
            return
        if user is not None and await registration.handle_text(
            ctx, event, user, text
        ):
            return

        role = role_of(ctx, event) if hasattr(ctx.sessions, "role") else None
        in_ai_mode = user is not None and user.user_id in ctx.ai_mode
        if role == "parent" and not in_ai_mode:
            await respond(
                event,
                PARENT_CANNOT_ADD_TEXT,
                keyboard=main_menu(role),
            )
            return

        token = await require_token(ctx, event)
        if token is None:
            return

        if not announced:
            await respond(event, PROCESSING_TEXT)

        if in_ai_mode:
            await ai_answer(ctx, event, token, text)
            return
        await create_from_text(ctx, event, token, text)

    return router
