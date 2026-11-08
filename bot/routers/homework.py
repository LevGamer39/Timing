"""Команды и кнопки парсера домашних заданий."""

from __future__ import annotations

from maxapi import Router
from maxapi.filters.command import Command
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated

from config import settings
from core.context import AppContext
from core.filters import PayloadPrefix
from core.ui import (
    event_chat_id,
    event_user,
    is_dialog,
    require_token,
    respond,
    role_of,
)
from keyboards import home_keyboard, main_menu
from services.tasks import add_homework
from texts import PARENT_CANNOT_ADD_TEXT


def build_router(ctx: AppContext) -> Router:
    router = Router("homework")

    async def is_chat_admin(event: MessageCreated, chat_id: int) -> bool:
        user = event_user(event)
        if user is None:
            return False
        try:
            admins = await ctx.bot.get_list_admin_chat(chat_id=chat_id)
        except Exception:  # noqa: BLE001
            return False
        return any(
            member.user_id == user.user_id
            and (member.is_admin or member.is_owner)
            for member in admins.members
        )

    @router.message_created(Command("hw"))
    async def hw_command(
        event: MessageCreated, args: list[str] | None = None
    ) -> None:
        mode = (args[0] if args else "").lower()
        chat_id = event_chat_id(event)
        if chat_id is None:
            return

        if mode in {"on", "off"}:
            if not await is_chat_admin(event, chat_id):
                await respond(event, "Менять парсер может только админ чата.")
                return
            if mode == "on":
                await ctx.parser.enable(chat_id)
                await respond(
                    event,
                    "Парсер ДЗ включён. Буду присылать найденные задания "
                    f"раз в {settings.parser_batch_interval_minutes} мин.",
                )
            else:
                await ctx.parser.disable(chat_id)
                await respond(event, "Парсер ДЗ выключен.")
            return

        await ctx.parser.manual(event)

    @router.message_callback(PayloadPrefix("hw:"))
    async def hw_callback(event: MessageCallback) -> None:
        payload = event.callback.payload or ""
        uid = int(payload.rsplit(":", 1)[1])
        if payload.startswith("hw:skip:"):
            ctx.parser.pop_pending(uid)
            await respond(
                event,
                "Ок, пропустил.",
                keyboard=home_keyboard() if is_dialog(event) else None,
            )
            return

        item = ctx.parser.pop_pending(uid)
        if item is None:
            await respond(event, "Карточка устарела. Отметьте задание заново.")
            return
        if role_of(ctx, event) == "parent":
            await respond(
                event,
                PARENT_CANNOT_ADD_TEXT,
                keyboard=main_menu("parent") if is_dialog(event) else None,
            )
            return
        token = await require_token(ctx, event)
        if token is not None:
            await add_homework(ctx, event, token, item)

    return router
