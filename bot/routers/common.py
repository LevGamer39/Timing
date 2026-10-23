"""Общие события и команды бота."""

from __future__ import annotations

from maxapi import Router
from maxapi.enums.parse_mode import Format
from maxapi.filters.command import Command, CommandStart
from maxapi.types.updates.bot_added import BotAdded
from maxapi.types.updates.bot_removed import BotRemoved
from maxapi.types.updates.bot_started import BotStarted
from maxapi.types.updates.message_created import MessageCreated
from maxapi.types.updates.message_edited import MessageEdited

from core.context import AppContext
from core.ui import event_user, get_token, respond, role_of
from keyboards import main_menu
from texts import HELP_TEXT, UNLINKED_TEXT, esc


def build_router(ctx: AppContext) -> Router:
    router = Router("common")

    @router.bot_started()
    async def bot_started(event: BotStarted) -> None:
        user_id = event.user.user_id
        ctx.store.upsert_user(
            user_id,
            chat_id=event.chat_id,
            first_name=event.user.first_name,
            username=event.user.username,
        )
        token = await ctx.sessions.token(user_id)
        greeting = f"Привет, {esc(event.user.first_name)}! 👋"
        text = greeting if token else f"{greeting}\n\n{UNLINKED_TEXT}"
        await ctx.bot.send_message(
            chat_id=event.chat_id,
            text=text,
            attachments=[main_menu(ctx.sessions.role(user_id))],
            format=Format.HTML,
        )
        await ctx.store.save()

    @router.bot_added()
    async def bot_added(event: BotAdded) -> None:
        if event.is_channel:
            return
        title = None
        try:
            chat = await event.fetch_chat()
            title = chat.title if chat else None
        except Exception:  # noqa: BLE001
            pass
        await ctx.parser.enable(event.chat_id, title)
        await ctx.bot.send_message(
            chat_id=event.chat_id,
            text=(
                "Я подключён к чату класса и буду искать домашние задания.\n"
                "Автопоиск уже включён. /hw off — выключить, "
                "/hw — разобрать сообщение, на которое вы ответили."
            ),
            format=Format.HTML,
        )

    @router.bot_removed()
    async def bot_removed(event: BotRemoved) -> None:
        await ctx.parser.disable(event.chat_id)

    @router.message_edited()
    async def message_edited(event: MessageEdited) -> None:
        await ctx.parser.feed_edited(event)

    @router.message_created(CommandStart())
    async def start(event: MessageCreated) -> None:
        token = await get_token(ctx, event)
        text = UNLINKED_TEXT
        if token:
            sender = event_user(event)
            text = f"Привет, {esc(sender.first_name)}! 👋\nЯ помогу не забыть про домашку."
        await respond(event, text, keyboard=main_menu(role_of(ctx, event)))
        await ctx.store.save()

    @router.message_created(Command("help"))
    async def help_command(event: MessageCreated) -> None:
        await respond(event, HELP_TEXT, keyboard=main_menu(role_of(ctx, event)))

    @router.message_created(Command("cancel"))
    async def cancel(event: MessageCreated) -> None:
        user = event_user(event)
        if user is not None:
            ctx.ai_mode.discard(user.user_id)
        await respond(
            event,
            "Вышли из ИИ-чата.",
            keyboard=main_menu(role_of(ctx, event)),
        )

    return router
