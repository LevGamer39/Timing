"""Команда ИИ-чата."""

from __future__ import annotations

from maxapi import Router
from maxapi.filters.command import Command
from maxapi.types.updates.message_created import MessageCreated

from core.context import AppContext
from core.ui import event_user, require_token, respond, role_of
from keyboards import main_menu
from services.ai_chat import answer
from texts import PROCESSING_TEXT


def build_router(ctx: AppContext) -> Router:
    router = Router("ai_chat")

    @router.message_created(Command("ai"))
    async def ai_command(
        event: MessageCreated, args: list[str] | None = None
    ) -> None:
        text = " ".join(args or []).strip()
        if not text:
            user = event_user(event)
            if user is not None:
                ctx.ai_mode.add(user.user_id)
            await respond(
                event,
                "Напишите вопрос — отвечу как ИИ-помощник.",
                keyboard=main_menu(role_of(ctx, event)),
            )
            return
        token = await require_token(ctx, event)
        if token is not None:
            await respond(event, PROCESSING_TEXT)
            await answer(ctx, event, token, text)

    return router
