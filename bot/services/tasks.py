"""Создание заданий из текста и данных парсера."""

from __future__ import annotations

from typing import Any

from core.context import AppContext
from core.ui import respond
from keyboards import task_actions
from services import ai
from services.backend import BackendError
from texts import esc, fmt_deadline, task_line


def task_payload(parsed: dict[str, Any], source: str) -> dict[str, Any]:
    deadline = parsed.get("deadline")
    if deadline:
        deadline = f"{deadline}T23:59:00+00:00"
    return {
        "title": (parsed.get("title") or "Домашнее задание")[:120],
        "subject": parsed.get("subject") or None,
        "description": (source or "")[:1000] or None,
        "deadline": deadline,
        "priority": parsed.get("priority") or "medium",
    }


async def create_from_text(
    ctx: AppContext, event: Any, token: str, text: str
) -> None:
    """Разбирает текст и создаёт задание."""
    await respond(event, "Разбираю задание…")
    parsed = await ai.parse_task(text)
    if not parsed.get("is_homework"):
        await respond(
            event,
            "Не похоже на задание. Если это всё-таки ДЗ — начните текст "
            "со слова «задание».",
        )
        return

    try:
        task = await ctx.backend.create_task(token, task_payload(parsed, text))
    except BackendError as exc:
        await respond(event, f"Не получилось создать задание: {esc(exc.detail)}")
        return

    details = [
        "✅ <b>Задание добавлено</b>",
        task_line(task),
        f"Срок: {esc(fmt_deadline(task.get('deadline')))}",
    ]
    if task.get("subject"):
        details.append(f"Предмет: {esc(task['subject'])}")
    await respond(
        event,
        "\n".join(details),
        keyboard=task_actions(int(task["id"]), done=False),
    )


async def add_homework(
    ctx: AppContext, event: Any, token: str, item: dict[str, Any]
) -> None:
    """Добавляет задание, найденное парсером."""
    payload = task_payload(
        {
            "title": item.get("title", "Домашнее задание"),
            "subject": item.get("subject"),
            "deadline": item.get("deadline"),
            "priority": item.get("priority", "medium"),
        },
        source=item.get("source", ""),
    )
    try:
        task = await ctx.backend.create_task(token, payload)
    except BackendError as exc:
        await respond(event, f"Не получилось: {esc(exc.detail)}")
        return
    await respond(event, f"✅ Добавлено: {task_line(task)}")
