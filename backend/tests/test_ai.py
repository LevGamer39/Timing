"""Тесты ИИ-роутера: чаты, история, сжатие, инструменты, подтверждения."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app import config
from app.models import (
    AIChat,
    AIMessage,
    Grade,
    Schedule,
    Task,
    TaskPriority,
    TaskStatus,
)
from app.routers import ai

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def make_chat(db, user, title="Чат") -> AIChat:
    chat = AIChat(user_id=user.id, title=title)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def add_messages(db, chat, count=4, size=10) -> list[AIMessage]:
    rows = []
    for index in range(count):
        row = AIMessage(
            user_id=chat.user_id,
            chat_id=chat.id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"{index}" * size,
        )
        db.add(row)
        rows.append(row)
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


def tool_call(name, arguments) -> dict:
    return {
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        }
    }


# ---------------------------------------------------------------------------
# Чаты
# ---------------------------------------------------------------------------


def test_resolve_chat_creates_chat_titled_from_message(db, student):
    chat = ai._resolve_chat(student, db, None, False, "Составь расписание")
    assert chat.id is not None
    assert chat.title == "Составь расписание"


def test_resolve_chat_reuses_latest_chat(db, student):
    first = make_chat(db, student, "Старый")
    second = make_chat(db, student, "Новый")
    first.updated_at = NOW
    second.updated_at = NOW + timedelta(minutes=1)
    db.commit()
    assert ai._resolve_chat(student, db, None, False, "привет").id == second.id
    assert first.id != second.id


def test_resolve_chat_new_chat_flag_creates_another(db, student):
    existing = make_chat(db, student, "Старый")
    chat = ai._resolve_chat(student, db, None, True, "Новая тема")
    assert chat.id != existing.id


def test_find_chat_rejects_foreign_chat(db, student, student2):
    foreign = make_chat(db, student2, "Чужой")
    with pytest.raises(HTTPException) as exc:
        ai._find_chat(student, db, foreign.id)
    assert exc.value.status_code == 404


def test_history_is_scoped_to_one_chat(db, student):
    first = make_chat(db, student, "Первый")
    second = make_chat(db, student, "Второй")
    add_messages(db, first, 2)
    add_messages(db, second, 2)

    rows = ai.get_history(
        chat_id=second.id, current_user=student, db=db
    )
    assert len(rows) == 2
    assert all(row.chat_id == second.id for row in rows)


def test_history_without_chat_id_returns_latest_chat(db, student):
    older = make_chat(db, student, "Старый")
    newer = make_chat(db, student, "Новый")
    older.updated_at = NOW
    newer.updated_at = NOW + timedelta(minutes=1)
    db.commit()
    add_messages(db, older, 2)
    add_messages(db, newer, 2)
    rows = ai.get_history(current_user=student, db=db)
    assert {row.chat_id for row in rows} == {newer.id}


def test_delete_chat_removes_its_messages(db, student):
    chat = make_chat(db, student)
    add_messages(db, chat, 4)
    ai.delete_chat(chat.id, current_user=student, db=db)
    assert db.query(AIMessage).filter(AIMessage.chat_id == chat.id).count() == 0


def test_clear_history_does_not_touch_other_users(db, student, student2):
    mine = make_chat(db, student)
    add_messages(db, mine, 2)
    theirs = make_chat(db, student2)
    add_messages(db, theirs, 2)

    ai.clear_history(current_user=student, db=db)
    assert db.query(AIChat).filter(AIChat.user_id == student.id).count() == 0
    assert db.query(AIChat).filter(AIChat.user_id == student2.id).count() == 1


# ---------------------------------------------------------------------------
# История и сжатие
# ---------------------------------------------------------------------------


def test_store_ai_exchange_keeps_user_then_assistant(db, student):
    chat = make_chat(db, student)
    ai._store_ai_exchange(chat, "вопрос", "ответ", db)
    rows = db.query(AIMessage).filter(AIMessage.chat_id == chat.id).all()
    assert [row.role for row in rows] == ["user", "assistant"]


def test_prompt_history_is_chronological(db, student):
    chat = make_chat(db, student)
    ai._store_ai_exchange(chat, "первый", "ответ 1", db)
    ai._store_ai_exchange(chat, "второй", "ответ 2", db)
    db.refresh(chat)
    history = ai._prompt_history(chat)
    assert history[0]["content"] == "первый"
    assert history[-1]["content"] == "ответ 2"


def test_prompt_history_respects_character_budget(db, student, monkeypatch):
    monkeypatch.setattr(config, "AI_HISTORY_CHARS", 12)
    chat = make_chat(db, student)
    add_messages(db, chat, 4, size=10)
    db.refresh(chat)
    history = ai._prompt_history(chat)
    assert len(history) == 1


def test_prompt_history_skips_compacted_messages(db, student):
    chat = make_chat(db, student)
    rows = add_messages(db, chat, 4)
    chat.summary = "кратко"
    chat.summary_upto_id = rows[1].id
    db.commit()
    db.refresh(chat)
    history = ai._prompt_history(chat)
    assert [item["content"] for item in history] == [
        rows[2].content, rows[3].content,
    ]


def test_compact_chat_sets_summary_and_cutoff(db, student, monkeypatch):
    monkeypatch.setattr(config, "AI_COMPACT_KEEP", 2)
    chat = make_chat(db, student)
    rows = add_messages(db, chat, 6)
    db.refresh(chat)

    async def fake(messages, tools=None, options=None):
        return {"content": "Краткое резюме"}

    monkeypatch.setattr(ai, "_call_ollama", fake)
    asyncio.run(ai._compact_chat(chat, db))

    db.refresh(chat)
    assert chat.summary == "Краткое резюме"
    assert chat.summary_upto_id == rows[3].id


def test_compact_chat_failure_keeps_chat_working(db, student, monkeypatch):
    monkeypatch.setattr(config, "AI_COMPACT_KEEP", 1)
    chat = make_chat(db, student)
    add_messages(db, chat, 4)
    db.refresh(chat)

    async def boom(messages, tools=None, options=None):
        raise HTTPException(503, "down")

    monkeypatch.setattr(ai, "_call_ollama", boom)
    asyncio.run(ai._compact_chat(chat, db))

    db.refresh(chat)
    assert chat.summary is None
    assert ai._prompt_history(chat)


# ---------------------------------------------------------------------------
# Инструменты
# ---------------------------------------------------------------------------


def test_insert_tasks_dedupes_same_title_and_day(db, student):
    payload = {
        "title": "Параграф 12",
        "deadline": NOW + timedelta(days=1),
        "priority": TaskPriority.medium,
    }
    created = ai._insert_tasks(student, db, [payload, dict(payload)])
    db.commit()
    assert len(created) == 1


def test_create_tasks_requires_confirmation_for_three(db, student):
    args = {
        "tasks": [{"title": f"Задача {i}"} for i in range(3)],
    }
    response = ai._create_tasks_response(
        student, db, args, TZ, "добавь три задачи"
    )
    assert response.action == "confirm_create"
    assert response.data["pending"]["operation"] == "create"
    assert len(response.data["pending"]["tasks"]) == 3
    # Без подтверждения ничего не создано.
    assert db.query(Task).filter(Task.student_id == student.id).count() == 0


def test_create_tasks_small_batch_is_immediate(db, student):
    args = {"tasks": [{"title": "Прочитать параграф"}]}
    response = ai._create_tasks_response(
        student, db, args, TZ, "добавь задачу"
    )
    assert response.action == "task_created"
    assert db.query(Task).filter(Task.student_id == student.id).count() == 1


def test_create_task_without_date_has_no_invented_deadline(db, student):
    args = {"tasks": [{"title": "Полить цветы"}]}
    ai._create_tasks_response(student, db, args, TZ, "добавь задачу")
    task = db.query(Task).filter(Task.student_id == student.id).one()
    assert task.deadline is None


def test_create_task_uses_date_from_message(db, student):
    args = {"tasks": [{"title": "Полить цветы"}]}
    ai._create_tasks_response(
        student, db, args, TZ, "добавь задачу на завтра"
    )
    task = db.query(Task).filter(Task.student_id == student.id).one()
    assert task.deadline is not None
    assert task.deadline.astimezone(TZ).hour == 18


def test_run_tool_parses_json_string_arguments(db, student, make_task):
    make_task(student.id, title="Параграф", subject="Алгебра")
    call = {
        "function": {"name": "get_all_tasks", "arguments": "{\"subject\": \"алгебра\"}"},
    }
    response = ai._run_tool(call, student, db, TZ, "покажи задачи")
    assert response.action == "tasks_listed"
    assert "Параграф" in response.reply


def test_run_tool_ignores_unknown_names(db, student):
    assert ai._run_tool(tool_call("drop_database", {}), student, db, TZ, "") is None


def test_build_plan_tool_returns_structured_reply(db, student, make_task):
    make_task(
        student.id,
        title="Домашка",
        deadline=NOW + timedelta(days=1),
    )
    response = ai._run_tool(
        tool_call("build_plan", {"days": 2}), student, db, TZ, "расписание"
    )
    assert response.action == "plan"
    assert "Расписание на 2 дня" in response.reply


def test_get_subject_grades_tool(db, student, teacher):
    db.add(
        Grade(
            student_id=student.id,
            subject="Алгебра",
            value=4,
            graded_by_id=teacher.id,
        )
    )
    db.commit()
    response = ai._run_tool(
        tool_call("get_subject_grades", {"subject": "алгебра"}),
        student, db, TZ, "оценки",
    )
    assert response.action == "grades_listed"
    assert "средний 4.00" in response.reply


def test_delete_tool_only_proposes_and_needs_action(db, student, make_task):
    task = make_task(student.id, title="Удалить меня")
    response = ai._run_tool(
        tool_call("delete_tasks", {"task_queries": ["удалить меня"]}),
        student, db, TZ, "удали",
    )
    assert response.action == "confirm_delete"
    assert db.query(Task).filter(Task.id == task.id).count() == 1


def test_update_tool_rejects_teacher_assignment(db, student, make_task,
                                                make_assignment):
    assignment = make_assignment()
    make_task(
        student.id,
        title="Домашка от учителя",
        is_personal=False,
        assignment_id=assignment.id,
    )
    response = ai._run_tool(
        tool_call(
            "update_tasks",
            {"task_queries": ["домашка"], "updates": {"status": "done"}},
        ),
        student, db, TZ, "отметь выполненной",
    )
    assert "нельзя" in response.reply


# ---------------------------------------------------------------------------
# Подтверждённые действия
# ---------------------------------------------------------------------------


def test_apply_action_deletes_only_own_personal_tasks(db, student, student2,
                                                      make_task):
    mine = make_task(student.id, title="Моя")
    foreign = make_task(student2.id, title="Чужая")
    body = ai.ActionRequest(operation="delete", task_ids=[mine.id, foreign.id])
    response = ai.apply_action(body, current_user=student, db=db)
    assert response.data["count"] == 1
    assert db.query(Task).filter(Task.id == mine.id).count() == 0
    assert db.query(Task).filter(Task.id == foreign.id).count() == 1


def test_apply_action_create_sanitizes_input(db, student):
    body = ai.ActionRequest(
        operation="create",
        tasks=[{"title": "  Задача  ", "priority": "nonsense"}],
    )
    response = ai.apply_action(body, current_user=student, db=db)
    assert response.action == "task_created"
    task = db.query(Task).filter(Task.student_id == student.id).one()
    assert task.title == "Задача"
    assert task.priority == TaskPriority.medium


def test_apply_action_unknown_operation_is_rejected(db, student):
    body = ai.ActionRequest(operation="drop")
    with pytest.raises(HTTPException) as exc:
        ai.apply_action(body, current_user=student, db=db)
    assert exc.value.status_code == 400


def test_apply_action_records_confirmation_in_chat(db, student):
    chat = make_chat(db, student)
    body = ai.ActionRequest(
        operation="create",
        tasks=[{"title": "Новая"}],
        chat_id=chat.id,
    )
    ai.apply_action(body, current_user=student, db=db)
    rows = db.query(AIMessage).filter(AIMessage.chat_id == chat.id).all()
    assert [row.role for row in rows] == ["user", "assistant"]


# ---------------------------------------------------------------------------
# Эндпоинт /chat
# ---------------------------------------------------------------------------


def test_chat_stores_exchange_and_returns_chat_id(db, student, monkeypatch):
    async def fake(messages, tools=None, options=None):
        return {"content": "Привет!"}

    monkeypatch.setattr(ai, "_call_ollama", fake)
    result = asyncio.run(
        ai.chat(ai.ChatRequest(message="привет"), student, db)
    )
    assert result.reply == "Привет!"
    assert result.chat_id is not None
    rows = db.query(AIMessage).filter(AIMessage.chat_id == result.chat_id).all()
    assert [row.role for row in rows] == ["user", "assistant"]


def test_chat_runs_tool_calls_without_second_model_call(db, student,
                                                       monkeypatch):
    calls = []

    async def fake(messages, tools=None, options=None):
        calls.append(messages)
        return {"content": "", "tool_calls": [tool_call("build_plan", {"days": 1})]}

    monkeypatch.setattr(ai, "_call_ollama", fake)
    result = asyncio.run(
        ai.chat(ai.ChatRequest(message="что на неделе"), student, db)
    )
    assert len(calls) == 1
    assert result.action == "plan"
    assert "Расписание" in result.reply


def test_chat_passes_timezone_into_context(db, student, monkeypatch):
    seen = {}

    async def fake(messages, tools=None, options=None):
        seen["system"] = messages[0]["content"]
        return {"content": "ок"}

    monkeypatch.setattr(ai, "_call_ollama", fake)
    asyncio.run(
        ai.chat(
            ai.ChatRequest(message="привет", timezone="Asia/Novosibirsk"),
            student, db,
        )
    )
    assert "Asia/Novosibirsk" in seen["system"]


def test_build_context_lists_grades_tasks_and_schedule(db, student, teacher,
                                                       make_task):
    db.add(
        Grade(
            student_id=student.id,
            subject="Физика",
            value=5,
            graded_by_id=teacher.id,
        )
    )
    db.add(
        Schedule(
            student_id=student.id, subject="Физика", weekday=0,
            start_time="09:00", end_time="09:45",
        )
    )
    make_task(
        student.id,
        title="Лабораторная",
        subject="Физика",
        deadline=NOW + timedelta(days=1),
    )
    db.commit()

    context = ai._build_context(student, db, TZ)
    assert "Физика" in context
    assert "Лабораторная" in context
    assert "Расписание уроков" in context
    assert "Загруженность" in context


# ---------------------------------------------------------------------------
# Фантомные действия
# ---------------------------------------------------------------------------


def test_chat_retries_when_model_claims_action_without_tool(db, student,
                                                           monkeypatch):
    calls = []

    async def fake(messages, tools=None, options=None):
        calls.append(messages)
        if len(calls) == 1:
            return {"content": "Задачи добавлены!"}
        return {
            "content": "",
            "tool_calls": [tool_call("create_tasks", {"tasks": [{"title": "Новая"}]})],
        }

    monkeypatch.setattr(ai, "_call_ollama", fake)
    result = asyncio.run(
        ai.chat(ai.ChatRequest(message="добавь задачу полить цветы"), student, db)
    )
    assert len(calls) == 2
    assert result.action == "task_created"
    assert db.query(Task).filter(Task.student_id == student.id).count() == 1


def test_chat_admits_when_retry_also_has_no_tool(db, student, monkeypatch):
    async def fake(messages, tools=None, options=None):
        return {"content": "Задача добавлена!"}

    monkeypatch.setattr(ai, "_call_ollama", fake)
    result = asyncio.run(
        ai.chat(ai.ChatRequest(message="добавь задачу полить цветы"), student, db)
    )
    assert "не применились" in result.reply
    assert db.query(Task).filter(Task.student_id == student.id).count() == 0


def test_chat_does_not_retry_plain_answers(db, student, monkeypatch):
    calls = []

    async def fake(messages, tools=None, options=None):
        calls.append(messages)
        return {"content": "У тебя всё хорошо."}

    monkeypatch.setattr(ai, "_call_ollama", fake)
    asyncio.run(ai.chat(ai.ChatRequest(message="как дела"), student, db))
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Расписание считается без модели
# ---------------------------------------------------------------------------


def test_plan_request_skips_model(db, student, make_task, monkeypatch):
    """Маленькая модель путает дни недели, поэтому план собираем сами."""
    make_task(
        student.id,
        title="Кружок по физике",
        deadline=NOW + timedelta(days=1),
    )
    calls = []

    async def fake(messages, tools=None, options=None):
        calls.append(messages)
        return {"content": "сам придумаю"}

    monkeypatch.setattr(ai, "_call_ollama", fake)
    result = asyncio.run(
        ai.chat(
            ai.ChatRequest(message="Составь расписание на неделю", timezone="UTC"),
            student, db,
        )
    )
    assert calls == []
    assert result.action == "plan"
    assert "Кружки и доп. занятия" in result.reply


def test_plan_request_one_day_for_tomorrow(db, student, monkeypatch):
    calls = []

    async def fake(messages, tools=None, options=None):
        calls.append(messages)
        return {"content": "ок"}

    monkeypatch.setattr(ai, "_call_ollama", fake)
    result = asyncio.run(
        ai.chat(ai.ChatRequest(message="Какие у меня завтра уроки?"), student, db)
    )
    assert calls == []
    assert "Расписание на 1 день" in result.reply


def test_plan_request_detects_load_question(db, student):
    assert ai._plan_request_days("Где у меня перегруз?") is not None
    assert ai._plan_request_days("Какая нагрузка на неделе?") is not None
    assert ai._plan_request_days("Сколько задач я сделал?") is None
