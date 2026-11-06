"""Статистика ученика: срез на день, неделя, динамика и вывод ИИ.

Считает сервер: бот и SPA показывают одни и те же цифры. Родитель ходит
через ``/child/{student_id}``, ученик — через ``/my``; сама логика общая.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.dependencies import assert_parent_of, require_role
from app.models import StatsSummary, TaskStatus, User, UserRole
from app.routers.ai import _call_ollama
from app.stats import dynamics as build_dynamics
from app.stats import summary as build_summary
from app.stats import waiting as build_waiting
from app.stats import week_bounds, zone

router = APIRouter(prefix="/api/stats", tags=["stats"])

MAX_WEEKS = 26
DEFAULT_WEEKS = 8

INSIGHT_PROMPT = (
    "Ты анализируешь учебную нагрузку школьника за несколько недель. "
    "На вход подряд идут агрегаты: сколько заданий закрыто в срок, позже "
    "срока и осталось открытыми, и по каким предметам. Напиши 2-3 коротких "
    "предложения: что меняется, без оценок личности, без сравнений с другими "
    "учениками и без слова «просрочено». В конце одно конкретное действие на "
    "следующую неделю."
)


class StatsTask(BaseModel):
    id: int
    title: str
    subject: str | None
    status: TaskStatus
    deadline: datetime | None
    age_days: int | None


class StatsSummaryResponse(BaseModel):
    date_from: date
    date_to: date
    total: int
    in_work: int
    done: int
    accepted: int
    waiting: int
    on_time: int
    late: int
    open: int
    week_ok: bool
    tasks: list[StatsTask]
    # Поля дневного среза; в недельном остаются пустыми.
    waiting_tasks: list[StatsTask] = []
    tomorrow: list[StatsTask] = []


class DynamicsWeek(BaseModel):
    week_start: date
    on_time: int
    late: int
    open: int


class DynamicsSubject(BaseModel):
    subject: str
    count: int


class DynamicsResponse(BaseModel):
    weeks: list[DynamicsWeek]
    subjects: list[DynamicsSubject]


class InsightResponse(BaseModel):
    week: date
    text: str
    cached: bool


def _today() -> date:
    return datetime.now(zone(config.TIMEZONE)).date()


def _daily(db: Session, student_id: int) -> dict:
    tz_name = config.TIMEZONE
    now = datetime.now(timezone.utc)
    today = now.astimezone(zone(tz_name)).date()
    tomorrow = today + timedelta(days=1)

    data = build_summary(db, student_id, today, today, tz_name, now=now)
    data["waiting_tasks"] = build_waiting(
        db, student_id, today, tz_name, now=now
    )
    data["tomorrow"] = build_summary(
        db, student_id, tomorrow, tomorrow, tz_name, now=now
    )["tasks"]
    return data


def _weekly(db: Session, student_id: int) -> dict:
    start, end = week_bounds(_today())
    return build_summary(db, student_id, start, end, config.TIMEZONE)


@router.get("/daily/my", response_model=StatsSummaryResponse)
def daily_my(
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    return _daily(db, current_user.id)


@router.get("/daily/child/{student_id}", response_model=StatsSummaryResponse)
def daily_child(
    student_id: int,
    current_user: User = Depends(require_role(UserRole.parent)),
    db: Session = Depends(get_db),
):
    assert_parent_of(current_user.id, student_id, db)
    return _daily(db, student_id)


@router.get("/weekly/my", response_model=StatsSummaryResponse)
def weekly_my(
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    return _weekly(db, current_user.id)


@router.get("/weekly/child/{student_id}", response_model=StatsSummaryResponse)
def weekly_child(
    student_id: int,
    current_user: User = Depends(require_role(UserRole.parent)),
    db: Session = Depends(get_db),
):
    assert_parent_of(current_user.id, student_id, db)
    return _weekly(db, student_id)


@router.get("/dynamics/my", response_model=DynamicsResponse)
def dynamics_my(
    weeks: int = Query(DEFAULT_WEEKS, ge=2, le=MAX_WEEKS),
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    return build_dynamics(db, current_user.id, weeks, config.TIMEZONE)


@router.get(
    "/dynamics/child/{student_id}", response_model=DynamicsResponse
)
def dynamics_child(
    student_id: int,
    weeks: int = Query(DEFAULT_WEEKS, ge=2, le=MAX_WEEKS),
    current_user: User = Depends(require_role(UserRole.parent)),
    db: Session = Depends(get_db),
):
    assert_parent_of(current_user.id, student_id, db)
    return build_dynamics(db, student_id, weeks, config.TIMEZONE)


async def _insight(db: Session, student_id: int) -> dict:
    """Вывод ИИ на текущую неделю: считается один раз и кэшируется."""
    monday = week_bounds(_today())[0]
    cached = (
        db.query(StatsSummary)
        .filter(
            StatsSummary.student_id == student_id,
            StatsSummary.week == monday,
        )
        .first()
    )
    if cached is not None:
        return {"week": monday, "text": cached.text, "cached": True}

    data = build_dynamics(db, student_id, DEFAULT_WEEKS, config.TIMEZONE)
    if not any(
        week["on_time"] + week["late"] + week["open"]
        for week in data["weeks"]
    ):
        raise HTTPException(404, "Not enough data yet")

    message = await _call_ollama(
        [
            {"role": "system", "content": INSIGHT_PROMPT},
            {
                "role": "user",
                "content": json.dumps(data, ensure_ascii=False, default=str),
            },
        ]
    )
    text = (message.get("content") or "").strip()
    if not text:
        raise HTTPException(503, "AI service is unavailable")

    db.add(StatsSummary(student_id=student_id, week=monday, text=text))
    try:
        db.commit()
    except IntegrityError:
        # Параллельный запрос успел первым — текст у него не хуже.
        db.rollback()
    return {"week": monday, "text": text, "cached": False}


@router.get("/insight/my", response_model=InsightResponse)
async def insight_my(
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    return await _insight(db, current_user.id)


@router.get("/insight/child/{student_id}", response_model=InsightResponse)
async def insight_child(
    student_id: int,
    current_user: User = Depends(require_role(UserRole.parent)),
    db: Session = Depends(get_db),
):
    assert_parent_of(current_user.id, student_id, db)
    return await _insight(db, student_id)
