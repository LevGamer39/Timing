"""Форматирование сообщений бота."""

from __future__ import annotations

from datetime import date, datetime, timezone
from html import escape
from typing import Any

WEEKDAYS = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
]

PRIORITY_LABELS = {
    "low": "низкий",
    "medium": "обычный",
    "high": "высокий",
    "critical": "критический",
}


def esc(value: Any) -> str:
    return escape(str(value), quote=False)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def fmt_deadline(value: str | None) -> str:
    parsed = _parse_dt(value)
    if parsed is None:
        return "без срока"

    today = date.today()
    local_date = parsed.astimezone().date()
    delta = (local_date - today).days

    if delta < 0:
        return f"ждёт закрытия ({local_date.strftime('%d.%m')})"
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "завтра"
    return local_date.strftime("%d.%m")


def task_line(task: dict[str, Any]) -> str:
    title = esc(task.get("title", "Без названия"))
    subject = task.get("subject")
    priority = task.get("priority", "medium")
    parts = [f"<b>{title}</b>"]
    if subject:
        parts.append(f"— {esc(subject)}")
    parts.append(f"· {fmt_deadline(task.get('deadline'))}")
    if priority in {"high", "critical"}:
        parts.append(f"· <b>{PRIORITY_LABELS[priority]}</b>")
    return " ".join(parts)


def tasks_block(tasks: list[dict[str, Any]], limit: int = 10) -> str:
    if not tasks:
        return "Заданий нет — можно выдохнуть."
    lines = [task_line(task) for task in tasks[:limit]]
    if len(tasks) > limit:
        lines.append(f"…и ещё {len(tasks) - limit}")
    return "\n".join(f"• {line}" for line in lines)


def grades_block(grades: list[dict[str, Any]], limit: int = 12) -> str:
    if not grades:
        return "Оценок пока нет."
    lines = []
    for grade in grades[:limit]:
        lines.append(
            f"• {esc(grade.get('subject', '—'))}: "
            f"<b>{esc(grade.get('value', '—'))}</b>"
        )
    if len(grades) > limit:
        lines.append(f"…и ещё {len(grades) - limit}")
    return "\n".join(lines)


def averages_block(averages: list[dict[str, Any]]) -> str:
    if not averages:
        return "Средних баллов пока нет."
    return "\n".join(
        f"• {esc(item.get('subject', '—'))}: "
        f"<b>{float(item.get('average', 0)):.2f}</b> "
        f"({item.get('count', 0)})"
        for item in averages
    )


def summary_block(summary: dict[str, Any]) -> str:
    overall = summary.get("overall_average")
    parts = []
    if overall is not None:
        parts.append(f"Средний балл: <b>{float(overall):.2f}</b>")
    rank = summary.get("class_rank")
    size = summary.get("class_size")
    if rank and size:
        parts.append(f"Место в классе: <b>{rank}</b> из {size}")
    best = summary.get("best_subjects") or []
    weak = summary.get("weak_subjects") or []
    if best:
        parts.append(
            "Сильные предметы: "
            + ", ".join(esc(item.get("subject", "")) for item in best)
        )
    if weak:
        parts.append(
            "Подтянуть: "
            + ", ".join(esc(item.get("subject", "")) for item in weak)
        )
    return "\n".join(parts) or "Данных для сводки пока нет."


def schedule_block(entries: list[dict[str, Any]], weekday: int | None = None) -> str:
    if weekday is None:
        weekday = datetime.now().weekday()
    today = [
        entry for entry in entries if int(entry.get("weekday", -1)) == weekday
    ]
    if not today:
        return f"На {WEEKDAYS[weekday].lower()} уроков нет."
    today.sort(key=lambda entry: entry.get("start_time", ""))
    lines = []
    for entry in today:
        room = f", каб. {esc(entry['room'])}" if entry.get("room") else ""
        lines.append(
            f"• <b>{esc(entry.get('start_time', ''))}–"
            f"{esc(entry.get('end_time', ''))}</b> "
            f"{esc(entry.get('subject', ''))}{room}"
        )
    return "\n".join(lines)


def children_block(children: list[dict[str, Any]]) -> str:
    if not children:
        return "Дети не привязаны к аккаунту."
    return "\n".join(
        f"• {esc(child.get('first_name', ''))} "
        f"{esc(child.get('last_name', ''))} — id {child.get('id')}"
        for child in children
    )
