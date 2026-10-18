"""Расписание занятий на неделю."""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models import ParentStudent, Schedule, User, UserRole

router = APIRouter(prefix="/api/schedule", tags=["schedule"])

_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

MAX_SUBJECT_LENGTH = 100


def _validate_hhmm(value: str) -> str:
    """Приводит время к ``HH:MM`` с ведущими нулями либо отклоняет его.

    Колонка — ``String(5)``, а значение раньше писалось как есть, поэтому в БД
    могло оказаться ``"abc"`` или кривое ``"8:00"``, которое сортируется не
    так, как ``"10:00"``. Часы с одной цифрой принимаются на входе и
    дополняются нулями при записи.
    """
    value = value.strip()
    match = _HHMM_RE.match(value)
    if not match:
        raise ValueError("Time must be in HH:MM 24-hour format")
    return f"{int(match.group(1)):02d}:{match.group(2)}"


class ScheduleEntryCreate(BaseModel):
    subject: str
    teacher_name: str | None = None
    weekday: int
    start_time: str
    end_time: str
    room: str | None = None

    @field_validator("weekday")
    @classmethod
    def _weekday_range(cls, value: int) -> int:
        # 0 = понедельник ... 6 = воскресенье, как в models.Schedule.weekday.
        if not 0 <= value <= 6:
            raise ValueError("weekday must be between 0 (Mon) and 6 (Sun)")
        return value

    @field_validator("subject")
    @classmethod
    def _subject_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Subject must not be blank")
        if len(value) > MAX_SUBJECT_LENGTH:
            raise ValueError(f"Subject must be <= {MAX_SUBJECT_LENGTH} characters")
        return value

    @field_validator("start_time", "end_time")
    @classmethod
    def _valid_time(cls, value: str) -> str:
        return _validate_hhmm(value)


class ScheduleEntryResponse(BaseModel):
    id: int
    subject: str
    teacher_name: str | None
    weekday: int
    start_time: str
    end_time: str
    room: str | None

    class Config:
        from_attributes = True


def _own_entry(entry_id: int, student_id: int, db: Session) -> Schedule:
    entry = (
        db.query(Schedule)
        .filter(Schedule.id == entry_id, Schedule.student_id == student_id)
        .first()
    )
    if not entry:
        raise HTTPException(404, "Entry not found")
    return entry


def _assert_parent_of(parent_id: int, student_id: int, db: Session) -> None:
    link = (
        db.query(ParentStudent)
        .filter(
            ParentStudent.parent_id == parent_id,
            ParentStudent.student_id == student_id,
        )
        .first()
    )
    if not link:
        raise HTTPException(403, "Not your child")


@router.post("", response_model=ScheduleEntryResponse, status_code=201)
def add_entry(
    body: ScheduleEntryCreate,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    entry = Schedule(student_id=current_user.id, **body.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.get("/my", response_model=list[ScheduleEntryResponse])
def my_schedule(
    weekday: int | None = None,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    query = db.query(Schedule).filter(Schedule.student_id == current_user.id)
    if weekday is not None:
        query = query.filter(Schedule.weekday == weekday)
    return query.order_by(Schedule.weekday, Schedule.start_time).all()


@router.get("/child/{student_id}", response_model=list[ScheduleEntryResponse])
def child_schedule(
    student_id: int,
    current_user: User = Depends(require_role(UserRole.parent)),
    db: Session = Depends(get_db),
):
    _assert_parent_of(current_user.id, student_id, db)
    return (
        db.query(Schedule)
        .filter(Schedule.student_id == student_id)
        .order_by(Schedule.weekday, Schedule.start_time)
        .all()
    )


@router.put("/{entry_id}", response_model=ScheduleEntryResponse)
def update_entry(
    entry_id: int,
    body: ScheduleEntryCreate,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    entry = _own_entry(entry_id, current_user.id, db)
    for field, value in body.model_dump().items():
        setattr(entry, field, value)
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/{entry_id}", status_code=204)
def delete_entry(
    entry_id: int,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    entry = _own_entry(entry_id, current_user.id, db)
    db.delete(entry)
    db.commit()