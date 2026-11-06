"""Статистика: срез дня, ожидание, динамика и вывод ИИ."""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.routers import stats
from app.stats import dynamics, summary, waiting

TZ = "Europe/Moscow"
UTC = timezone.utc
# 23:59 по Москве и полночь следующих суток — граница, на которой ломается
# подсчёт по UTC.
MSK_END = datetime(2026, 11, 5, 20, 59, tzinfo=UTC)
MSK_NEXT = datetime(2026, 11, 5, 21, 0, tzinfo=UTC)


def test_summary_counts_statuses(db, student, make_assignment, make_task):
    assignment = make_assignment()
    for status in ("pending", "done", "accepted", "overdue"):
        make_task(
            student.id,
            assignment_id=assignment.id,
            is_personal=False,
            deadline=MSK_END,
            status=getattr(stats.TaskStatus, status),
        )

    data = summary(db, student.id, date(2026, 11, 5), date(2026, 11, 5), TZ)

    assert data["total"] == 4
    assert data["in_work"] == 1
    assert data["done"] == 1
    assert data["accepted"] == 1
    assert data["waiting"] == 1
    assert data["week_ok"] is False


def test_personal_tasks_are_not_counted(
    db, student, make_assignment, make_task
):
    assignment = make_assignment()
    make_task(
        student.id,
        assignment_id=assignment.id,
        is_personal=False,
        deadline=MSK_END,
    )
    make_task(student.id, deadline=MSK_END, is_personal=True)

    data = summary(db, student.id, date(2026, 11, 5), date(2026, 11, 5), TZ)
    assert data["total"] == 1


def test_week_is_normal_only_when_nothing_hangs(
    db, student, make_assignment, make_task
):
    assignment = make_assignment()
    closed = make_task(
        student.id,
        assignment_id=assignment.id,
        is_personal=False,
        deadline=MSK_END,
        status=stats.TaskStatus.accepted,
    )
    closed.completed_at = MSK_END
    db.commit()
    assert summary(
        db, student.id, date(2026, 11, 5), date(2026, 11, 5), TZ
    )["week_ok"] is True

    make_task(
        student.id,
        assignment_id=assignment.id,
        is_personal=False,
        deadline=MSK_END,
        status=stats.TaskStatus.overdue,
    )
    assert summary(
        db, student.id, date(2026, 11, 5), date(2026, 11, 5), TZ
    )["week_ok"] is False


def test_day_bounds_use_local_time(db, student, make_assignment, make_task):
    assignment = make_assignment()
    make_task(
        student.id,
        assignment_id=assignment.id,
        is_personal=False,
        deadline=MSK_END,
    )
    make_task(
        student.id,
        assignment_id=assignment.id,
        is_personal=False,
        deadline=MSK_NEXT,
    )

    fifth = summary(db, student.id, date(2026, 11, 5), date(2026, 11, 5), TZ)
    sixth = summary(db, student.id, date(2026, 11, 6), date(2026, 11, 6), TZ)
    assert fifth["total"] == 1
    assert sixth["total"] == 1


def test_waiting_lists_unaccepted_and_counts_days(
    db, student, make_assignment, make_task
):
    assignment = make_assignment()
    make_task(
        student.id,
        assignment_id=assignment.id,
        is_personal=False,
        deadline=MSK_END,
        status=stats.TaskStatus.overdue,
    )
    make_task(
        student.id,
        assignment_id=assignment.id,
        is_personal=False,
        deadline=MSK_END,
        status=stats.TaskStatus.accepted,
    )

    items = waiting(
        db,
        student.id,
        date(2026, 11, 10),
        TZ,
        now=datetime(2026, 11, 10, 9, 0, tzinfo=UTC),
    )
    assert len(items) == 1
    assert items[0]["age_days"] == 5


def test_dynamics_groups_by_week(db, student, make_assignment, make_task):
    assignment = make_assignment()
    make_task(
        student.id,
        assignment_id=assignment.id,
        is_personal=False,
        subject="Math",
        deadline=datetime(2026, 10, 28, 12, 0, tzinfo=UTC),
        status=stats.TaskStatus.accepted,
    )
    make_task(
        student.id,
        assignment_id=assignment.id,
        is_personal=False,
        subject="Math",
        deadline=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
        status=stats.TaskStatus.overdue,
    )

    data = dynamics(db, student.id, weeks=3, tz_name=TZ)
    totals = {
        week["week_start"]: week["on_time"] + week["late"] + week["open"]
        for week in data["weeks"]
    }
    assert totals[date(2026, 10, 26)] == 1
    assert totals[date(2026, 11, 2)] == 1
    assert data["subjects"][0] == {"subject": "Math", "count": 2}


def test_parent_cannot_read_foreign_child(
    db, parent, student2
):
    with pytest.raises(HTTPException) as error:
        stats.daily_child(student2.id, parent, db)
    assert error.value.status_code == 403


def test_insight_is_generated_once(
    db, student, make_assignment, make_task, monkeypatch
):
    import asyncio

    assignment = make_assignment()
    task = make_task(
        student.id,
        assignment_id=assignment.id,
        is_personal=False,
        deadline=datetime.now(UTC) - timedelta(days=1),
    )
    task.completed_at = task.deadline
    db.commit()

    calls = []

    async def fake_call(messages, tools=None, options=None):
        calls.append(messages)
        return {"content": "Держи темп."}

    monkeypatch.setattr(stats, "_call_ollama", fake_call)

    first = asyncio.run(stats._insight(db, student.id))
    second = asyncio.run(stats._insight(db, student.id))

    assert first["cached"] is False
    assert second["cached"] is True
    assert len(calls) == 1


def test_insight_refuses_empty_history(db, student):
    import asyncio

    with pytest.raises(HTTPException) as error:
        asyncio.run(stats._insight(db, student.id))
    assert error.value.status_code == 404
