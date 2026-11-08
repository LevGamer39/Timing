"""ИИ-чат: сначала бэкенд, затем локальная модель."""

from __future__ import annotations

from typing import Any

from core.context import AppContext
from core.ui import error_text, respond, role_of
from keyboards import main_menu
from services import ai
from services.backend import BackendError
from texts import esc, tasks_block


async def answer(ctx: AppContext, event: Any, token: str | None, text: str) -> None:
    role = role_of(ctx, event)
    keyboard = main_menu(role)
    if token:
        try:
            response = await ctx.backend.ai_chat(token, text)
            await respond(
                event,
                esc(response.get("reply") or "Не смог ответить."),
                keyboard=keyboard,
            )
            return
        except BackendError as exc:
            if exc.status == 403:
                await respond(event, error_text(exc, "Бэкенд недоступен"))
                return

    # Бэкенд недоступен (сеть, 5xx): отвечает локальная модель без его данных.
    context = ""
    if token:
        try:
            if role == "parent":
                context = await _children_context(ctx, token)
            else:
                tasks = await ctx.backend.list_tasks(token)
                active = [
                    task for task in tasks if task.get("status") != "done"
                ][:5]
                context = "Активные задания:\n" + tasks_block(active)
        except BackendError:
            context = ""
    reply = await ai.chat_reply(text, context, role)
    await respond(event, esc(reply), keyboard=keyboard)


async def _children_context(ctx: AppContext, token: str) -> str:
    children = await ctx.backend.children(token)
    blocks = []
    for child in children[:3]:
        first = str(child.get("first_name") or "").strip()
        last = str(child.get("last_name") or "").strip()
        name = " ".join(part for part in (first, last) if part) or (
            f"id {child.get('id')}"
        )
        tasks = await ctx.backend.child_tasks(token, int(child["id"]))
        active = [task for task in tasks if task.get("status") != "done"][:5]
        blocks.append(f"{name}:\n{tasks_block(active)}")
    return "\n\n".join(blocks)
