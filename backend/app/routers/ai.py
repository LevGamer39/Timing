"""Эндпоинты ИИ-ассистента на базе Ollama.

Вся чистая логика парсинга, плана и матчинга вынесена в :mod:`app.ai_text`,
чтобы её можно было покрыть юнит-тестами; этот модуль занимается только
обращением к БД и вызовами модели.

Модель вызывается один раз на сообщение: ответ либо текст, либо вызов
инструмента. Инструменты выполняются здесь детерминированно, поэтому
фантомные действия и двойной запрос к модели (раньше это давало до 85 секунд
и HTTP 500 по таймауту) больше не нужны.
"""
import json
import re
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import config
from app.ai_text import (
    MAX_PLAN_DAYS,
    WEEKDAY_MAP,
    build_plan,
    clean_ai_text,
    detect_action,
    format_deadline,
    format_plan,
    format_priority,
    has_date_hint,
    looks_like_activity,
    match_tasks,
    normalize_text,
    parse_deadline_hint,
    parse_priority,
    resolve_timezone,
)
from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models import (
    AIChat,
    AIMessage,
    Grade,
    NotificationChannel,
    Schedule,
    Task,
    TaskPriority,
    TaskStatus,
    User,
    UserRole,
)
from app.routers.notifications import create_notification

router = APIRouter(prefix="/api/ai", tags=["ai"])

MAX_CONTEXT_GRADES = 40
MAX_LISTED_TASKS = 15
MAX_ALL_TASKS = 40
MAX_CREATED_TASKS = 8
MAX_SUBTASKS = 6
MAX_HISTORY_ROWS = 200
MAX_TASK_TITLE = 255
MAX_TASK_SUBJECT = 100
MAX_TASK_DESCRIPTION = 2000
CONFIRM_CREATE_THRESHOLD = 3
MAX_SUMMARY_SOURCE_CHARS = 12000

WEEKDAY_NAMES = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]

QUICK_PROMPTS = [
    "Составь расписание на неделю",
    "Покажи мои задачи по приоритету",
    "Добавь две задачи: подготовиться к физике и прочитать параграф 12",
    "Какие у меня оценки по алгебре?",
]

SYSTEM_PROMPT = """Ты — «Тайминг», помощник школьника по учёбе и планированию.

В контексте уже есть данные ученика: оценки (средние и несколько последних),
открытые задачи, расписание уроков и загруженность ближайших дней.

Инструменты:
- build_plan — структурированное расписание на несколько дней. Уроки и задачи
  собираешь ты сам; в activity_task_ids передай id задач, которые являются
  кружком, секцией, занятием с репетитором или другим доп. занятием. Бери
  только id из контекста.
- get_subject_grades — все оценки по предмету, если в контексте только
  последние.
- get_all_tasks — все задачи, включая выполненные и подзадачи.
- create_tasks, update_tasks, complete_tasks, delete_tasks — изменения задач.

Правила:
- сначала попробуй ответить из контекста; если данных мало — вызови
  инструмент, а не проси ученика прислать их;
- любое изменение задач (создать, удалить, изменить, отметить) выполняется
  ТОЛЬКО через инструмент. Никогда не пиши, что задача добавлена или
  удалена, если ты не вызвал инструмент;
- не выдумывай задачи, оценки, даты и id;
- на просьбы «составь расписание», «что у меня на неделе», «где перегруз»
  вызывай build_plan;
- после изменения задач коротко скажи, что именно сделал;
- не решай домашние задания за ученика и не пиши готовые сочинения;
- отвечай по-русски, обычным текстом, коротко и по делу, без HTML и
  markdown-таблиц."""

SUMMARY_PROMPT = """Сожми диалог ученика с планировщиком в короткое резюме.
Сохрани: о чём спрашивал ученик, какие задачи были созданы или изменены
(названия и id), какие выводы и договорённости есть. Без воды, до 300 слов,
по-русски, обычным текстом."""

KNOWN_TOOLS = {
    "create_tasks", "get_all_tasks", "update_tasks", "complete_tasks",
    "delete_tasks", "build_plan", "get_subject_grades",
}

# Модель иногда отвечает «добавил задачи», не вызвав инструмент. Такой ответ
# хуже ошибки: ученик считает, что задача есть. Просьба о действии и заявление
# о нём ловятся здесь, чтобы сделать один повторный запрос.
ACTION_REQUEST_RE = re.compile(
    r"(добав|созда|завед|удали|убери|отметь|выполн|измен|перенес|обнови)",
    re.IGNORECASE,
)
ACTION_CLAIM_RE = re.compile(
    r"(добавил|добавлен|создал|создан|удалил|удал[её]н|отметил|отмечен|"
    r"изменил|измен[её]н|перенёс|перенес[её]н|обновил|обновл[её]н|готово)",
    re.IGNORECASE,
)
ACTION_NUDGE = (
    "Ты ответил текстом, но не вызвал инструмент. Вызови нужный инструмент "
    "и выполни просьбу. Не пиши, что действие выполнено, без вызова."
)

# Расписание и нагрузку считаем сами: маленькая модель путает дни недели и
# выдумывает время уроков, а данные для плана и так лежат в БД.
PLAN_REQUEST_RE = re.compile(r"(расписан|перегруз|нагрузк|урок)", re.IGNORECASE)


