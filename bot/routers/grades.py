"""Команды оценок и сводки."""

from __future__ import annotations

from maxapi import Router
from maxapi.filters.command import Command
from maxapi.types.updates.message_created import MessageCreated

from core.context import AppContext
from core.ui import require_token, respond, role_of
from keyboards import main_menu
from services import views


def build_router(ctx: AppContext) -> Router:
    router = Router("grades")

    @router.message_created(Command("grades"))
    async def grades(event: MessageCreated) -> None:
        token = await require_token(ctx, event)
        if token is None:
            return
        result = await views.safe_view(event, views.grades(ctx.backend, token))
        if result is not None:
            await respond(
                event,
                result[0],
                keyboard=main_menu(role_of(ctx, event)),
            )

    @router.message_created(Command("summary"))
    async def summary(event: MessageCreated) -> None:
        token = await require_token(ctx, event)
        if token is None:
            return
        result = await views.safe_view(
            event, views.summary(ctx.backend, token, role_of(ctx, event))
        )
        if result is not None:
            await respond(
                event,
                result[0],
                keyboard=main_menu(role_of(ctx, event)),
            )

    return router
