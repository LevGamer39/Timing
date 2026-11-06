"""Агрегаты по заданиям ученика.

Чистая логика без FastAPI и без ИИ: её используют и роутер статистики,
и почтовая сводка, и бот (через тот же роутер). Всё считается поверх
``tasks`` — отдельной коллекции данных нет.

Считаются только задания учителя (``assignment_id`` заполнен): личные
задачи ученика — его собственный трекер, и в оценку недели они не входят.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import Task, TaskStatus

# Закрыто — значит ученик отметил, дальше это либо принято, либо ждёт приёма.
CLOSED = (TaskStatus.done, TaskStatus.accepted)
# Открыто — срок ещё не истёк или истёк, но никто не отметил.
OPEN = (TaskStatus.pending, TaskStatus.in_progress, TaskStatus.overdue)

# Возраст долга показываем в днях, глубже месяца смысла нет.
MAX_WAITING = 30


def zone(tz_name: str | None) -> ZoneInfo:
    """Часовой пояс школы; неверное имя не должно ронять запрос."""
    try:
        return ZoneInfo(tz_name or "UTC")
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def day_bounds(day: date, tz_name: str | None) -> tuple[datetime, datetime]:
    """UTC-границы местных суток: ``[начало, начало следующих)``."""
    tz = zone(tz_name)
    start = datetime.combine(day, time.min, tzinfo=tz)
    return (
        start.astimezone(timezone.utc),
        (start + timedelta(days=1)).astimezone(timezone.utc),
    )


def week_bounds(day: date) -> tuple[date, date]:
    """Понедельник и воскресенье ISO-недели, в которую попадает ``day``."""
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def _base_query(student_id: int):
    return (
        Task.student_id == student_id,
        Task.parent_task_id.is_(None),
        Task.assignment_id.isnot(None),
        Task.deadline.isnot(None),
    )


def _task_payload(task: Task, tz_name: str | None, now: datetime) -> dict:
    """Строка списка: возраст долга считается в местных днях."""
    tz = zone(tz_name)
    deadline = task.deadline
    local_today = now.astimezone(tz).date()
    age_days = None
    if deadline is not None:
        age_days = (local_today - deadline.astimezone(tz).date()).days
        age_days = max(0, age_days) if age_days >= 0 else None
    return {
        "id": task.id,
        "title": task.title,
        "subject": task.subject,
        "status": task.status,
        "deadline": deadline,
        "age_days": age_days,
    }


def summary(
    db: Session,
    student_id: int,
    start: date,
    end: date,
    tz_name: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Срез по заданиям с дедлайном в ``[start, end]`` включительно."""
    now = now or datetime.now(timezone.utc)
    start_utc, _ = day_bounds(start, tz_name)
    _, end_utc = day_bounds(end, tz_name)
    tasks = (
        db.query(Task)
        .filter(
            *_base_query(student_id),
            Task.deadline >= start_utc,
            Task.deadline < end_utc,
        )
        .order_by(Task.deadline.asc())
        .all()
    )

    in_work = done = accepted = waiting = on_time = late = open_ = 0
    for task in tasks:
        if task.status == TaskStatus.accepted:
            accepted += 1
        elif task.status == TaskStatus.done:
            done += 1
        elif task.status == TaskStatus.overdue:
            waiting += 1
        else:
            in_work += 1

        if task.completed_at is None:
            open_ += 1
        elif task.deadline is not None and task.completed_at > task.deadline:
            late += 1
        else:
            on_time += 1

    return {
        "date_from": start,
        "date_to": end,
        "total": len(tasks),
        "in_work": in_work,
        "done": done,
        "accepted": accepted,
        "waiting": waiting,
        "on_time": on_time,
        "late": late,
        "open": open_,
        # Норма недели — ничего не висит: всё либо отмечено, либо принято.
        "week_ok": bool(tasks) and open_ == 0 and waiting == 0,
        "tasks": [_task_payload(t, tz_name, now) for t in tasks],
    }


def waiting(
    db: Session,
    student_id: int,
    before: date,
    tz_name: str | None = None,
    now: datetime | None = None,
    limit: int = 20,
) -> list[dict]:
    """Задания с истёкшим сроком, которые ещё не приняты.

    Сюда попадают и «никто не отметил», и «ученик отметил, учитель ещё не
    посмотрел»: для родителя это один список «ожидает закрытия».
    """
    now = now or datetime.now(timezone.utc)
    start_utc, _ = day_bounds(before, tz_name)
    tasks = (
        db.query(Task)
        .filter(
            *_base_query(student_id),
            Task.deadline < start_utc,
            Task.deadline >= start_utc - timedelta(days=MAX_WAITING),
            Task.status != TaskStatus.accepted,
        )
        .order_by(Task.deadline.desc())
        .limit(limit)
        .all()
    )
    return [_task_payload(task, tz_name, now) for task in tasks]


def dynamics(
    db: Session,
    student_id: int,
    weeks: int = 8,
    tz_name: str | None = None,
) -> dict:
    """Ряды по неделям и распределение по предметам."""
    today = datetime.now(zone(tz_name)).date()
    first_monday = today - timedelta(days=today.weekday() + 7 * (weeks - 1))
    start_utc, _ = day_bounds(first_monday, tz_name)
    tasks = (
        db.query(Task)
        .filter(*_base_query(student_id), Task.deadline >= start_utc)
        .all()
    )

    buckets: dict[date, dict[str, int]] = {}
    subjects: dict[str, int] = {}
    tz = zone(tz_name)
    for task in tasks:
        monday = task.deadline.astimezone(tz).date()
        monday -= timedelta(days=monday.weekday())
        bucket = buckets.setdefault(
            monday, {"on_time": 0, "late": 0, "open": 0}
        )
        if task.completed_at is None:
            bucket["open"] += 1
        elif task.deadline is not None and task.completed_at > task.deadline:
            bucket["late"] += 1
        else:
            bucket["on_time"] += 1
        subject = (task.subject or "").strip() or "Без предмета"
        subjects[subject] = subjects.get(subject, 0) + 1

    series = []
    for index in range(weeks):
        monday = first_monday + timedelta(days=7 * index)
        bucket = buckets.get(monday, {"on_time": 0, "late": 0, "open": 0})
        series.append({"week_start": monday, **bucket})

    return {
        "weeks": series,
        "subjects": [
            {"subject": name, "count": count}
            for name, count in sorted(
                subjects.items(), key=lambda item: (-item[1], item[0])
            )
        ],
    }