def _plan_request_days(message: str) -> int | None:
    """Сколько дней просили, если это просьба про расписание или нагрузку."""
    if not PLAN_REQUEST_RE.search(message):
        return None
    lowered = message.lower()
    if any(word in lowered for word in ("завтра", "сегодня", *WEEKDAY_MAP)):
        return 1
    return config.AI_CONTEXT_DAYS
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "build_plan",
            "description": (
                "Собрать расписание на несколько дней: уроки по дням, задания "
                "и дедлайны, кружки и доп. занятия, перегруженные дни."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "На сколько дней вперёд, 1-14",
                    },
                    "activity_task_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": (
                            "id задач, которые являются кружком, секцией или "
                            "доп. занятием (не домашняя работа: параграфы, "
                            "упражнения и подготовка к контрольной — это учёба)"
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_subject_grades",
            "description": "Все оценки ученика по одному предмету.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                },
                "required": ["subject"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_all_tasks",
            "description": (
                "Все задачи ученика, включая выполненные, просроченные и "
                "подзадачи."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "include_done": {"type": "boolean"},
                    "status": {"type": "string"},
                    "subject": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_tasks",
            "description": "Создать личные задачи ученика.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "subject": {"type": "string"},
                                "description": {"type": "string"},
                                "deadline_hint": {"type": "string"},
                                "priority": {
                                    "type": "string",
                                    "enum": [
                                        "low", "medium", "high", "critical",
                                    ],
                                },
                            },
                            "required": ["title"],
                        },
                    },
                },
                "required": ["tasks"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_tasks",
            "description": "Изменить личные задачи: название, предмет, срок, приоритет, статус.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_queries": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "updates": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "subject": {"type": "string"},
                            "description": {"type": "string"},
                            "deadline_hint": {"type": "string"},
                            "priority": {"type": "string"},
                            "status": {"type": "string"},
                        },
                    },
                },
                "required": ["task_queries"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_tasks",
            "description": "Отметить задачи выполненными.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_queries": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["task_queries"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_tasks",
            "description": "Удалить личные задачи ученика.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_queries": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["task_queries"],
            },
        },
    },
]


# Схемы


