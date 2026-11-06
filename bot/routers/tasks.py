"""Команды и кнопки для заданий."""

from __future__ import annotations

from maxapi import Router
from maxapi.filters.command import Command
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated

from core.context import AppContext
from core.filters import PayloadPrefix
from core.ui import event_user, require_token, respond, role_of
from keyboards import home_keyboard, main_menu, tasks_keyboard
from services import views
from services.backend import BackendError
from services.tasks import create_from_text
from texts import PROCESSING_TEXT, esc, task_line, tasks_block


def build_router(ctx: AppContext) -> Router:
    router = Router("tasks")

    @router.message_created(Command("today"))
    async def today(event: MessageCreated) -> None:
        token = await require_token(ctx, event)
        if token is None:
            return
        result = await views.safe_view(event, views.today(ctx.backend, token))
        if result is not None:
            await respond(event, result[0], result[1])

    @router.message_created(Command("add"))
    async def add(event: MessageCreated, args: list[str] | None = None) -> None:
        user = event_user(event)
        if user is not None:
            ctx.ai_mode.discard(user.user_id)
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
        try:
            tasks = await ctx.backend.list_tasks(token)
        except BackendError as exc:
            await respond(event, f"Бэкенд недоступен: {esc(exc.detail)}")
            return
        active = [task for task in tasks if task.get("status") != "done"]
        if not active:
            await respond(
                event,
                "Активных заданий нет 🎉",
                keyboard=main_menu(role_of(ctx, event)),
            )
            return
        await respond(
            event,
            f"<b>Отметьте выполненное</b>\n{tasks_block(active)}",
            keyboard=tasks_keyboard(active),
        )

    @router.message_callback(PayloadPrefix("task:"))
    async def task_callback(event: MessageCallback) -> None:
        payload = event.callback.payload or ""
        task_id = int(payload.rsplit(":", 1)[1])
        token = await require_token(ctx, event)
        if token is None:
            return
        if payload.startswith("task:done:"):
            try:
                task = await ctx.backend.complete_task(token, task_id)
            except BackendError as exc:
                await respond(event, f"Не получилось: {esc(exc.detail)}")
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
            await respond(event, f"Не получилось: {esc(exc.detail)}")
            return
        await respond(
            event,
            "🗑 Задание удалено.",
            keyboard=main_menu(role_of(ctx, event)),
        )

    return router
