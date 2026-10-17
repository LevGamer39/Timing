import json
import re
import unicodedata
import os
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from dotenv import load_dotenv

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    AIMessage,
    NotificationChannel,
    Schedule,
    Task,
    TaskPriority,
    TaskStatus,
    User,
    UserRole,
)
from app.routers.notifications import create_notification
from app.routers.users import require_role

load_dotenv()

OLLAMA_URL = os.environ.get("OLLAMA_URL")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL")

router = APIRouter(prefix="/api/ai", tags=["ai"])

QUICK_PROMPTS = [
    "Покажи мои задачи по приоритету",
    "Добавь две задачи: подготовиться к физике и прочитать параграф 12 по истории",
    "Удалить задачу по химии",
    "Перенеси задачу по алгебре на завтра 18:00",
]

WEEKDAY_MAP = {
    "понедельник": 0,
    "вторник": 1,
    "среда": 2,
    "среду": 2,
    "четверг": 3,
    "пятница": 4,
    "суббота": 5,
    "воскресенье": 6,
}

SYSTEM_PROMPT = """Ты — умный учебный ассистент для школьника.

Твои главные функции:
- читать и кратко анализировать список задач;
- помогать планировать учебу;
- создавать, обновлять, завершать и удалять личные задачи ученика;
- добавлять сразу несколько задач из одного сообщения;
- объяснять, что именно ты сделал.

Ограничения:
- не решай домашние задания за ученика;
- не пиши готовые сочинения, эссе и контрольные;
- если запрос не про управление задачами, помогай советом, планом и разбиением на шаги.

Формат ответа:
- пиши только обычный текст без HTML;
- не используй теги <br>, <b> и тому подобное;
- отвечай коротко, ясно и по-русски."""

INTENT_PROMPT = """Определи действие пользователя и верни только JSON.

Разрешенные action:
- "create_tasks"
- "list_tasks"
- "delete_tasks"
- "complete_tasks"
- "update_tasks"
- "chat"

Формат:
{
  "action": "create_tasks|list_tasks|delete_tasks|complete_tasks|update_tasks|chat",
  "tasks": [
    {
      "title": "строка",
      "subject": "строка или null",
      "description": "строка или null",
      "deadline_hint": "строка или null",
      "priority": "low|medium|high|critical|null"
    }
  ],
  "task_queries": ["как пользователь называет задачу"],
  "updates": {
    "title": "строка или null",
    "subject": "строка или null",
    "description": "строка или null",
    "deadline_hint": "строка или null",
    "priority": "low|medium|high|critical|null",
    "status": "pending|in_progress|done|overdue|null"
  },
  "filters": {
    "status": "pending|in_progress|done|overdue|null",
    "subject": "строка или null"
  }
}

Правила:
- если пользователь просит добавить несколько задач, положи их все в tasks;
- если пользователь просит прочитать, показать, вывести или перечислить задачи, action = "list_tasks";
- если пользователь просит удалить задачу, action = "delete_tasks";
- если пользователь просит отметить задачу выполненной, action = "complete_tasks";
- если пользователь просит перенести, переименовать, поменять приоритет, срок или предмет, action = "update_tasks";
- если запрос не про действия с задачами, action = "chat";
- никаких пояснений, только JSON."""


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    action: str | None = None
    data: dict | None = None