class ChatRequest(BaseModel):
    message: str
    chat_id: int | None = None
    new_chat: bool = False
    # IANA-пояс устройства: «завтра 18:00» значит 18:00 там, где ученик.
    timezone: str | None = None

    @field_validator("message")
    @classmethod
    def _message_length(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message must not be blank")
        if len(value) > 4000:
            raise ValueError("Message must be <= 4000 characters")
        return value


class ChatResponse(BaseModel):
    reply: str
    action: str | None = None
    data: dict | None = None
    chat_id: int | None = None
    title: str | None = None


class MessageResponse(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


class ChatInfo(BaseModel):
    id: int
    title: str
    message_count: int = 0
    updated_at: datetime | None = None


class ActionRequest(BaseModel):
    operation: str
    task_ids: list[int] = []
    tasks: list[dict] = []
    chat_id: int | None = None
    timezone: str | None = None


# Хелперы для БД


def _base_tasks_query(student_id: int):
    """Только верхнеуровневые задачи: подзадачи ИИ отдельными не считаем."""
    return (
        Task.student_id == student_id,
        Task.parent_task_id.is_(None),
    )


def _filter_tasks(
    student_id: int,
    db: Session,
    status: str | None = None,
    subject: str | None = None,
) -> list[Task]:
    owner, top_level = _base_tasks_query(student_id)
    query = db.query(Task).filter(owner, top_level)
    if status:
        try:
            query = query.filter(Task.status == TaskStatus(status))
        except ValueError:
            pass
    if subject:
        query = query.filter(Task.subject.ilike(f"%{subject.strip()}%"))
    return query.order_by(
        Task.deadline.asc().nullslast(), Task.created_at.desc()
    ).all()


def _match_tasks(student_id: int, queries: list[str], db: Session) -> list[Task]:
    return match_tasks(_filter_tasks(student_id, db), queries)


def _open_tasks(student_id: int, db: Session, limit: int) -> list[Task]:
    owner, top_level = _base_tasks_query(student_id)
    return (
        db.query(Task)
        .filter(owner, top_level, Task.status != TaskStatus.done)
        .order_by(Task.deadline.asc().nullslast(), Task.id.asc())
        .limit(limit)
        .all()
    )


def _short_date(value: datetime | None, tz) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(tz).strftime("%d.%m")


def _priority_label(priority) -> str:
    try:
        return format_priority(
            priority if isinstance(priority, TaskPriority)
            else TaskPriority(priority)
        )
    except (ValueError, TypeError):
        return "важный"


def _build_context(user: User, db: Session, tz) -> str:
    """Текущее состояние ученика: оценки, задачи, уроки, загруженность."""
    now = datetime.now(tz)
    parts = [
        f"Сегодня: {WEEKDAY_NAMES[now.weekday()]}, "
        f"{now.strftime('%d.%m.%Y %H:%M')} ({tz})",
        f"Ученик: {user.first_name} {user.last_name}, "
        f"класс {user.grade or 'не указан'}",
    ]

    grades = (
        db.query(Grade)
        .filter(Grade.student_id == user.id)
        .order_by(Grade.graded_at.desc(), Grade.id.desc())
        .all()
    )
    if grades:
        by_subject: dict[str, list[Grade]] = {}
        for grade in grades:
            by_subject.setdefault(grade.subject, []).append(grade)
        overall = sum(g.value for g in grades) / len(grades)
        parts.append(
            f"\nОценки (всего {len(grades)}, средний {overall:.2f}):"
        )
        for subject in sorted(by_subject):
            items = by_subject[subject]
            average = sum(g.value for g in items) / len(items)
            last = items[: max(1, config.AI_CONTEXT_GRADES)]
            shown = ", ".join(
                f"{g.value:g} ({_short_date(g.graded_at, tz)})" for g in last
            )
            parts.append(
                f"- {subject}: средний {average:.2f}; последние: {shown}"
            )
    else:
        parts.append("\nОценок пока нет.")

    tasks = _open_tasks(user.id, db, config.AI_CONTEXT_TASKS)
    if tasks:
        parts.append(f"\nОткрытые задачи ({len(tasks)}):")
        for task in tasks:
            overdue = (
                task.deadline is not None
                and task.deadline.astimezone(timezone.utc) < now.astimezone(
                    timezone.utc
                )
            )
            parts.append(
                f"- id={task.id}; «{task.title}»; "
                f"предмет={task.subject or 'общее'}; "
                f"срок={format_deadline(task.deadline, tz)}; "
                f"приоритет={_priority_label(task.priority)}; "
                f"{'от учителя' if task.assignment_id else 'личная'}"
                f"{'; просрочено' if overdue else ''}"
            )
    else:
        parts.append("\nОткрытых задач нет.")

    schedule = (
        db.query(Schedule)
        .filter(Schedule.student_id == user.id)
        .order_by(Schedule.weekday, Schedule.start_time)
        .all()
    )
    if schedule:
        parts.append("\nРасписание уроков:")
        by_day: dict[int, list] = {}
        for entry in schedule:
            by_day.setdefault(entry.weekday, []).append(entry)
        for weekday in sorted(by_day):
            times = ", ".join(
                f"{e.start_time}-{e.end_time} {e.subject}"
                for e in by_day[weekday]
            )
            parts.append(f"- {WEEKDAY_NAMES[weekday]}: {times}")

    plan = build_plan(
        schedule, tasks, now.date(), config.AI_CONTEXT_DAYS, tz
    )
    if plan["days"]:
        parts.append(
            f"\nЗагруженность на {len(plan['days'])} дней:"
        )
        for day in plan["days"]:
            line = (
                f"- {WEEKDAY_NAMES[day['weekday']][:2]} "
                f"{day['date']:%d.%m}: {len(day['lessons'])} уроков, "
                f"{len(day['tasks']) + len(day['activities'])} дедлайнов"
            )
            if day["overloaded"]:
                line += f" — перегруз: {day['reason']}"
            parts.append(line)

    return "\n".join(parts)


# Чаты


def _find_chat(user: User, db: Session, chat_id: int) -> AIChat:
    chat = (
        db.query(AIChat)
        .filter(AIChat.id == chat_id, AIChat.user_id == user.id)
        .first()
    )
    if not chat:
        raise HTTPException(404, "Chat not found")
    return chat


def _latest_chat(user: User, db: Session) -> AIChat | None:
    return (
        db.query(AIChat)
        .filter(AIChat.user_id == user.id)
        .order_by(AIChat.updated_at.desc(), AIChat.id.desc())
        .first()
    )


def _resolve_chat(
    user: User, db: Session, chat_id: int | None, new_chat: bool, message: str
) -> AIChat:
    """Чат запроса: явный, новый или последний (совместимо с ботом)."""
    if chat_id is not None:
        return _find_chat(user, db, chat_id)
    if not new_chat:
        existing = _latest_chat(user, db)
        if existing:
            return existing
    title = (message.strip() or "Новый чат")[: config.AI_CHAT_TITLE_CHARS]
    chat = AIChat(user_id=user.id, title=title)
    db.add(chat)
    db.flush()
    return chat


def _chat_messages(chat: AIChat) -> list[AIMessage]:
    return list(chat.messages)


def _store_ai_exchange(
    chat: AIChat, user_message: str, reply: str, db: Session
) -> None:
    db.add(
        AIMessage(
            user_id=chat.user_id, chat_id=chat.id,
            role="user", content=user_message,
        )
    )
    db.add(
        AIMessage(
            user_id=chat.user_id, chat_id=chat.id,
            role="assistant", content=reply,
        )
    )
    chat.updated_at = datetime.now(timezone.utc)
    db.commit()


def _prompt_history(chat: AIChat) -> list[dict]:
    """Сообщения после последнего сжатия, обрезанные по бюджету символов."""
    cutoff = chat.summary_upto_id or 0
    rows = [m for m in _chat_messages(chat) if m.id > cutoff]
    budget = config.AI_HISTORY_CHARS
    selected: list[AIMessage] = []
    used = 0
    for message in reversed(rows):
        size = len(message.content or "")
        if selected and used + size > budget:
            break
        selected.append(message)
        used += size
    return [
        {"role": m.role, "content": m.content} for m in reversed(selected)
    ]


async def _compact_chat(chat: AIChat, db: Session) -> None:
    """Сжимает старую часть диалога в ``chat.summary``.

    Сжатие — единственный дополнительный вызов модели, и он происходит
    только когда история перестаёт влезать в бюджет. Сбой сжатия не ломает
    чат: старый ``summary`` остаётся, а в промпт идёт свежий кусок.
    """
    keep = max(1, config.AI_COMPACT_KEEP)
    cutoff = chat.summary_upto_id or 0
    older = [m for m in _chat_messages(chat) if m.id > cutoff]
    candidates = older[:-keep] if len(older) > keep else []
    if not candidates:
        return

    included: list[AIMessage] = []
    used = 0
    for message in candidates:
        size = len(message.content or "")
        if included and used + size > MAX_SUMMARY_SOURCE_CHARS:
            break
        included.append(message)
        used += size
    if not included:
        return

    transcript = "\n".join(
        f"{'ученик' if m.role == 'user' else 'ассистент'}: {m.content}"
        for m in included
    )
    request = [
        {"role": "system", "content": SUMMARY_PROMPT},
        {
            "role": "user",
            "content": (
                f"Предыдущее резюме:\n{chat.summary or 'нет'}\n\n"
                f"Новая часть диалога:\n{transcript}"
            ),
        },
    ]
    try:
        message = await _call_ollama(
            request, options={"temperature": 0.2, "num_predict": 400}
        )
    except HTTPException:
        return

    summary = clean_ai_text(message.get("content"))
    if not summary:
        return
    chat.summary = summary[: config.AI_SUMMARY_CHARS]
    chat.summary_upto_id = included[-1].id
    db.commit()


async def _ensure_history_budget(chat: AIChat, db: Session) -> None:
    total = sum(len(m.content or "") for m in _chat_messages(chat))
    if total <= config.AI_HISTORY_CHARS:
        return
    await _compact_chat(chat, db)


# Ollama


async def _call_ollama(
    messages: list[dict],
    tools: list[dict] | None = None,
    options: dict | None = None,
) -> dict:
    """Возвращает ``message`` ответа Ollama: текст и/или вызовы инструментов."""
    if not config.OLLAMA_URL or not config.OLLAMA_MODEL:
        raise HTTPException(503, "AI service is not configured")

    merged_options = {
        "num_ctx": config.AI_NUM_CTX,
        "temperature": config.AI_TEMPERATURE,
        "num_predict": config.AI_NUM_PREDICT,
    }
    merged_options.update(options or {})
    # Ollama принимает keep_alive числом (-1 — не выгружать); строкой
    # длительности ("30m") тоже, но голый "-1" строкой отвергает.
    keep_alive = config.AI_KEEP_ALIVE
    if str(keep_alive).lstrip("-").isdigit():
        keep_alive = int(keep_alive)  # type: ignore[assignment]
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "keep_alive": keep_alive,
        "options": merged_options,
    }
    if tools:
        payload["tools"] = tools

    try:
        async with httpx.AsyncClient(timeout=config.AI_TIMEOUT) as client:
            response = await client.post(
                f"{config.OLLAMA_URL}/api/chat", json=payload
            )
            response.raise_for_status()
            return response.json().get("message") or {}
    except httpx.HTTPStatusError as exc:
        # Модель без поддержки tools: повторяем как обычный чат, без действий.
        if tools and exc.response.status_code == 400:
            return await _call_ollama(
                messages, tools=None, options=options
            )
        raise HTTPException(503, "AI service unavailable") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "AI service timed out") from exc
    except httpx.ConnectError as exc:
        raise HTTPException(503, "AI service unavailable") from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, "AI service error") from exc


