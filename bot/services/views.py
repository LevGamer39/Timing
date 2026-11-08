"""Готовые представления данных для команд и меню."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from keyboards import tasks_keyboard
from services.backend import Backend, BackendError
from texts import (
    averages_block,
    children_block,
    esc,
    grades_block,
    schedule_block,
    summary_block,
    tasks_block,
)


def is_due(task: dict[str, Any]) -> bool:
    deadline = task.get("deadline")
    if not deadline:
        return False
    try:
        parsed = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.date() <= date.today()


async def today(
    backend: Backend, token: str, role: str | None = None
) -> tuple[str, Any | None]:
    if role == "parent":
        return await _parent_today(backend, token), None
    tasks = await backend.list_tasks(token)
    active = [task for task in tasks if task.get("status") != "done"]
    # «Что на сегодня» показывает сегодняшние задачи и то, что ждёт закрытия.
    # Раньше при их отсутствии выводился весь список активных.
    due = [task for task in active if is_due(task)]
    if not due:
        # Пустая клавиатура не годится: под сообщением остался бы старый
        # ряд кнопок. Оставляем хотя бы выход в главное меню.
        return "На сегодня заданий нет 🎉", tasks_keyboard([])
    return (
        f"<b>На сегодня и то, что ждёт закрытия</b>\n{tasks_block(due)}",
        tasks_keyboard(due),
    )


async def grades(
    backend: Backend, token: str, role: str | None = None
) -> tuple[str, None]:
    if role == "parent":
        return await _parent_grades(backend, token), None
    averages = await backend.my_averages(token)
    marks = await backend.my_grades(token)
    return (
        f"<b>Средние баллы</b>\n{averages_block(averages)}\n\n"
        f"<b>Последние оценки</b>\n{grades_block(marks)}",
        None,
    )


async def summary(backend: Backend, token: str, role: str | None) -> tuple[str, None]:
    if role == "parent":
        return await _parent_summary(backend, token), None
    data = await backend.my_grade_summary(token)
    return f"<b>Ваша сводка</b>\n{summary_block(data)}", None


async def schedule(
    backend: Backend, token: str, role: str | None = None
) -> tuple[str, None]:
    if role == "parent":
        return await _parent_schedule(backend, token), None
    entries = await backend.my_schedule(token)
    return f"<b>Расписание</b>\n{schedule_block(entries)}", None


async def done(
    backend: Backend, token: str, role: str | None = None
) -> tuple[str, Any | None]:
    if role == "parent":
        return await _parent_active(backend, token, "<b>Активные задания детей</b>"), None
    tasks = await backend.list_tasks(token)
    active = [task for task in tasks if task.get("status") != "done"]
    if not active:
        return "Активных заданий нет 🎉", None
    return f"<b>Отметьте выполненное</b>\n{tasks_block(active)}", tasks_keyboard(active)


def child_name(child: dict[str, Any]) -> str:
    first = str(child.get("first_name") or "").strip()
    last = str(child.get("last_name") or "").strip()
    name = " ".join(part for part in (first, last) if part)
    return name or f"id {child.get('id')}"


NO_CHILDREN_TEXT = "Дети не привязаны к аккаунту."


async def _parent_today(backend: Backend, token: str) -> str:
    children = await backend.children(token)
    if not children:
        return NO_CHILDREN_TEXT
    blocks = []
    for child in children:
        tasks = await backend.child_tasks(token, int(child["id"]))
        active = [task for task in tasks if task.get("status") != "done"]
        due = [task for task in active if is_due(task)]
        body = tasks_block(due) if due else "На сегодня заданий нет 🎉"
        blocks.append(f"<b>{esc(child_name(child))}</b>\n{body}")
    return "<b>На сегодня и то, что ждёт закрытия</b>\n\n" + "\n\n".join(blocks)


async def _parent_active(backend: Backend, token: str, title: str) -> str:
    children = await backend.children(token)
    if not children:
        return NO_CHILDREN_TEXT
    blocks = []
    for child in children:
        tasks = await backend.child_tasks(token, int(child["id"]))
        active = [task for task in tasks if task.get("status") != "done"]
        body = tasks_block(active) if active else "Активных заданий нет 🎉"
        blocks.append(f"<b>{esc(child_name(child))}</b>\n{body}")
    return f"{title}\n\n" + "\n\n".join(blocks)


async def _parent_grades(backend: Backend, token: str) -> str:
    children = await backend.children(token)
    if not children:
        return NO_CHILDREN_TEXT
    blocks = []
    for child in children:
        student_id = int(child["id"])
        averages = await backend.child_averages(token, student_id)
        marks = await backend.child_grades(token, student_id)
        blocks.append(
            f"<b>{esc(child_name(child))}</b>\n"
            f"<b>Средние баллы</b>\n{averages_block(averages)}\n\n"
            f"<b>Последние оценки</b>\n{grades_block(marks)}"
        )
    return "\n\n".join(blocks)


async def _parent_summary(backend: Backend, token: str) -> str:
    children = await backend.children(token)
    if not children:
        return NO_CHILDREN_TEXT
    blocks = []
    for child in children:
        data = await backend.child_summary(token, int(child["id"]))
        blocks.append(
            f"<b>{esc(child_name(child))}</b>\n{summary_block(data)}"
        )
    return "<b>Сводка по детям</b>\n\n" + "\n\n".join(blocks)


async def _parent_schedule(backend: Backend, token: str) -> str:
    children = await backend.children(token)
    if not children:
        return NO_CHILDREN_TEXT
    blocks = []
    for child in children:
        entries = await backend.child_schedule(token, int(child["id"]))
        blocks.append(
            f"<b>{esc(child_name(child))}</b>\n{schedule_block(entries)}"
        )
    return "<b>Расписание детей</b>\n\n" + "\n\n".join(blocks)


async def children(backend: Backend, token: str) -> tuple[str, None]:
    return children_block(await backend.children(token)), None


async def classes(backend: Backend, token: str) -> tuple[str, None]:
    items = await backend.my_classes(token)
    if not items:
        return "Классов пока нет.", None
    lines = [
        f"• {esc(item.get('name', 'Класс'))} — "
        f"{item.get('student_count', 0)} учеников"
        for item in items
    ]
    return "<b>Мои классы</b>\n" + "\n".join(lines), None


async def safe_view(
    event: Any, coro: Any
) -> tuple[str, Any | None] | None:
    """Выполняет представление и показывает ошибку бэкенда пользователю."""
    from core.ui import error_text, respond

    try:
        return await coro
    except BackendError as exc:
        await respond(event, error_text(exc, "Бэкенд недоступен"))
        return None
