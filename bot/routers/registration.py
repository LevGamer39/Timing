"""Callback-кнопки диалога регистрации."""

from __future__ import annotations

from maxapi import Router
from maxapi.types.updates.message_callback import MessageCallback

from core.context import AppContext
from core.filters import PayloadPrefix
from services import registration


def build_router(ctx: AppContext) -> Router:
    router = Router("registration")

    @router.message_callback(PayloadPrefix("reg:"))
    async def registration_callback(event: MessageCallback) -> None:
        await registration.handle_callback(
            ctx, event, event.callback.payload or ""
        )

    return router