# Обработчики инструментов


def _build_create_payloads(
    message: str, parsed_tasks: list[dict], tz
) -> list[dict]:
    payloads: list[dict] = []
    for item in (parsed_tasks or [])[:MAX_CREATED_TASKS]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip(" .,:;-")[:MAX_TASK_TITLE]
        if not title:
            continue
        hint = str(item.get("deadline_hint") or "").strip()
        payloads.append(
            {
                "title": title,
                "subject": (
                    str(item.get("subject") or "").strip()[:MAX_TASK_SUBJECT]
                    or None
                ),
                "description": (
                    str(item.get("description") or "").strip()[
                        :MAX_TASK_DESCRIPTION
                    ]
                    or None
                ),
                # Без явной даты не ставим срок «сегодня 18:00» наугад.
                "deadline": (
                    parse_deadline_hint(hint, message, tz)
                    if hint or has_date_hint(message)
                    else None
                ),
                "priority": parse_priority(
                    item.get("priority"), f"{title} {message}"
                ),
            }
        )
    return payloads


def _existing_keys(user_id: int, db: Session) -> set[str]:
    """Ключи открытых задач для защиты от повторного создания."""
    rows = (
        db.query(Task)
        .filter(Task.student_id == user_id, Task.status != TaskStatus.done)
        .all()
    )
    keys = set()
    for task in rows:
        day = (
            task.deadline.astimezone(timezone.utc).date().isoformat()
            if task.deadline
            else ""
        )
        keys.add(f"{normalize_text(task.title)}|{day}")
    return keys


def _insert_tasks(user: User, db: Session, payloads: list[dict]) -> list[Task]:
    seen = _existing_keys(user.id, db)
    created: list[Task] = []
    for payload in payloads:
        day = (
            payload["deadline"].astimezone(timezone.utc).date().isoformat()
            if payload.get("deadline")
            else ""
        )
        key = f"{normalize_text(payload['title'])}|{day}"
        if key in seen:
            continue
        seen.add(key)
        task = Task(
            student_id=user.id,
            title=payload["title"],
            subject=payload.get("subject"),
            description=payload.get("description"),
            deadline=payload.get("deadline"),
            priority=payload.get("priority") or TaskPriority.medium,
            is_personal=True,
            status=TaskStatus.pending,
        )
        db.add(task)
        db.flush()
        created.append(task)
    if created:
        create_notification(
            db=db,
            user_id=user.id,
            title="ИИ добавил задачи",
            body=f"Добавлено задач: {len(created)}",
            channel=NotificationChannel.browser,
            commit=False,
        )
    return created


