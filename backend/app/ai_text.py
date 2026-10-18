"""Чистые текстовые и парсерные помощники для ИИ-ассистента.

В модуле нет побочных эффектов и нет зависимостей кроме стандартной
библиотеки — именно поэтому нечёткое сопоставление задач тестируется без
подмены БД. Доступ к базе и к Ollama остаётся в роутере `app.routers.ai`.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Iterable, Protocol, Sequence

from app.models import TaskPriority

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

_WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

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


# ---------------------------------------------------------------------------
# Нормализация текста
# ---------------------------------------------------------------------------


def clean_ai_text(text: str | None) -> str:
    """Убирает HTML, который модель иногда подмешивает в ответ."""
    cleaned = (text or "").strip()
    for tag in ("<br>", "<br/>", "<br />"):
        cleaned = cleaned.replace(tag, "\n")
    cleaned = re.sub(r"</?b>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</?strong>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</?[^>]+>", "", cleaned)
    cleaned = cleaned.replace("&nbsp;", " ")
    cleaned = cleaned.replace("•", "-")
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


# ---------------------------------------------------------------------------
# Приоритет
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Сроки
# ---------------------------------------------------------------------------


def parse_deadline_hint(
    deadline_hint: str | None, user_message: str = ""
) -> datetime | None:
    """Переводит русскую датировку в datetime по UTC, насколько может.

    Распознаёт «сегодня», «завтра», «послезавтра», названия дней недели и
    ``ДД.ММ[.ГГГГ]``, при желании с ``ЧЧ:ММ``. Всё остальное — и любая
    некорректная дата — даёт ``None``, а не выдуманную дату.
    """
    source = f"{deadline_hint or ''} {user_message}".strip().lower()
    if not source:
        return None

    now = datetime.now(timezone.utc)
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
    return datetime(
        base_date.year, base_date.month, base_date.day, hour, minute,
        tzinfo=timezone.utc,
    )


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


# ---------------------------------------------------------------------------
# Сопоставление
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Форматирование
# ---------------------------------------------------------------------------


def format_deadline(deadline: datetime | None) -> str:
    if not deadline:
        return "без срока"
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline.astimezone(timezone.utc).strftime("%d.%m %H:%M")


def format_priority(priority: TaskPriority) -> str:
    return {
        TaskPriority.low: "обычный",
        TaskPriority.medium: "важный",
        TaskPriority.high: "высокий",
        TaskPriority.critical: "критичный",
    }[priority]


def format_task(task: TaskLike, status: str, index: int | None = None) -> str:
    prefix = f"{index}. " if index is not None else "- "
    return (
        f"{prefix}{task.title} "
        f"(id: {task.id}, статус: {status}, приоритет: "
        f"{getattr(task, 'priority', None)}, предмет: "
        f"{getattr(task, 'subject', None) or 'общее'}, дедлайн: "
        f"{format_deadline(getattr(task, 'deadline', None))})"
    )


def describe_schedule(entries: Sequence) -> list[str]:
    """Представляет строки расписания строками для промпта."""
    lines = []
    for item in entries:
        lines.append(
            f"- {_WEEKDAYS[item.weekday]} {item.start_time}-{item.end_time}: "
            f"{item.subject}"
        )
    return lines


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