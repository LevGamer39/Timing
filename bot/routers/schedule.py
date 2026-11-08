"""Команда расписания."""

from __future__ import annotations

from maxapi import Router
from maxapi.filters.command import Command
from maxapi.types.updates.message_created import MessageCreated

from core.context import AppContext
from core.ui import require_token, respond, role_of
from keyboards import main_menu
from services import views


def build_router(ctx: AppContext) -> Router:
    router = Router("schedule")

    @router.message_created(Command("schedule"))
    async def schedule(event: MessageCreated) -> None:
        token = await require_token(ctx, event)
        if token is None:
            return
        result = await views.safe_view(
            event, views.schedule(ctx.backend, token, role_of(ctx, event))
        )
        if result is not None:
            await respond(
                event,
                result[0],
                keyboard=main_menu(role_of(ctx, event)),
            )

    return router