def _pending_payload(payloads: list[dict]) -> dict:
    return {
        "operation": "create",
        "tasks": [
            {
                "title": item["title"],
                "subject": item.get("subject"),
                "description": item.get("description"),
                "deadline": (
                    item["deadline"].isoformat()
                    if item.get("deadline")
                    else None
                ),
                "priority": (
                    item["priority"].value
                    if isinstance(item.get("priority"), TaskPriority)
                    else item.get("priority")
                ),
            }
            for item in payloads
        ],
        "titles": [item["title"] for item in payloads],
    }


def _create_tasks_response(
    user: User, db: Session, args: dict, tz, message: str
) -> ChatResponse:
    payloads = _build_create_payloads(
        message, args.get("tasks") or [], tz
    )
    if not payloads:
        return ChatResponse(
            reply=(
                "Не смог понять, какие именно задачи нужно добавить. "
                "Напиши их чуть конкретнее."
            )
        )

    if len(payloads) >= CONFIRM_CREATE_THRESHOLD:
        lines = ["Добавить эти задачи?"]
        lines.extend(
            f"{index}. {item['title']} — "
            f"{format_deadline(item.get('deadline'), tz)}"
            for index, item in enumerate(payloads, start=1)
        )
        return ChatResponse(
            reply="\n".join(lines),
            action="confirm_create",
            data={"pending": _pending_payload(payloads)},
        )

    created = _insert_tasks(user, db, payloads)
    if not created:
        return ChatResponse(
            reply="Такие задачи уже есть — дубликаты не добавлял."
        )
    reply = "Добавил задачи:\n" + "\n".join(
        f"- {task.title} — {format_deadline(task.deadline, tz)}, "
        f"приоритет: {format_priority(task.priority)}"
        for task in created
    )
    return ChatResponse(
        reply=reply,
        action="task_created",
        data={"count": len(created), "titles": [t.title for t in created]},
    )


def _delete_tasks_response(
    user: User, db: Session, args: dict
) -> ChatResponse:
    matched = _match_tasks(
        user.id, args.get("task_queries") or [], db
    )
    personal = [task for task in matched if task.is_personal]
    if not personal:
        return ChatResponse(
            reply=(
                "Не нашел личные задачи для удаления. "
                "Попробуй указать название точнее."
            )
        )
    titles = [task.title for task in personal]
    return ChatResponse(
        reply="Удалить эти задачи?\n" + "\n".join(f"- {t}" for t in titles),
        action="confirm_delete",
        data={
            "pending": {
                "operation": "delete",
                "task_ids": [task.id for task in personal],
                "titles": titles,
            }
        },
    )


def _complete_tasks_response(
    user: User, db: Session, args: dict
) -> ChatResponse:
    matched = _match_tasks(
        user.id, args.get("task_queries") or [], db
    )
    if not matched:
        return ChatResponse(
            reply="Не нашел задачи, которые нужно отметить выполненными."
        )
    now = datetime.now(timezone.utc)
    for task in matched:
        task.status = TaskStatus.done
        task.completed_at = now
    titles = [task.title for task in matched]
    return ChatResponse(
        reply="Отметил выполненными:\n" + "\n".join(f"- {t}" for t in titles),
        action="task_completed",
        data={"count": len(titles), "titles": titles},
    )


def _update_tasks_response(
    user: User, db: Session, args: dict, tz, message: str
) -> ChatResponse:
    matched = _match_tasks(
        user.id, args.get("task_queries") or [], db
    )
    if not matched:
        return ChatResponse(
            reply="Не нашел задачи для изменения. Уточни название."
        )
    tasks = [task for task in matched if task.is_personal]
    if not tasks:
        return ChatResponse(
            reply="Это задание от учителя — изменить его нельзя."
        )

    updates = args.get("updates") or {}
    changed: list[str] = []

    def note(label: str) -> None:
        if label not in changed:
            changed.append(label)

    deadline = (
        parse_deadline_hint(
            updates.get("deadline_hint"), message or "", tz
        )
        if updates.get("deadline_hint")
        else None
    )
    priority = (
        parse_priority(updates.get("priority"), message or "")
        if updates.get("priority")
        else None
    )
    status = None
    if updates.get("status"):
        try:
            status = TaskStatus(updates["status"])
        except ValueError:
            status = None

    for task in tasks:
        if updates.get("title"):
            task.title = updates["title"].strip()[:MAX_TASK_TITLE]
            note("название")
        if updates.get("subject"):
            task.subject = updates["subject"].strip()[:MAX_TASK_SUBJECT]
            note("предмет")
        if updates.get("description"):
            task.description = (
                updates["description"].strip()[:MAX_TASK_DESCRIPTION]
            )
            note("описание")
        if deadline:
            task.deadline = deadline
            note("срок")
        if priority:
            task.priority = priority
            note("приоритет")
        if status:
            task.status = status
            if status == TaskStatus.done and task.completed_at is None:
                task.completed_at = datetime.now(timezone.utc)
            elif status != TaskStatus.done:
                task.completed_at = None
            note("статус")

    if not changed:
        return ChatResponse(
            reply=(
                "Я понял, какую задачу ты имеешь в виду, "
                "но не увидел, что именно нужно поменять."
            )
        )
    reply = (
        f"Обновил {len(tasks)} задач. Изменил: {', '.join(changed)}.\n"
        + "\n".join(f"- {task.title}" for task in tasks)
    )
    return ChatResponse(
        reply=reply,
        action="task_updated",
        data={"count": len(tasks), "fields": changed},
    )


