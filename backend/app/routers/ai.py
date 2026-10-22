"""Эндпоинты ИИ-ассистента на базе Ollama.

Вся чистая логика парсинга и матчинга вынесена в :mod:`app.ai_text`, чтобы её
можно было покрыть юнит-тестами; этот модуль занимается только обращением
к БД и вызовами модели.
"""
import json
import re
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import config
from app.ai_text import (
    clean_ai_text,
    describe_schedule,
    detect_action,
    extract_json,
    format_deadline,
    format_priority,
    format_task,
    match_tasks,
    parse_deadline_hint,
    parse_priority,
)
from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models import (
    AIMessage,
    NotificationChannel,
    Schedule,
    Task,
    TaskStatus,
    User,
    UserRole,
)
from app.routers.notifications import create_notification

OLLAMA_URL = config.OLLAMA_URL
OLLAMA_MODEL = config.OLLAMA_MODEL

router = APIRouter(prefix="/api/ai", tags=["ai"])

OLLAMA_TIMEOUT = 60.0
MAX_CONTEXT_TASKS = 30
MAX_HISTORY_MESSAGES = 10
MAX_HISTORY_ROWS = 100
MAX_SUBTASKS = 6
MAX_LISTED_TASKS = 15
MAX_CREATED_TASKS = 8

QUICK_PROMPTS = [
    "Покажи мои задачи по приоритету",
    "Добавь две задачи: подготовиться к физике и прочитать параграф 12 по истории",
    "Удалить задачу по химии",
    "Перенеси задачу по алгебре на завтра 18:00",
]

