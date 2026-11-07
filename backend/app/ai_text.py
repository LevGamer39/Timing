"""Чистые текстовые и парсерные помощники для ИИ-ассистента.

В модуле нет побочных эффектов и нет зависимостей кроме стандартной
библиотеки — именно поэтому нечёткое сопоставление задач тестируется без
подмены БД. Доступ к базе и к Ollama остаётся в роутере `app.routers.ai`.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Iterable, Protocol, Sequence
from zoneinfo import ZoneInfo

from app.models import TaskPriority, TaskStatus

# Порог схожести для нечёткого сопоставления по названию. Ниже него
# совпадение отбрасывается, а не угадывается: ошибочно выбранная задача
# хуже, чем просьба уточнить запрос.
MATCH_THRESHOLD = 0.55
# Надбавка, когда нормализованный запрос дословно встречается в названии.
CONTAINS_BONUS = 0.35
# Небольшая надбавка, когда запрос упоминает предмет задачи.
SUBJECT_BONUS = 0.1
# Сколько кандидатов модель может запросить на сопоставление за раз.
MAX_MATCH_QUERIES = 5

WEEKDAY_MAP = {
    "понедельник": 0,
    "вторник": 1,
    "среда": 2,
    "среду": 2,
    "четверг": 3,
    "пятница": 4,
    "пятницу": 4,
    "суббота": 5,
    "субботу": 5,
    "воскресенье": 6,
}

_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b")
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_NON_WORD_RE = re.compile(r"[^a-zа-яё0-9\s]", flags=re.IGNORECASE)


class TaskLike(Protocol):
    """Минимальный набор полей задачи, который нужен сопоставителю."""

    id: int
    title: str
    subject: str | None


# Нормализация текста


def clean_ai_text(text: str | None) -> str:
    """Убирает HTML и markdown, который модель подмешивает в ответ."""
    cleaned = (text or "").strip()
    for tag in ("<br>", "<br/>", "<br />"):
        cleaned = cleaned.replace(tag, "\n")
    cleaned = re.sub(r"</?b>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</?strong>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</?[^>]+>", "", cleaned)
    cleaned = cleaned.replace("&nbsp;", " ")
    cleaned = cleaned.replace("•", "-")
    # Модель любит заголовки и жирный текст, а чат показывает обычный текст.
    cleaned = re.sub(r"^\s{0,3}#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__(.+?)__", r"\1", cleaned)
    cleaned = re.sub(r"^\s*[*+]\s+", "- ", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def normalize_text(text: str) -> str:
    """Нижний регистр, без диакритики и пунктуации — для нечёткого сравнения."""
    normalized = unicodedata.normalize("NFKD", (text or "").lower())
    normalized = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    normalized = _NON_WORD_RE.sub(" ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def extract_json(raw: str) -> dict:
    """Достаёт JSON-объект из ответа модели, возможно обёрнутого в ```."""
    cleaned = (raw or "").strip()
    cleaned = (
        cleaned.removeprefix("```json")
        .removeprefix("```")
        .removesuffix("```")
        .strip()
    )
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    payload = match.group(0) if match else cleaned
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object")
    return data


# Приоритет

_CRITICAL_WORDS = ("срочно", "сегодня", "до вечера", "сейчас")
_HIGH_WORDS = ("экзам", "огэ", "егэ", "контрольн", "олимпиад", "проект", "сдать")
_MEDIUM_WORDS = ("подготов", "домаш", "урок", "презентац", "повторить")


def default_priority_from_text(text: str) -> TaskPriority:
    lower = (text or "").lower()
    if any(word in lower for word in _CRITICAL_WORDS):
        return TaskPriority.critical
    if any(word in lower for word in _HIGH_WORDS):
        return TaskPriority.high
    if any(word in lower for word in _MEDIUM_WORDS):
        return TaskPriority.medium
    return TaskPriority.medium


def parse_priority(
    value: str | None, fallback_text: str = ""
) -> TaskPriority:
    """Явное значение от модели важнее; иначе приоритет выводится из текста."""
    if value:
        try:
            return TaskPriority(value)
        except ValueError:
            pass
    return default_priority_from_text(fallback_text)