def _get_all_tasks_response(
    user: User, db: Session, args: dict, tz
) -> ChatResponse:
    status = (args.get("status") or "").strip() or None
    subject = (args.get("subject") or "").strip() or None
    include_done = args.get("include_done")
    include_done = True if include_done is None else bool(include_done)

    query = db.query(Task).filter(Task.student_id == user.id)
    if not include_done:
        query = query.filter(Task.status != TaskStatus.done)
    if status:
        try:
            query = query.filter(Task.status == TaskStatus(status))
        except ValueError:
            pass
    if subject:
        query = query.filter(Task.subject.ilike(f"%{subject}%"))
    tasks = query.order_by(
        Task.deadline.asc().nullslast(), Task.id.desc()
    ).all()

    if not tasks:
        return ChatResponse(reply="Задач с такими условиями нет.")

    lines = [f"Задачи ({len(tasks)}):"]
    for task in tasks[:MAX_ALL_TASKS]:
        step = " (шаг)" if task.parent_task_id else ""
        lines.append(
            f"- id={task.id}; «{task.title}»{step}; "
            f"статус={task.status.value}; "
            f"предмет={task.subject or 'общее'}; "
            f"срок={format_deadline(task.deadline, tz)}; "
            f"приоритет={_priority_label(task.priority)}"
        )
    if len(tasks) > MAX_ALL_TASKS:
        lines.append(f"И еще {len(tasks) - MAX_ALL_TASKS} задач.")
    return ChatResponse(
        reply="\n".join(lines),
        action="tasks_listed",
        data={"count": len(tasks)},
    )


def _get_subject_grades_response(
    user: User, db: Session, args: dict, tz
) -> ChatResponse:
    subject = (args.get("subject") or "").strip()
    if not subject:
        return ChatResponse(
            reply="Уточни предмет — по какому смотреть оценки?"
        )
    grades = (
        db.query(Grade)
        .filter(
            Grade.student_id == user.id,
            Grade.subject.ilike(f"%{subject}%"),
        )
        .order_by(Grade.graded_at.desc(), Grade.id.desc())
        .limit(MAX_CONTEXT_GRADES)
        .all()
    )
    if not grades:
        return ChatResponse(reply=f"По предмету «{subject}» оценок нет.")

    average = sum(g.value for g in grades) / len(grades)
    lines = [
        f"{grades[0].subject}: оценок {len(grades)}, средний {average:.2f}"
    ]
    for grade in grades:
        comment = f" — {grade.comment}" if grade.comment else ""
        lines.append(
            f"- {grade.value:g} от {_short_date(grade.graded_at, tz)}{comment}"
        )
    return ChatResponse(
        reply="\n".join(lines),
        action="grades_listed",
        data={"count": len(grades), "average": round(average, 2)},
    )


def _build_plan_response(
    user: User, db: Session, args: dict, tz
) -> ChatResponse:
    try:
        days = int(args.get("days") or config.AI_CONTEXT_DAYS)
    except (TypeError, ValueError):
        days = config.AI_CONTEXT_DAYS
    days = max(1, min(days, MAX_PLAN_DAYS))

    schedule = (
        db.query(Schedule)
        .filter(Schedule.student_id == user.id)
        .order_by(Schedule.weekday, Schedule.start_time)
        .all()
    )
    tasks = _open_tasks(user.id, db, config.AI_CONTEXT_TASKS)

    activity_ids = {task.id for task in tasks if looks_like_activity(task.title)}
    for raw in args.get("activity_task_ids") or []:
        try:
            activity_ids.add(int(raw))
        except (TypeError, ValueError):
            continue
    plan = build_plan(
        schedule, tasks, datetime.now(tz).date(), days, tz, activity_ids
    )
    reply = format_plan(plan, tz)
    return ChatResponse(
        reply=reply,
        action="plan",
        data={
            "days": days,
            "overloaded": [
                day["date"].isoformat()
                for day in plan["days"]
                if day["overloaded"]
            ],
        },
    )


def _run_tool(
    call: dict, user: User, db: Session, tz, message: str
) -> ChatResponse | None:
    function = call.get("function") or {}
    name = function.get("name")
    if name not in KNOWN_TOOLS:
        return None
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}

    if name == "build_plan":
        return _build_plan_response(user, db, arguments, tz)
    if name == "get_subject_grades":
        return _get_subject_grades_response(user, db, arguments, tz)
    if name == "get_all_tasks":
        return _get_all_tasks_response(user, db, arguments, tz)
    if name == "create_tasks":
        return _create_tasks_response(user, db, arguments, tz, message)
    if name == "update_tasks":
        return _update_tasks_response(user, db, arguments, tz, message)
    if name == "complete_tasks":
        return _complete_tasks_response(user, db, arguments)
    if name == "delete_tasks":
        return _delete_tasks_response(user, db, arguments)
    return None


