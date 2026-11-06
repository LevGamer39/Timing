"""Хелперы для ответов и определения автора события."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maxapi.enums.chat_type import ChatType
from maxapi.enums.parse_mode import Format
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated

from services.voice import body_text
from texts.messages import UNLINKED_TEXT

if TYPE_CHECKING:
    from core.context import AppContext


def event_user(event: Any) -> Any | None:
    """Возвращает автора сообщения или нажатия кнопки."""
    if isinstance(event, MessageCallback):
        return event.callback.user
    message = getattr(event, "message", None)
    return message.sender if message else None


def event_chat_id(event: Any) -> int | None:
    """Возвращает chat_id, в который нужно ответить."""
    message = getattr(event, "message", None)
    if not message:
        return None
    return message.recipient.chat_id


async def message_text(
    ctx: AppContext, event: MessageCreated
) -> str | None:
    """Текст сообщения, транскрипция MAX или распознанное голосовое."""
    user = event_user(event)
    user_id = getattr(user, "user_id", 0) or 0
    return await body_text(event.message.body, ctx.transcriber, user_id)


def is_dialog(event: Any) -> bool:
    """True, если событие пришло в личный чат с ботом.

    Подходит и для callback: у него ``message`` может отсутствовать.
    """
    message = getattr(event, "message", None)
    recipient = getattr(message, "recipient", None)
    return getattr(recipient, "chat_type", None) == ChatType.DIALOG


async def respond(event: Any, text: str, keyboard: Any | None = None) -> None:
    """Отвечает сообщением или редактирует сообщение с кнопкой."""
    attachments = [keyboard] if keyboard is not None else None
    if isinstance(event, MessageCallback):
        await event.answer(new_text=text, attachments=attachments)
        return
    await event.message.answer(
        text=text,
        attachments=attachments,
        format=Format.HTML,
    )


def remember(ctx: AppContext, event: Any) -> Any | None:
    """Запоминает пользователя и его чат для напоминаний."""
    user = event_user(event)
    if user is None:
        return None
    ctx.store.upsert_user(
        user.user_id,
        chat_id=event_chat_id(event),
        first_name=getattr(user, "first_name", None),
        username=getattr(user, "username", None),
    )
    return user


async def get_token(ctx: AppContext, event: Any) -> str | None:
    """Возвращает JWT пользователя, при необходимости запрашивая его у бэкенда."""
    user = remember(ctx, event)
    if user is None:
        return None
    return await ctx.sessions.token(user.user_id)


async def require_token(ctx: AppContext, event: Any) -> str | None:
    """Как get_token, но один раз объясняет, что аккаунт не привязан."""
    token = await get_token(ctx, event)
    if token is not None:
        return token

    user = event_user(event)
    if user is not None:
        if user.user_id in ctx.notified_unlinked:
            return None
        ctx.notified_unlinked.add(user.user_id)
    await respond(event, UNLINKED_TEXT)
    return None


def role_of(ctx: AppContext, event: Any) -> str | None:
    """Роль пользователя из локального состояния."""
    user = event_user(event)
    return ctx.sessions.role(user.user_id) if user else None