# Сроки


def parse_deadline_hint(
    deadline_hint: str | None,
    user_message: str = "",
    tz: ZoneInfo | None = None,
) -> datetime | None:
    """Переводит русскую датировку в datetime по UTC, насколько может.

    Распознаёт «сегодня», «завтра», «послезавтра», названия дней недели и
    ``ДД.ММ[.ГГГГ]``, при желании с ``ЧЧ:ММ``. Всё остальное — и любая
    некорректная дата — даёт ``None``, а не выдуманную дату.

    Дата и время считаются в поясе ``tz`` (поясе устройства ученика): «завтра
    18:00» должно означать 18:00 там, где он сидит, а не 21:00 по Москве.
    Без ``tz`` поведение прежнее — UTC.
    """
    source = f"{deadline_hint or ''} {user_message}".strip().lower()
    if not source:
        return None

    zone = tz or timezone.utc
    now = datetime.now(zone)
    base_date = now.date()

    if "послезавтра" in source:
        base_date = (now + timedelta(days=2)).date()
    elif "завтра" in source:
        base_date = (now + timedelta(days=1)).date()
    elif "сегодня" in source:
        base_date = now.date()
    else:
        match = _DATE_RE.search(source)
        if match:
            parsed = _safe_date(match, now)
            base_date = parsed or base_date
        else:
            for name, number in WEEKDAY_MAP.items():
                if name in source:
                    diff = (number - now.weekday()) % 7
                    base_date = (now + timedelta(days=diff or 7)).date()
                    break

    time_match = _TIME_RE.search(source)
    hour = int(time_match.group(1)) if time_match else 18
    minute = int(time_match.group(2)) if time_match else 0
    local = datetime(
        base_date.year, base_date.month, base_date.day, hour, minute,
        tzinfo=zone,
    )
    return local.astimezone(timezone.utc)


def has_date_hint(text: str) -> bool:
    """Есть ли в тексте хоть какая-то дата: без неё срок не выдумываем."""
    source = (text or "").lower()
    if not source:
        return False
    if any(word in source for word in ("сегодня", "завтра", "послезавтра")):
        return True
    if _DATE_RE.search(source) or _TIME_RE.search(source):
        return True
    return any(name in source for name in WEEKDAY_MAP)


def _safe_date(match: re.Match[str], now: datetime) -> datetime.date | None:
    day, month = int(match.group(1)), int(match.group(2))
    raw_year = match.group(3)
    if not 1 <= day <= 31 or not 1 <= month <= 12:
        return None
    year = int(raw_year) if raw_year else now.year
    if year < 100:
        year += 2000
    try:
        return datetime(year, month, day, tzinfo=timezone.utc).date()
    except ValueError:
        # например, 31.02
        return None


# Сопоставление


def score_task(query_norm: str, task: TaskLike) -> float:
    """Сходство одного нормализованного запроса с одной задачей."""
    title_norm = normalize_text(task.title)
    score = SequenceMatcher(None, query_norm, title_norm).ratio()
    if query_norm and query_norm in title_norm:
        score += CONTAINS_BONUS
    if task.subject and normalize_text(task.subject) in query_norm:
        score += SUBJECT_BONUS
    return score


def match_tasks(
    tasks: Sequence[TaskLike], queries: Iterable[str]
) -> list[TaskLike]:
    """Сопоставляет свободный текст с конкретными задачами.

    Числовой запрос считается id задачи. Иначе каждый запрос выбирает
    задачу с лучшим баллом, если тот превышает ``MATCH_THRESHOLD``.
    Результат без повторов, в порядке запросов.

    Возвращает пустой список, когда сопоставлять нечего: раньше здесь был
    обращение к пустому списку и ``IndexError``, из-за которого безобидное
    «удали мою задачу по математике» превращалось в HTTP 500.
    """
    candidates = list(tasks)
    wanted = [q for q in (queries or []) if normalize_text(q)][:MAX_MATCH_QUERIES]
    if not wanted or not candidates:
        return []

    matched: list[TaskLike] = []
    seen: set[int] = set()

    for query in wanted:
        query_norm = normalize_text(query)
        if not query_norm:
            continue

        if query_norm.isdigit():
            direct = next(
                (t for t in candidates if t.id == int(query_norm)), None
            )
            if direct is not None and direct.id not in seen:
                matched.append(direct)
                seen.add(direct.id)
            continue

        scored = sorted(
            ((score_task(query_norm, task), task) for task in candidates),
            key=lambda item: item[0],
            reverse=True,
        )
        if not scored:
            continue

        best_score, best_task = scored[0]
        if best_score >= MATCH_THRESHOLD and best_task.id not in seen:
            matched.append(best_task)
            seen.add(best_task.id)

    return matched


