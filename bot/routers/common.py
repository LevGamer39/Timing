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
from core.security import hash_user_id
from core.ui import (
    error_text,
    event_user,
    get_token,
    message_text,
    require_token,
    respond,
    role_of,
)
from keyboards import main_menu, register_keyboard
from services.backend import BackendError
from services import link
from texts import UNLINKED_TEXT, greeting, help_text


def build_router(ctx: AppContext) -> Router:
    router = Router("common")

    @router.bot_started()
    async def bot_started(event: BotStarted) -> None:
        user_id = event.user.user_id
        # Повторный вход начинает диалог регистрации с чистого листа.
        ctx.registration.pop(user_id, None)
        ctx.store.upsert_user(
            user_id,
            chat_id=event.chat_id,
            first_name=event.user.first_name,
            username=event.user.username,
        )
        token = await ctx.sessions.token(user_id)
        role = ctx.sessions.role(user_id)
        text = greeting(role, event.user.first_name)
        if not token:
            text = f"{text}\n\n{UNLINKED_TEXT}"
        await ctx.bot.send_message(
            chat_id=event.chat_id,
            text=text,
            attachments=[main_menu(role) if token else register_keyboard()],
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
        # /start — способ выйти из регистрации и начать заново.
        sender = event_user(event)
        if sender is not None:
            ctx.registration.pop(sender.user_id, None)
            ctx.link.pop(sender.user_id, None)
        token = await get_token(ctx, event)
        role = role_of(ctx, event)
        text = UNLINKED_TEXT
        if token:
            text = greeting(role, sender.first_name)
        await respond(
            event,
            text,
            keyboard=main_menu(role) if token else register_keyboard(),
        )
        await ctx.store.save()

    @router.message_created(Command("link"))
    async def link_command(event: MessageCreated) -> None:
        """Привязка к аккаунту, который уже есть на сайте."""
        token = await get_token(ctx, event)
        if token is not None:
            await respond(event, "Аккаунт MAX уже привязан.")
            return
        user = event_user(event)
        if user is None:
            return
        ctx.registration.pop(user.user_id, None)
        link.begin(ctx, user.user_id)
        await respond(event, "Введите логин аккаунта, созданного на сайте.")

    @router.message_created(Command("code"))
    async def code(event: MessageCreated) -> None:
        """Код для входа в SPA вместо пароля."""
        token = await get_token(ctx, event)
        if token is None:
            return
        user = event_user(event)
        if user is None:
            return
        try:
            payload = await ctx.backend.issue_max_code(
                hash_user_id(user.user_id)
            )
        except BackendError as exc:
            await respond(event, error_text(exc, "Не удалось получить код"))
            return
        await respond(
            event,
            "Код для входа на сайт: <code>"
            + str(payload.get("code", ""))
            + "</code>\nДействует 10 минут. Введите его на странице входа "
            "«Войти через MAX».",
            keyboard=main_menu(role_of(ctx, event)),
        )

    @router.message_created(Command("notify"))
    async def notify_command(event: MessageCreated) -> None:
        """Включение и выключение уведомлений от учителя в MAX."""
        token = await require_token(ctx, event)
        if token is None:
            return
        parts = (message_text(event) or "").split(maxsplit=1)
        arg = parts[1].strip().lower() if len(parts) > 1 else ""
        try:
            profile = await ctx.backend.profile(token)
            enabled = bool(profile.get("notify_max", True))
            if arg in {"вкл", "on"}:
                target = True
            elif arg in {"выкл", "off"}:
                target = False
            else:
                target = not enabled
            if target != enabled:
                await ctx.backend.update_profile(
                    token, {"notify_max": target}
                )
        except BackendError as exc:
            await respond(event, error_text(exc, "Не получилось"))
            return

        state = "включены" if target else "выключены"
        await respond(
            event,
            f"Уведомления о работах: {state}.\n"
            "Напоминания о сроках приходят всегда.",
        )

    @router.message_created(Command("help"))
    async def help_command(event: MessageCreated) -> None:
        role = role_of(ctx, event)
        await respond(event, help_text(role), keyboard=main_menu(role))

    @router.message_created(Command("cancel"))
    async def cancel(event: MessageCreated) -> None:
        user = event_user(event)
        if user is not None:
            ctx.ai_mode.discard(user.user_id)
            ctx.registration.pop(user.user_id, None)
            ctx.link.pop(user.user_id, None)
        await respond(
            event,
            "Вышли из текущего режима.",
            keyboard=main_menu(role_of(ctx, event)),
        )

    return router
