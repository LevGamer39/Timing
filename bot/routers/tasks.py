"""Команды и кнопки для заданий."""

from __future__ import annotations

from maxapi import Router
from maxapi.filters.command import Command
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated

from core.context import AppContext
from core.filters import PayloadPrefix
from core.ui import error_text, event_user, require_token, respond, role_of
from keyboards import home_keyboard, main_menu
from services import views
from services.backend import BackendError
from services.tasks import create_from_text
from texts import PARENT_CANNOT_ADD_TEXT, PROCESSING_TEXT, task_line

PARENT_CANNOT_EDIT_TEXT = (
    "Родители не могут менять задачи детей — это делает сам ученик."
)


def build_router(ctx: AppContext) -> Router:
    router = Router("tasks")

    @router.message_created(Command("today"))
    async def today(event: MessageCreated) -> None:
        token = await require_token(ctx, event)
        if token is None:
            return
        role = role_of(ctx, event)
        result = await views.safe_view(
            event, views.today(ctx.backend, token, role)
        )
        if result is not None:
            await respond(event, result[0], result[1] or main_menu(role))

    @router.message_created(Command("add"))
    async def add(event: MessageCreated, args: list[str] | None = None) -> None:
        user = event_user(event)
        if user is not None:
            ctx.ai_mode.discard(user.user_id)
        role = role_of(ctx, event)
        if role == "parent":
            await respond(
                event,
                PARENT_CANNOT_ADD_TEXT,
                keyboard=main_menu(role),
            )
            return
        text = " ".join(args or []).strip()
        if not text:
            await respond(
                event,
                "Пришлите текст задания: «Математика, №42, к пятнице» "
                "или отправьте голосовое.",
                keyboard=home_keyboard("🚫 Отмена"),
            )
            return
        token = await require_token(ctx, event)
        if token is not None:
            await respond(event, PROCESSING_TEXT)
            await create_from_text(ctx, event, token, text)

    @router.message_created(Command("done"))
    async def done(event: MessageCreated) -> None:
        token = await require_token(ctx, event)
        if token is None:
            return
        role = role_of(ctx, event)
        result = await views.safe_view(
            event, views.done(ctx.backend, token, role)
        )
        if result is not None:
            await respond(event, result[0], result[1] or main_menu(role))

    @router.message_callback(PayloadPrefix("task:"))
    async def task_callback(event: MessageCallback) -> None:
        payload = event.callback.payload or ""
        task_id = int(payload.rsplit(":", 1)[1])
        token = await require_token(ctx, event)
        if token is None:
            return
        role = role_of(ctx, event)
        if role == "parent":
            await respond(
                event, PARENT_CANNOT_EDIT_TEXT, keyboard=main_menu(role)
            )
            return
        if payload.startswith("task:done:"):
            try:
                task = await ctx.backend.complete_task(token, task_id)
            except BackendError as exc:
                await respond(event, error_text(exc, "Не получилось"))
                return
            await respond(
                event,
                f"✅ Выполнено: {task_line(task)}",
                keyboard=main_menu(role_of(ctx, event)),
            )
            return
        try:
            await ctx.backend.delete_task(token, task_id)
        except BackendError as exc:
            await respond(event, error_text(exc, "Не получилось"))
            return
        await respond(
            event,
            "🗑 Задание удалено.",
            keyboard=main_menu(role_of(ctx, event)),
        )

    return router