class MessageResponse(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


def _clean_ai_text(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = cleaned.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    cleaned = re.sub(r"</?b>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</?strong>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</?[^>]+>", "", cleaned)
    cleaned = cleaned.replace("&nbsp;", " ")
    cleaned = cleaned.replace("•", "-")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"[^a-zа-яё0-9\s]", " ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _extract_json(raw: str) -> dict:
    cleaned = (raw or "").strip()
    cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    payload = match.group(0) if match else cleaned
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object")
    return data


def _build_context(student_id: int, db: Session) -> str:
    now = datetime.now(timezone.utc)

    tasks = (
        db.query(Task)
        .filter(Task.student_id == student_id, Task.parent_task_id == None)
        .order_by(Task.deadline.asc().nullslast(), Task.created_at.desc())
        .limit(30)
        .all()
    )

    schedule = (
        db.query(Schedule)
        .filter(Schedule.student_id == student_id)
        .order_by(Schedule.weekday, Schedule.start_time)
        .all()
    )

    weekdays = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    parts = [f"Сегодня: {now.strftime('%d.%m.%Y %H:%M')}"]

    if tasks:
        parts.append("\nЗадачи:")
        for task in tasks:
            deadline_str = task.deadline.strftime("%d.%m %H:%M") if task.deadline else "без срока"
            parts.append(
                f"- id={task.id}; статус={task.status.value}; приоритет={task.priority.value}; "
                f"предмет={task.subject or 'общее'}; название={task.title}; дедлайн={deadline_str}"
            )
    else:
        parts.append("\nЗадач нет.")

    if schedule:
        parts.append("\nРасписание:")
        for item in schedule:
            parts.append(f"- {weekdays[item.weekday]} {item.start_time}-{item.end_time}: {item.subject}")

    return "\n".join(parts)


def _get_history(user_id: int, db: Session, limit: int = 10) -> list[dict]:
    messages = (
        db.query(AIMessage)
        .filter(AIMessage.user_id == user_id)
        .order_by(AIMessage.created_at.desc())
        .limit(limit)
        .all()
    )
    return [{"role": item.role, "content": item.content} for item in reversed(messages)]


async def _call_ollama(messages: list[dict]) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
            return data["message"]["content"]
    except httpx.ConnectError:
        raise HTTPException(503, "AI service unavailable")
    except Exception as exc:
        raise HTTPException(500, f"AI error: {exc}")


async def _detect_intent(user_message: str, context: str) -> dict:
    messages = [
        {"role": "system", "content": INTENT_PROMPT},
        {"role": "user", "content": f"Контекст:\n{context}\n\nСообщение пользователя:\n{user_message}"},
    ]
    try:
        raw = await _call_ollama(messages)
        data = _extract_json(raw)
    except Exception:
        data = {"action": "chat", "tasks": [], "task_queries": [], "updates": {}, "filters": {}}

    data.setdefault("action", "chat")
    data.setdefault("tasks", [])
    data.setdefault("task_queries", [])
    data.setdefault("updates", {})
    data.setdefault("filters", {})
    return data


def _default_priority_from_text(text: str) -> TaskPriority:
    lower = text.lower()
    if any(word in lower for word in ["срочно", "сегодня", "до вечера", "сейчас"]):
        return TaskPriority.critical
    if any(word in lower for word in ["экзам", "огэ", "егэ", "контрольн", "олимпиад", "проект", "сдать"]):
        return TaskPriority.high
    if any(word in lower for word in ["подготов", "домаш", "урок", "презентац", "повторить"]):
        return TaskPriority.medium
    return TaskPriority.medium


def _parse_priority(value: str | None, fallback_text: str = "") -> TaskPriority:
    if value:
        try:
            return TaskPriority(value)
        except ValueError:
            pass
    return _default_priority_from_text(fallback_text)


def _parse_deadline_hint(deadline_hint: str | None, user_message: str = "") -> datetime | None:
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
        match = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b", source)
        if match:
            day = int(match.group(1))
            month = int(match.group(2))
            year_raw = match.group(3)
            year = int(year_raw) if year_raw else now.year
            if year < 100:
                year += 2000
            try:
                base_date = datetime(year, month, day, tzinfo=timezone.utc).date()
            except ValueError:
                base_date = now.date()
        else:
            for weekday_name, weekday_number in WEEKDAY_MAP.items():
                if weekday_name in source:
                    diff = (weekday_number - now.weekday()) % 7
                    if diff == 0:
                        diff = 7
                    base_date = (now + timedelta(days=diff)).date()
                    break

    time_match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", source)
    hour = int(time_match.group(1)) if time_match else 18
    minute = int(time_match.group(2)) if time_match else 0
    return datetime(base_date.year, base_date.month, base_date.day, hour, minute, tzinfo=timezone.utc)


def _store_ai_exchange(user_id: int, user_message: str, reply: str, db: Session) -> None:
    db.add(AIMessage(user_id=user_id, role="user", content=user_message))
    db.add(AIMessage(user_id=user_id, role="assistant", content=reply))
    db.commit()


def _format_deadline(deadline: datetime | None) -> str:
    if not deadline:
        return "без срока"
    return deadline.astimezone(timezone.utc).strftime("%d.%m %H:%M")


def _format_priority(priority: TaskPriority) -> str:
    return {
        TaskPriority.low: "обычный",
        TaskPriority.medium: "важный",
        TaskPriority.high: "высокий",
        TaskPriority.critical: "критичный",
    }[priority]


def _format_task(task: Task, index: int | None = None) -> str:
    prefix = f"{index}. " if index is not None else "- "
    return (
        f"{prefix}{task.title} "
        f"(id: {task.id}, статус: {task.status.value}, приоритет: {task.priority.value}, "
        f"предмет: {task.subject or 'общее'}, дедлайн: {_format_deadline(task.deadline)})"
    )


def _filter_tasks(student_id: int, db: Session, status: str | None = None, subject: str | None = None) -> list[Task]:
    query = db.query(Task).filter(Task.student_id == student_id, Task.parent_task_id == None)
    if status:
        try:
            query = query.filter(Task.status == TaskStatus(status))
        except ValueError:
            pass
    if subject:
        query = query.filter(Task.subject.ilike(f"%{subject.strip()}%"))
    return query.order_by(Task.deadline.asc().nullslast(), Task.created_at.desc()).all()


def _match_tasks(student_id: int, queries: list[str], db: Session) -> list[Task]:
    all_tasks = _filter_tasks(student_id, db)
    if not queries:
        return []

    matched: list[Task] = []
    seen_ids: set[int] = set()

    for query in queries:
        query_norm = _normalize_text(query)
        if not query_norm:
            continue

        if query_norm.isdigit():
            direct = next((task for task in all_tasks if task.id == int(query_norm)), None)
            if direct and direct.id not in seen_ids:
                matched.append(direct)
                seen_ids.add(direct.id)
                continue

        scored: list[tuple[float, Task]] = []
        for task in all_tasks:
            title_norm = _normalize_text(task.title)
            score = SequenceMatcher(None, query_norm, title_norm).ratio()
            if query_norm in title_norm:
                score += 0.35
            if task.subject and _normalize_text(task.subject) in query_norm:
                score += 0.1
            scored.append((score, task))

        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_task = scored[0]
        if best_score >= 0.55 and best_task.id not in seen_ids:
            matched.append(best_task)
            seen_ids.add(best_task.id)

    return matched


def _fallback_create_tasks(message: str) -> list[dict]:
    separators = re.split(r"(?:\n|;|, и | и еще | ещё | также )", message, flags=re.IGNORECASE)
    items: list[dict] = []
    for chunk in separators:
        title = chunk.strip(" .,:;-")
        title = re.sub(
            r"^(добавь|создай|запиши|поставь)\s+(мне\s+)?(задачу|задачи|дело|дела|напоминание)\s*",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip(" .,:;-")
        if len(title) < 3:
            continue
        items.append(
            {
                "title": title,
                "subject": None,
                "description": None,
                "deadline_hint": None,
                "priority": None,
            }
        )
    return items[:8]


def _build_create_payloads(message: str, parsed_tasks: list[dict]) -> list[dict]:
    source_tasks = parsed_tasks or _fallback_create_tasks(message)
    payloads: list[dict] = []

    for item in source_tasks[:8]:
        title = str(item.get("title") or "").strip(" .,:;-")
        if not title:
            continue
        payloads.append(
            {
                "title": title,
                "subject": (item.get("subject") or None),
                "description": (item.get("description") or None),
                "deadline": _parse_deadline_hint(item.get("deadline_hint"), message),
                "priority": _parse_priority(item.get("priority"), title + " " + message),
            }
        )
    return payloads


def _list_tasks_response(user_message: str, current_user: User, db: Session, filters: dict) -> ChatResponse:
    tasks = _filter_tasks(current_user.id, db, filters.get("status"), filters.get("subject"))
    if not tasks:
        reply = "Сейчас подходящих задач нет."
        _store_ai_exchange(current_user.id, user_message, reply, db)
        return ChatResponse(reply=reply, action="tasks_listed", data={"count": 0})

    lines = ["Вот твои задачи:"]
    for index, task in enumerate(tasks[:15], start=1):
        lines.append(_format_task(task, index))
    if len(tasks) > 15:
        lines.append(f"И еще {len(tasks) - 15} задач.")

    reply = "\n".join(lines)
    _store_ai_exchange(current_user.id, user_message, reply, db)
    return ChatResponse(reply=reply, action="tasks_listed", data={"count": len(tasks)})


def _create_tasks_response(user_message: str, current_user: User, db: Session, parsed_tasks: list[dict]) -> ChatResponse:
    payloads = _build_create_payloads(user_message, parsed_tasks)
    if not payloads:
        reply = "Не смог понять, какие именно задачи нужно добавить. Напиши их чуть конкретнее."
        _store_ai_exchange(current_user.id, user_message, reply, db)
        return ChatResponse(reply=reply)

    created_tasks: list[Task] = []
    for payload in payloads:
        task = Task(
            student_id=current_user.id,
            title=payload["title"],
            subject=payload["subject"],
            description=payload["description"],
            deadline=payload["deadline"],
            priority=payload["priority"],
            is_personal=True,
        )
        db.add(task)
        db.flush()
        created_tasks.append(task)

    reply_lines = ["Добавил задачи:"]
    for index, task in enumerate(created_tasks, start=1):
        reply_lines.append(
            f"{index}. {task.title} — {_format_deadline(task.deadline)}, приоритет: {_format_priority(task.priority)}"
        )
    reply = "\n".join(reply_lines)

    create_notification(
        db=db,
        user_id=current_user.id,
        title="ИИ добавил задачи",
        body=f"Добавлено задач: {len(created_tasks)}",
        channel=NotificationChannel.browser,
    )
    _store_ai_exchange(current_user.id, user_message, reply, db)
    return ChatResponse(
        reply=reply,
        action="task_created",
        data={"count": len(created_tasks), "titles": [task.title for task in created_tasks]},
    )


def _delete_tasks_response(user_message: str, current_user: User, db: Session, task_queries: list[str]) -> ChatResponse:
    tasks = _match_tasks(current_user.id, task_queries, db)
    personal_tasks = [task for task in tasks if task.is_personal]

    if not personal_tasks:
        reply = "Не нашел личные задачи для удаления. Попробуй указать название точнее."
        _store_ai_exchange(current_user.id, user_message, reply, db)
        return ChatResponse(reply=reply)

    titles = [task.title for task in personal_tasks]
    for task in personal_tasks:
        db.delete(task)

    reply = "Удалил задачи:\n" + "\n".join(f"- {title}" for title in titles)
    _store_ai_exchange(current_user.id, user_message, reply, db)
    return ChatResponse(reply=reply, action="task_deleted", data={"count": len(titles), "titles": titles})


def _complete_tasks_response(user_message: str, current_user: User, db: Session, task_queries: list[str]) -> ChatResponse:
    tasks = _match_tasks(current_user.id, task_queries, db)
    if not tasks:
        reply = "Не нашел задачи, которые нужно отметить выполненными."
        _store_ai_exchange(current_user.id, user_message, reply, db)
        return ChatResponse(reply=reply)

    now = datetime.now(timezone.utc)
    for task in tasks:
        task.status = TaskStatus.done
        task.completed_at = now

    reply = "Отметил выполненными:\n" + "\n".join(f"- {task.title}" for task in tasks)
    _store_ai_exchange(current_user.id, user_message, reply, db)
    return ChatResponse(reply=reply, action="task_completed", data={"count": len(tasks), "titles": [task.title for task in tasks]})


def _update_tasks_response(user_message: str, current_user: User, db: Session, task_queries: list[str], updates: dict) -> ChatResponse:
    tasks = _match_tasks(current_user.id, task_queries, db)
    if not tasks:
        reply = "Не нашел задачи для изменения. Уточни название."
        _store_ai_exchange(current_user.id, user_message, reply, db)
        return ChatResponse(reply=reply)

    changed_fields: list[str] = []
    deadline = _parse_deadline_hint(updates.get("deadline_hint"), user_message) if updates.get("deadline_hint") else None
    priority = _parse_priority(updates.get("priority"), user_message) if updates.get("priority") else None
    status = None
    if updates.get("status"):
        try:
            status = TaskStatus(updates["status"])
        except ValueError:
            status = None

    for task in tasks:
        if updates.get("title"):
            task.title = updates["title"].strip()
            if "название" not in changed_fields:
                changed_fields.append("название")
        if "subject" in updates and updates.get("subject"):
            task.subject = updates["subject"].strip()
            if "предмет" not in changed_fields:
                changed_fields.append("предмет")
        if "description" in updates and updates.get("description"):
            task.description = updates["description"].strip()
            if "описание" not in changed_fields:
                changed_fields.append("описание")
        if deadline:
            task.deadline = deadline
            if "срок" not in changed_fields:
                changed_fields.append("срок")
        if priority:
            task.priority = priority
            if "приоритет" not in changed_fields:
                changed_fields.append("приоритет")
        if status:
            task.status = status
            if status == TaskStatus.done and not task.completed_at:
                task.completed_at = datetime.now(timezone.utc)
            if "статус" not in changed_fields:
                changed_fields.append("статус")

    if not changed_fields:
        reply = "Я понял, какую задачу ты имеешь в виду, но не увидел, что именно нужно поменять."
        _store_ai_exchange(current_user.id, user_message, reply, db)
        return ChatResponse(reply=reply)

    reply = (
        f"Обновил {len(tasks)} задач. Изменил: {', '.join(changed_fields)}.\n" +
        "\n".join(f"- {task.title}" for task in tasks)
    )
    _store_ai_exchange(current_user.id, user_message, reply, db)
    return ChatResponse(reply=reply, action="task_updated", data={"count": len(tasks), "fields": changed_fields})


def _detect_action(reply: str, user_message: str) -> tuple[str | None, dict | None]:
    lower = user_message.lower()
    if any(word in lower for word in ["разбей", "раздели", "по шагам", "подзадачи"]):
        if any(char in reply for char in ["1.", "2.", "•", "-"]):
            return "create_subtasks", {"hint": "Хочешь сохранить эти шаги как подзадачи?"}
    return None, None


async def _handle_task_action(body: ChatRequest, current_user: User, db: Session) -> ChatResponse | None:
    context = _build_context(current_user.id, db)
    intent = await _detect_intent(body.message, context)
    action = intent.get("action")

    if action == "list_tasks":
        return _list_tasks_response(body.message, current_user, db, intent.get("filters") or {})
    if action == "create_tasks":
        return _create_tasks_response(body.message, current_user, db, intent.get("tasks") or [])
    if action == "delete_tasks":
        return _delete_tasks_response(body.message, current_user, db, intent.get("task_queries") or [])
    if action == "complete_tasks":
        return _complete_tasks_response(body.message, current_user, db, intent.get("task_queries") or [])
    if action == "update_tasks":
        return _update_tasks_response(body.message, current_user, db, intent.get("task_queries") or [], intent.get("updates") or {})
    return None


@router.get("/quick-prompts")
def quick_prompts():
    return {"prompts": QUICK_PROMPTS}


@router.get("/history", response_model=list[MessageResponse])
def get_history(
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    return (
        db.query(AIMessage)
        .filter(AIMessage.user_id == current_user.id)
        .order_by(AIMessage.created_at.asc())
        .limit(100)
        .all()
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    action_response = await _handle_task_action(body, current_user, db)
    if action_response:
        return action_response

    context = _build_context(current_user.id, db)
    history = _get_history(current_user.id, db)

    messages = [
        {"role": "system", "content": f"{SYSTEM_PROMPT}\n\nКонтекст ученика:\n{context}"},
        *history,
        {"role": "user", "content": body.message},
    ]

    reply = _clean_ai_text(await _call_ollama(messages))
    _store_ai_exchange(current_user.id, body.message, reply, db)

    action, data = _detect_action(reply, body.message)
    return ChatResponse(reply=reply, action=action, data=data)


@router.post("/analyze-load")
async def analyze_load(
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    context = _build_context(current_user.id, db)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Проанализируй мою нагрузку и дай краткие рекомендации:\n{context}"},
    ]
    reply = _clean_ai_text(await _call_ollama(messages))
    return {"analysis": reply}


@router.post("/subtasks/{task_id}")
async def create_subtasks(
    task_id: int,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    task = (
        db.query(Task)
        .filter(Task.id == task_id, Task.student_id == current_user.id)
        .first()
    )
    if not task:
        raise HTTPException(404, "Task not found")

    deadline_str = task.deadline.strftime("%d.%m.%Y") if task.deadline else "не указан"
    prompt = (
        f"Задача: {task.title}\n"
        f"Предмет: {task.subject or 'не указан'}\n"
        f"Описание: {task.description or 'нет'}\n"
        f"Дедлайн: {deadline_str}\n\n"
        f"Разбей эту задачу на 3-6 конкретных шагов. "
        f"Ответь строго в JSON без лишнего текста:\n"
        f'[{{"title": "шаг 1"}}, {{"title": "шаг 2"}}]'
    )

    messages = [
        {"role": "system", "content": "Ты помощник по планированию. Отвечай только JSON."},
        {"role": "user", "content": prompt},
    ]

    raw = await _call_ollama(messages)

    try:
        raw_clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        steps = json.loads(raw_clean)
        if not isinstance(steps, list):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(500, "AI returned invalid format")

    created = []
    for step in steps[:6]:
        title = step.get("title", "").strip()
        if not title:
            continue
        subtask = Task(
            student_id=current_user.id,
            parent_task_id=task.id,
            title=title,
            subject=task.subject,
            deadline=task.deadline,
            priority=task.priority,
            is_personal=task.is_personal,
            assignment_id=task.assignment_id,
        )
        db.add(subtask)
        created.append(title)

    create_notification(
        db=db,
        user_id=current_user.id,
        title="Задача разбита на шаги",
        body=f'"{task.title}" разделена на {len(created)} шагов.',
        channel=NotificationChannel.browser,
    )

    db.commit()
    return {"created": len(created), "subtasks": created}


@router.delete("/history", status_code=204)
def clear_history(
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    db.query(AIMessage).filter(AIMessage.user_id == current_user.id).delete()
    db.commit()