# Роуты


@router.get("/quick-prompts")
def quick_prompts(current_user: User = Depends(get_current_user)):
    return {"prompts": QUICK_PROMPTS}


@router.get("/chats", response_model=list[ChatInfo])
def list_chats(
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    chats = (
        db.query(AIChat)
        .filter(AIChat.user_id == current_user.id)
        .order_by(AIChat.updated_at.desc(), AIChat.id.desc())
        .all()
    )
    counts = dict(
        db.query(AIMessage.chat_id, func.count(AIMessage.id))
        .filter(AIMessage.user_id == current_user.id)
        .group_by(AIMessage.chat_id)
        .all()
    )
    return [
        ChatInfo(
            id=chat.id,
            title=chat.title,
            message_count=counts.get(chat.id, 0),
            updated_at=chat.updated_at,
        )
        for chat in chats
    ]


@router.get("/history", response_model=list[MessageResponse])
def get_history(
    chat_id: int | None = None,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    """Сообщения чата в хронологическом порядке.

    ``chat_id`` не передан — отдаётся последний чат, как до появления
    нескольких чатов.
    """
    chat = (
        _find_chat(current_user, db, chat_id)
        if chat_id is not None
        else _latest_chat(current_user, db)
    )
    if not chat:
        return []
    rows = (
        db.query(AIMessage)
        .filter(AIMessage.chat_id == chat.id)
        .order_by(AIMessage.id.desc())
        .limit(MAX_HISTORY_ROWS)
        .all()
    )
    return list(reversed(rows))


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    tz = resolve_timezone(body.timezone, config.TIMEZONE)
    chat_row = _resolve_chat(
        current_user, db, body.chat_id, body.new_chat, body.message
    )
    await _ensure_history_budget(chat_row, db)

    plan_days = _plan_request_days(body.message)
    if plan_days:
        plan_reply = _build_plan_response(
            current_user, db, {"days": plan_days}, tz
        )
        _store_ai_exchange(chat_row, body.message, plan_reply.reply, db)
        return ChatResponse(
            reply=plan_reply.reply,
            action=plan_reply.action,
            data=plan_reply.data,
            chat_id=chat_row.id,
            title=chat_row.title,
        )

    system_parts = [SYSTEM_PROMPT]
    if config.AI_PROMPT_EXTRA:
        system_parts.append(config.AI_PROMPT_EXTRA)
    system_parts.append(f"Контекст ученика:\n{_build_context(current_user, db, tz)}")
    if chat_row.summary:
        system_parts.append(
            "Краткое содержание предыдущей части диалога:\n"
            f"{chat_row.summary}"
        )

    messages = (
        [{"role": "system", "content": "\n\n".join(system_parts)}]
        + _prompt_history(chat_row)
        + [{"role": "user", "content": body.message}]
    )

    message = await _call_ollama(messages, tools=TOOLS)
    retried_without_tool = False
    if (
        not (message.get("tool_calls") or [])
        and ACTION_REQUEST_RE.search(body.message)
        and ACTION_CLAIM_RE.search(message.get("content") or "")
    ):
        message = await _call_ollama(
            messages
            + [
                {"role": "assistant", "content": message.get("content") or ""},
                {"role": "user", "content": ACTION_NUDGE},
            ],
            tools=TOOLS,
        )
        retried_without_tool = not (message.get("tool_calls") or [])

    tool_calls = message.get("tool_calls") or []
    action = data = None

    if tool_calls:
        replies: list[str] = []
        for call in tool_calls:
            response = _run_tool(call, current_user, db, tz, body.message)
            if response is None:
                continue
            replies.append(response.reply)
            action = response.action or action
            data = response.data or data
        reply = "\n\n".join(replies) or clean_ai_text(
            message.get("content")
        )
    else:
        reply = clean_ai_text(message.get("content"))
        action, data = detect_action(reply, body.message)

    if not reply:
        reply = "Не смог сформулировать ответ. Попробуй переформулировать вопрос."
    elif retried_without_tool and ACTION_CLAIM_RE.search(reply):
        reply += "\n\nИзменения не применились — попробуй попросить ещё раз."

    _store_ai_exchange(chat_row, body.message, reply, db)
    return ChatResponse(
        reply=reply,
        action=action,
        data=data,
        chat_id=chat_row.id,
        title=chat_row.title,
    )


def _sanitize_pending_task(item: dict) -> dict | None:
    title = str(item.get("title") or "").strip()[:MAX_TASK_TITLE]
    if not title:
        return None
    subject = str(item.get("subject") or "").strip()[:MAX_TASK_SUBJECT]
    description = str(item.get("description") or "").strip()[
        :MAX_TASK_DESCRIPTION
    ]
    deadline = None
    raw_deadline = item.get("deadline")
    if isinstance(raw_deadline, str) and raw_deadline:
        try:
            deadline = datetime.fromisoformat(raw_deadline)
        except ValueError:
            deadline = None
        if deadline is not None and deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
    try:
        priority = TaskPriority(item.get("priority"))
    except (ValueError, TypeError):
        priority = TaskPriority.medium
    return {
        "title": title,
        "subject": subject or None,
        "description": description or None,
        "deadline": deadline,
        "priority": priority,
    }


@router.post("/actions")
def apply_action(
    body: ActionRequest,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    """Подтверждение действий, которые ИИ не выполняет без согласия."""
    operation = (body.operation or "").strip().lower()
    if operation not in {"delete", "create"}:
        raise HTTPException(400, "Unknown operation")
    tz = resolve_timezone(body.timezone, config.TIMEZONE)

    if operation == "delete":
        ids = [int(value) for value in body.task_ids][:50]
        tasks = (
            db.query(Task)
            .filter(
                Task.student_id == current_user.id,
                Task.is_personal.is_(True),
                Task.id.in_(ids),
            )
            .all()
        )
        titles = [task.title for task in tasks]
        for task in tasks:
            db.delete(task)
        reply = (
            "Удалил задачи:\n" + "\n".join(f"- {t}" for t in titles)
            if titles
            else "Не нашел личные задачи для удаления."
        )
        action = "task_deleted"
        data = {"count": len(titles), "titles": titles}
    else:
        payloads = []
        for item in body.tasks[:MAX_CREATED_TASKS]:
            if not isinstance(item, dict):
                continue
            sanitized = _sanitize_pending_task(item)
            if sanitized:
                payloads.append(sanitized)
        created = _insert_tasks(current_user, db, payloads)
        reply = (
            "Добавил задачи:\n"
            + "\n".join(
                f"- {task.title} — {format_deadline(task.deadline, tz)}"
                for task in created
            )
            if created
            else "Такие задачи уже есть — дубликаты не добавлял."
        )
        action = "task_created"
        data = {"count": len(created), "titles": [t.title for t in created]}

    chat = None
    if body.chat_id is not None:
        chat = _find_chat(current_user, db, body.chat_id)
    if chat is not None:
        _store_ai_exchange(chat, "Подтверждаю действие", reply, db)
    else:
        db.commit()
    return ChatResponse(reply=reply, action=action, data=data)


@router.delete("/chats/{chat_id}", status_code=204)
def delete_chat(
    chat_id: int,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    chat = _find_chat(current_user, db, chat_id)
    db.delete(chat)
    db.commit()


@router.delete("/history", status_code=204)
def clear_history(
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    """Удаляет все чаты пользователя вместе с сообщениями."""
    db.query(AIChat).filter(AIChat.user_id == current_user.id).delete(
        synchronize_session=False
    )
    db.query(AIMessage).filter(
        AIMessage.user_id == current_user.id
    ).delete(synchronize_session=False)
    db.commit()


@router.post("/analyze-load")
async def analyze_load(
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    """Сводка нагрузки на ближайшие дни — тот же план, что строит инструмент."""
    tz = resolve_timezone(None, config.TIMEZONE)
    schedule = (
        db.query(Schedule)
        .filter(Schedule.student_id == current_user.id)
        .all()
    )
    tasks = _open_tasks(current_user.id, db, config.AI_CONTEXT_TASKS)
    plan = build_plan(
        schedule, tasks, datetime.now(tz).date(),
        config.AI_CONTEXT_DAYS, tz,
    )
    return {"analysis": format_plan(plan, tz)}


@router.post("/subtasks/{task_id}")
async def create_subtasks(
    task_id: int,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    """Делит одну из своих задач на подзадачи, сгенерированные ИИ."""
    owner, _ = _base_tasks_query(current_user.id)
    task = (
        db.query(Task)
        .filter(Task.id == task_id, owner)
        .first()
    )
    if not task:
        raise HTTPException(404, "Task not found")

    if db.query(Task).filter(Task.parent_task_id == task.id).count():
        raise HTTPException(400, "Task is already split into subtasks")

    prompt = (
        f"Задача: {task.title}\n"
        f"Предмет: {task.subject or 'не указан'}\n"
        f"Описание: {task.description or 'нет'}\n"
        f"Дедлайн: {format_deadline(task.deadline)}\n\n"
        f"Разбей эту задачу на 3-6 конкретных шагов. "
        f"Ответь строго в JSON без лишнего текста:\n"
        f'[{{"title": "шаг 1"}}, {{"title": "шаг 2"}}]'
    )

    message = await _call_ollama(
        [
            {
                "role": "system",
                "content": "Ты помощник по планированию. Отвечай только JSON.",
            },
            {"role": "user", "content": prompt},
        ]
    )
    raw = message.get("content") or ""

    cleaned = (
        raw.strip()
        .removeprefix("```json")
        .removeprefix("```")
        .removesuffix("```")
        .strip()
    )
    try:
        steps = json.loads(cleaned)
        if not isinstance(steps, list):
            raise ValueError
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(500, "AI returned invalid format") from exc

    created: list[str] = []
    for step in steps[:MAX_SUBTASKS]:
        if not isinstance(step, dict):
            continue
        title = str(step.get("title") or "").strip()
        if not title:
            continue
        db.add(
            Task(
                student_id=current_user.id,
                parent_task_id=task.id,
                title=title,
                subject=task.subject,
                deadline=task.deadline,
                priority=task.priority,
                is_personal=task.is_personal,
                # assignment_id намеренно не копируется: подзадача — не
                # отдельный результат задания, а прогресс у учителя
                # считается по задачам с заполненным assignment_id.
                assignment_id=None,
            )
        )
        created.append(title)

    create_notification(
        db=db,
        user_id=current_user.id,
        title="Задача разбита на шаги",
        body=f'"{task.title}" разделена на {len(created)} шагов.',
        channel=NotificationChannel.browser,
        commit=False,
    )
    db.commit()
    return {"created": len(created), "subtasks": created}