# Форматирование


def format_deadline(
    deadline: datetime | None, tz: ZoneInfo | None = None
) -> str:
    if not deadline:
        return "без срока"
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline.astimezone(tz or timezone.utc).strftime("%d.%m %H:%M")


def format_priority(priority: TaskPriority) -> str:
    return {
        TaskPriority.low: "обычный",
        TaskPriority.medium: "важный",
        TaskPriority.high: "высокий",
        TaskPriority.critical: "критичный",
    }[priority]


# Расписание на неделю

# Оценка нагрузки: урок и задача считаются одинаково, порог в 7 учебных часов
# соответствует «школьный день + домашняя работа». День помечается перегруженным
# по любому из трёх признаков — так видны и дни без уроков с тремя дедлайнами.
PLAN_MINUTES_PER_LESSON = 45
PLAN_MINUTES_PER_TASK = 45
PLAN_OVERLOAD_MINUTES = 420
PLAN_OVERLOAD_TASKS = 3
PLAN_OVERLOAD_LESSONS = 7
MAX_PLAN_DAYS = 14

# Кружки и доп. занятия узнаём по названию задачи: «кружок», «секция»,
# «репетитор». Худший случай — кружок остался в обычных заданиях: он всё равно
# виден в плане, просто в другом разделе.
ACTIVITY_WORDS = (
    "кружок", "секци", "репетитор", "трениров", "музыкальн", "худож",
    "шахмат", "бассейн", "танц", "спортивн",
)


def looks_like_activity(title: str | None) -> bool:
    source = (title or "").lower()
    return any(word in source for word in ACTIVITY_WORDS)

_WEEKDAY_FULL = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]


def resolve_timezone(
    name: str | None, fallback: str = "Europe/Moscow"
) -> ZoneInfo:
    """Пояс браузера, а если его нет или он битый — пояс из конфигурации."""
    for candidate in (name, fallback, "UTC"):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError и пустые строки
            continue
    return ZoneInfo("UTC")


