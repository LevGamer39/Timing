"""ИИ-чат: сначала бэкенд, затем локальная модель."""

from __future__ import annotations

from typing import Any

from core.context import AppContext
from core.ui import respond
from services import ai
from services.backend import BackendError
from texts import esc, tasks_block


async def answer(ctx: AppContext, event: Any, token: str | None, text: str) -> None:
    if token:
        try:
            response = await ctx.backend.ai_chat(token, text)
            await respond(event, esc(response.get("reply") or "Не смог ответить."))
            return
        except BackendError:
            pass

    context = ""
    if token:
        try:
            tasks = await ctx.backend.list_tasks(token)
            active = [task for task in tasks if task.get("status") != "done"][:5]
            context = "Активные задания:\n" + tasks_block(active)
        except BackendError:
            context = ""
    reply = await ai.chat_reply(text, context)
    await respond(event, esc(reply))