SYSTEM_PROMPT = """Ты — «Вектор», умный планировщик для школьников.
Твоя задача — помогать ученику организовать учёбу и управлять задачами.

Ты можешь:
- отвечать на вопросы по организации учёбы и планированию времени;
- помогать разбивать большие задачи на шаги;
- подсказывать приоритеты и подходящее время для работы;
- объяснять, что именно ты сделал.

Ограничения:
- не решай домашние задания за ученика;
- не пиши готовые сочинения, эссе и контрольные;
- если запрос не про управление задачами, помогай советом, планом и разбиением на шаги.

Формат ответа:
- пиши только обычный текст без HTML;
- не используй теги <br>, <b> и тому подобные;
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


# ---------------------------------------------------------------------------
# Схемы
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Хелперы для БД
# ---------------------------------------------------------------------------


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


def _build_context(student_id: int, db: Session) -> str:
    now = datetime.now(timezone.utc)
    owner, top_level = _base_tasks_query(student_id)
    tasks = (
        db.query(Task)
        .filter(owner, top_level)
        .order_by(Task.deadline.asc().nullslast(), Task.created_at.desc())
        .limit(MAX_CONTEXT_TASKS)
        .all()
    )
    schedule = (
        db.query(Schedule)
        .filter(Schedule.student_id == student_id)
        .order_by(Schedule.weekday, Schedule.start_time)
        .all()
    )

    parts = [f"Сегодня: {now.strftime('%d.%m.%Y %H:%M')}"]

    if tasks:
        parts.append("\nЗадачи:")
        for task in tasks:
            parts.append(
                f"- id={task.id}; статус={task.status.value}; "
                f"приоритет={task.priority.value}; "
                f"предмет={task.subject or 'общее'}; название={task.title}; "
                f"дедлайн={format_deadline(task.deadline)}"
            )
    else:
        parts.append("\nЗадач нет.")

    if schedule:
        parts.append("\nРасписание:")
        parts.extend(describe_schedule(schedule))

    return "\n".join(parts)


def _get_history(user_id: int, db: Session) -> list[dict]:
    messages = (
        db.query(AIMessage)
        .filter(AIMessage.user_id == user_id)
        .order_by(AIMessage.created_at.desc())
        .limit(MAX_HISTORY_MESSAGES)
        .all()
    )
    return [
        {"role": item.role, "content": item.content} for item in reversed(messages)
    ]


def _store_ai_exchange(
    user_id: int, user_message: str, reply: str, db: Session
) -> None:
    db.add(AIMessage(user_id=user_id, role="user", content=user_message))
    db.add(AIMessage(user_id=user_id, role="assistant", content=reply))
    db.commit()


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


async def _call_ollama(messages: list[dict]) -> str:
    if not OLLAMA_URL or not OLLAMA_MODEL:
        raise HTTPException(503, "AI service is not configured")

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/chat", json=payload
            )
            response.raise_for_status()
            return response.json()["message"]["content"]
    except httpx.ConnectError as exc:
        raise HTTPException(503, "AI service unavailable") from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"AI error: {exc}") from exc


async def _detect_intent(user_message: str, context: str) -> dict:
    """Спрашивает у модели, что делать; при любой ошибке — обычный чат."""
    messages = [
        {"role": "system", "content": INTENT_PROMPT},
        {
            "role": "user",
            "content": (
                f"Контекст:\n{context}\n\n"
                f"Сообщение пользователя:\n{user_message}"
            ),
        },
    ]
    try:
        data = extract_json(await _call_ollama(messages))
    except Exception:  # noqa: BLE001
        data = {}

    if not isinstance(data, dict):
        data = {}
    data.setdefault("action", "chat")
    data.setdefault("tasks", [])
    data.setdefault("task_queries", [])
    data.setdefault("updates", {})
    data.setdefault("filters", {})
    return data


# ---------------------------------------------------------------------------
# Обработчики намерений
# ---------------------------------------------------------------------------


def _fallback_create_tasks(message: str) -> list[dict]:
    """Делит сообщение на названия задач, если модель не вернула JSON."""
    separators = re.split(
        r"(?:\n|;|, и | и еще | ещё | также )", message, flags=re.IGNORECASE
    )
    items: list[dict] = []
    for chunk in separators:
        title = chunk.strip(" .,:;-")
        title = re.sub(
            r"^(добавь|создай|запиши|поставь)\s+(мне\s+)?"
            r"(задачу|задачи|дело|дела|напоминание)\s*",
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
    return items


def _build_create_payloads(
    message: str, parsed_tasks: list[dict]
) -> list[dict]:
    source = parsed_tasks or _fallback_create_tasks(message)
    payloads: list[dict] = []

    for item in source[:MAX_CREATED_TASKS]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip(" .,:;-")
        if not title:
            continue
        payloads.append(
            {
                "title": title,
                "subject": item.get("subject") or None,
                "description": item.get("description") or None,
                "deadline": parse_deadline_hint(
                    item.get("deadline_hint"), message
                ),
                "priority": parse_priority(
                    item.get("priority"), f"{title} {message}"
                ),
            }
        )
    return payloads


def _list_tasks_response(
    user_message: str, current_user: User, db: Session, filters: dict
) -> ChatResponse:
    tasks = _filter_tasks(
        current_user.id, db, filters.get("status"), filters.get("subject")
    )
    if not tasks:
        reply = "Сейчас подходящих задач нет."
        _store_ai_exchange(current_user.id, user_message, reply, db)
        return ChatResponse(reply=reply, action="tasks_listed", data={"count": 0})

    lines = ["Вот твои задачи:"]
    for index, task in enumerate(tasks[:MAX_LISTED_TASKS], start=1):
        lines.append(
            format_task(
                task, task.status.value, index
            )
        )
    if len(tasks) > MAX_LISTED_TASKS:
        lines.append(f"И еще {len(tasks) - MAX_LISTED_TASKS} задач.")

    reply = "\n".join(lines)
    _store_ai_exchange(current_user.id, user_message, reply, db)
    return ChatResponse(
        reply=reply, action="tasks_listed", data={"count": len(tasks)}
    )


def _create_tasks_response(
    user_message: str, current_user: User, db: Session, parsed_tasks: list[dict]
) -> ChatResponse:
    payloads = _build_create_payloads(user_message, parsed_tasks)
    if not payloads:
        reply = (
            "Не смог понять, какие именно задачи нужно добавить. "
            "Напиши их чуть конкретнее."
        )
        _store_ai_exchange(current_user.id, user_message, reply, db)
        return ChatResponse(reply=reply)

    created: list[Task] = []
    for payload in payloads:
        task = Task(
            student_id=current_user.id,
            title=payload["title"],
            subject=payload["subject"],
            description=payload["description"],
            deadline=payload["deadline"],
            priority=payload["priority"],
            is_personal=True,
            status=TaskStatus.pending,
        )
        db.add(task)
        db.flush()
        created.append(task)

    reply_lines = ["Добавил задачи:"]
    for index, task in enumerate(created, start=1):
        reply_lines.append(
            f"{index}. {task.title} — {format_deadline(task.deadline)}, "
            f"приоритет: {format_priority(task.priority)}"
        )
    reply = "\n".join(reply_lines)

    create_notification(
        db=db,
        user_id=current_user.id,
        title="ИИ добавил задачи",
        body=f"Добавлено задач: {len(created)}",
        channel=NotificationChannel.browser,
        commit=False,
    )
    _store_ai_exchange(current_user.id, user_message, reply, db)
    return ChatResponse(
        reply=reply,
        action="task_created",
        data={"count": len(created), "titles": [t.title for t in created]},
    )


def _delete_tasks_response(
    user_message: str, current_user: User, db: Session, task_queries: list[str]
) -> ChatResponse:
    matched = _match_tasks(current_user.id, task_queries, db)
    personal = [task for task in matched if task.is_personal]

    if not personal:
        reply = (
            "Не нашел личные задачи для удаления. "
            "Попробуй указать название точнее."
        )
        _store_ai_exchange(current_user.id, user_message, reply, db)
        return ChatResponse(reply=reply)

    titles = [task.title for task in personal]
    for task in personal:
        # Подзадачи удалятся каскадом через связь в ORM.
        db.delete(task)
    db.commit()

    reply = "Удалил задачи:\n" + "\n".join(f"- {title}" for title in titles)
    _store_ai_exchange(current_user.id, user_message, reply, db)
    return ChatResponse(
        reply=reply, action="task_deleted",
        data={"count": len(titles), "titles": titles},
    )


def _complete_tasks_response(
    user_message: str, current_user: User, db: Session, task_queries: list[str]
) -> ChatResponse:
    matched = _match_tasks(current_user.id, task_queries, db)
    if not matched:
        reply = "Не нашел задачи, которые нужно отметить выполненными."
        _store_ai_exchange(current_user.id, user_message, reply, db)
        return ChatResponse(reply=reply)

    now = datetime.now(timezone.utc)
    for task in matched:
        task.status = TaskStatus.done
        task.completed_at = now
    db.commit()

    titles = [task.title for task in matched]
    reply = "Отметил выполненными:\n" + "\n".join(f"- {t}" for t in titles)
    _store_ai_exchange(current_user.id, user_message, reply, db)
    return ChatResponse(
        reply=reply, action="task_completed",
        data={"count": len(titles), "titles": titles},
    )


def _update_tasks_response(
    user_message: str,
    current_user: User,
    db: Session,
    task_queries: list[str],
    updates: dict,
) -> ChatResponse:
    matched = _match_tasks(current_user.id, task_queries, db)
    if not matched:
        reply = "Не нашел задачи для изменения. Уточни название."
        _store_ai_exchange(current_user.id, user_message, reply, db)
        return ChatResponse(reply=reply)

    # Ученик может править только задачи, созданные им самим.
    tasks = [task for task in matched if task.is_personal]
    if not tasks:
        reply = "Это задание от учителя — изменить его нельзя."
        _store_ai_exchange(current_user.id, user_message, reply, db)
        return ChatResponse(reply=reply)

    changed: list[str] = []

    def note(field: str, label: str) -> None:
        if label not in changed:
            changed.append(label)

    deadline = (
        parse_deadline_hint(updates.get("deadline_hint"), user_message)
        if updates.get("deadline_hint")
        else None
    )
    priority = (
        parse_priority(updates.get("priority"), user_message)
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
            task.title = updates["title"].strip()
            note("title", "название")
        if updates.get("subject"):
            task.subject = updates["subject"].strip()
            note("subject", "предмет")
        if updates.get("description"):
            task.description = updates["description"].strip()
            note("description", "описание")
        if deadline:
            task.deadline = deadline
            note("deadline", "срок")
        if priority:
            task.priority = priority
            note("priority", "приоритет")
        if status:
            task.status = status
            if status == TaskStatus.done and task.completed_at is None:
                task.completed_at = datetime.now(timezone.utc)
            elif status != TaskStatus.done:
                task.completed_at = None
            note("status", "статус")

    if not changed:
        reply = (
            "Я понял, какую задачу ты имеешь в виду, "
            "но не увидел, что именно нужно поменять."
        )
        _store_ai_exchange(current_user.id, user_message, reply, db)
        return ChatResponse(reply=reply)

    db.commit()
    reply = (
        f"Обновил {len(tasks)} задач. Изменил: {', '.join(changed)}.\n"
        + "\n".join(f"- {task.title}" for task in tasks)
    )
    _store_ai_exchange(current_user.id, user_message, reply, db)
    return ChatResponse(
        reply=reply, action="task_updated",
        data={"count": len(tasks), "fields": changed},
    )


async def _handle_task_action(
    body: ChatRequest, current_user: User, db: Session
) -> ChatResponse | None:
    context = _build_context(current_user.id, db)
    intent = await _detect_intent(body.message, context)
    action = intent.get("action")

    if action == "list_tasks":
        return _list_tasks_response(
            body.message, current_user, db, intent.get("filters") or {}
        )
    if action == "create_tasks":
        return _create_tasks_response(
            body.message, current_user, db, intent.get("tasks") or []
        )
    if action == "delete_tasks":
        return _delete_tasks_response(
            body.message, current_user, db, intent.get("task_queries") or []
        )
    if action == "complete_tasks":
        return _complete_tasks_response(
            body.message, current_user, db, intent.get("task_queries") or []
        )
    if action == "update_tasks":
        return _update_tasks_response(
            body.message,
            current_user,
            db,
            intent.get("task_queries") or [],
            intent.get("updates") or {},
        )
    return None


# ---------------------------------------------------------------------------
# Роуты
# ---------------------------------------------------------------------------


@router.get("/quick-prompts")
def quick_prompts(current_user: User = Depends(get_current_user)):
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
        .limit(MAX_HISTORY_ROWS)
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

    messages = [
        {
            "role": "system",
            "content": f"{SYSTEM_PROMPT}\n\n"
            f"Контекст ученика:\n{_build_context(current_user.id, db)}",
        },
        *_get_history(current_user.id, db),
        {"role": "user", "content": body.message},
    ]

    reply = clean_ai_text(await _call_ollama(messages))
    _store_ai_exchange(current_user.id, body.message, reply, db)

    action, data = detect_action(reply, body.message)
    return ChatResponse(reply=reply, action=action, data=data)


@router.post("/analyze-load")
async def analyze_load(
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    context = _build_context(current_user.id, db)
    reply = clean_ai_text(
        await _call_ollama(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Проанализируй мою нагрузку "
                        f"и дай краткие рекомендации:\n{context}"
                    ),
                },
            ]
        )
    )
    return {"analysis": reply}


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

    raw = await _call_ollama(
        [
            {
                "role": "system",
                "content": "Ты помощник по планированию. Отвечай только JSON.",
            },
            {"role": "user", "content": prompt},
        ]
    )

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


@router.delete("/history", status_code=204)
def clear_history(
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    db.query(AIMessage).filter(AIMessage.user_id == current_user.id).delete(
        synchronize_session=False
    )
    db.commit()