def _plural(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def _local_date(value: datetime | None, zone) -> date | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(zone).date()


def _overload_reason(lessons: int, tasks: int, minutes: int) -> str | None:
    overloaded = (
        minutes >= PLAN_OVERLOAD_MINUTES
        or tasks >= PLAN_OVERLOAD_TASKS
        or lessons >= PLAN_OVERLOAD_LESSONS
    )
    if not overloaded:
        return None
    parts = []
    if lessons >= PLAN_OVERLOAD_LESSONS:
        parts.append(f"{lessons} {_plural(lessons, 'урок', 'урока', 'уроков')}")
    if tasks >= PLAN_OVERLOAD_TASKS:
        parts.append(
            f"{tasks} {_plural(tasks, 'дедлайн', 'дедлайна', 'дедлайнов')}"
        )
    if not parts:
        hours = max(1, round(minutes / 60))
        parts.append(f"{hours} ч нагрузки")
    return " и ".join(parts)


def build_plan(
    schedule: Sequence,
    tasks: Sequence,
    start_date: date,
    days: int = 7,
    tz: ZoneInfo | None = None,
    activity_ids: Iterable[int] | None = None,
) -> dict:
    """Собирает расписание на несколько дней из уроков и задач.

    Задачи, которые модель признала кружками/доп. занятиями, попадают
    в отдельный список ``activities`` и не считаются дважды. Выполненные
    задачи пропускаются: план — про то, что ещё предстоит.
    """
    zone = tz or timezone.utc
    days = max(1, min(int(days or 7), MAX_PLAN_DAYS))
    activity_ids = set(activity_ids or ())

    by_weekday: dict[int, list] = {}
    for entry in schedule:
        by_weekday.setdefault(entry.weekday, []).append(entry)

    tasks_by_date: dict[date, list] = {}
    undated: list = []
    for task in tasks:
        if getattr(task, "status", None) == TaskStatus.done:
            continue
        key = _local_date(getattr(task, "deadline", None), zone)
        if key is None:
            undated.append(task)
            continue
        tasks_by_date.setdefault(key, []).append(task)

    planned: list[dict] = []
    for offset in range(days):
        day = start_date + timedelta(days=offset)
        lessons = sorted(
            by_weekday.get(day.weekday(), []), key=lambda item: item.start_time
        )
        day_tasks = sorted(
            tasks_by_date.get(day, []),
            key=lambda item: (item.deadline, item.title),
        )
        activities = [t for t in day_tasks if t.id in activity_ids]
        study = [t for t in day_tasks if t.id not in activity_ids]
        minutes = (
            PLAN_MINUTES_PER_LESSON * len(lessons)
            + PLAN_MINUTES_PER_TASK * len(day_tasks)
        )
        reason = _overload_reason(len(lessons), len(day_tasks), minutes)
        planned.append(
            {
                "date": day,
                "weekday": day.weekday(),
                "lessons": lessons,
                "tasks": study,
                "activities": activities,
                "minutes": minutes,
                "overloaded": reason is not None,
                "reason": reason,
            }
        )

    return {"days": planned, "undated": undated}


def _task_line(task, zone) -> str:
    priority = getattr(task, "priority", None)
    try:
        label = format_priority(
            priority if isinstance(priority, TaskPriority)
            else TaskPriority(priority)
        )
    except (ValueError, TypeError):
        label = "важный"
    subject = getattr(task, "subject", None)
    parts = [task.title]
    if subject:
        parts.append(subject)
    parts.append("до " + format_deadline(getattr(task, "deadline", None), zone))
    parts.append(label)
    return " · ".join(parts)


def format_plan(plan: dict, tz: ZoneInfo | None = None) -> str:
    """Текст расписания для чата. Данные никуда не сохраняются."""
    zone = tz or timezone.utc
    days = plan.get("days") or []
    if not days:
        return "На эти дни уроков и задач нет."

    first, last = days[0]["date"], days[-1]["date"]
    lines = [
        f"Расписание на {len(days)} "
        f"{_plural(len(days), 'день', 'дня', 'дней')}: "
        f"{first:%d.%m} – {last:%d.%m}"
    ]

    for day in days:
        head = (
            f"\n{_WEEKDAY_FULL[day['weekday']].capitalize()} "
            f"{day['date']:%d.%m}"
        )
        if day["overloaded"]:
            head += f" — перегруз: {day['reason']}"
        lines.append(head)

        if day["lessons"]:
            lesson_text = "; ".join(
                f"{item.start_time}–{item.end_time} {item.subject}"
                + (f" ({item.room})" if item.room else "")
                for item in day["lessons"]
            )
            lines.append(f"Уроки: {lesson_text}")
        else:
            lines.append("Уроков нет")

        if day["tasks"]:
            lines.append("Задания:")
            lines.extend(f"- {_task_line(task, zone)}" for task in day["tasks"])
        if day["activities"]:
            lines.append("Кружки и доп. занятия:")
            lines.extend(
                f"- {_task_line(task, zone)}" for task in day["activities"]
            )

    if plan.get("undated"):
        lines.append("\nБез срока:")
        lines.extend(
            f"- {_task_line(task, zone)}" for task in plan["undated"][:10]
        )

    return "\n".join(lines)


def detect_action(
    reply: str, user_message: str
) -> tuple[str | None, dict | None]:
    """Ищет момент «сохранить эти шаги?» в ответе чата."""
    lower = (user_message or "").lower()
    wants_steps = any(
        word in lower for word in ("разбей", "раздели", "по шагам", "подзадачи")
    )
    if wants_steps and any(mark in reply for mark in ("1.", "2.", "•", "-")):
        return "create_subtasks", {
            "hint": "Хочешь сохранить эти шаги как подзадачи?"
        }
    return None, None