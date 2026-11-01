"""Расписание занятий на неделю."""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models import (
    Class,
    ClassMembership,
    ClassSchedule,
    ParentStudent,
    Schedule,
    User,
    UserRole,
)

router = APIRouter(prefix="/api/schedule", tags=["schedule"])

_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

MAX_SUBJECT_LENGTH = 100
MAX_TEACHER_LENGTH = 255
MAX_ROOM_LENGTH = 50


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

    @field_validator("teacher_name")
    @classmethod
    def _teacher_length(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) > MAX_TEACHER_LENGTH:
            raise ValueError(
                f"Teacher name must be <= {MAX_TEACHER_LENGTH} characters"
            )
        return value or None

    @field_validator("room")
    @classmethod
    def _room_length(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) > MAX_ROOM_LENGTH:
            raise ValueError(
                f"Room must be <= {MAX_ROOM_LENGTH} characters"
            )
        return value or None


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


class ClassScheduleEntryCreate(ScheduleEntryCreate):
    teacher_id: int | None = None


class ClassScheduleEntryResponse(ScheduleEntryResponse):
    lesson_index: int
    teacher_id: int | None


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


def _class_access(class_id: int, user: User, db: Session) -> Class:
    """Класс доступен его классному руководителю или администратору."""
    query = db.query(Class).filter(Class.id == class_id)
    if user.role != UserRole.admin:
        query = query.filter(Class.teacher_id == user.id)
    class_ = query.first()
    if not class_:
        raise HTTPException(404, "Class not found")
    return class_


def _class_rows(class_id: int, db: Session) -> list[ClassSchedule]:
    return (
        db.query(ClassSchedule)
        .filter(ClassSchedule.class_id == class_id)
        .order_by(ClassSchedule.weekday, ClassSchedule.lesson_index)
        .all()
    )


def _student_class_id(student_id: int, db: Session) -> int | None:
    row = (
        db.query(ClassMembership.class_id)
        .filter(ClassMembership.student_id == student_id)
        .order_by(ClassMembership.joined_at.desc())
        .first()
    )
    return row[0] if row else None


def _ensure_class_schedule(class_id: int, db: Session) -> None:
    """Переносит личное расписание первого ученика в расписание класса.

    Пока классное расписание пусто, у учеников может быть своё (старые
    записи в ``schedule``). Чтобы учитель не начинал с пустой таблицы,
    один раз копируем его в ``class_schedule``.
    """
    if _class_rows(class_id, db):
        return
    member_id = (
        db.query(ClassMembership.student_id)
        .filter(ClassMembership.class_id == class_id)
        .order_by(ClassMembership.id)
        .first()
    )
    if not member_id:
        return
    rows = (
        db.query(Schedule)
        .filter(Schedule.student_id == member_id[0])
        .order_by(Schedule.weekday, Schedule.start_time)
        .all()
    )
    counters: dict[int, int] = {}
    for row in rows:
        index = counters.get(row.weekday, 0)
        counters[row.weekday] = index + 1
        db.add(
            ClassSchedule(
                class_id=class_id,
                weekday=row.weekday,
                lesson_index=index,
                subject=row.subject,
                teacher_name=row.teacher_name,
                start_time=row.start_time,
                end_time=row.end_time,
                room=row.room,
            )
        )
    if rows:
        db.commit()


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
    class_id = _student_class_id(current_user.id, db)
    if class_id is not None:
        rows = _class_rows(class_id, db)
        if rows:
            if weekday is not None:
                rows = [row for row in rows if row.weekday == weekday]
            return rows
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
    class_id = _student_class_id(student_id, db)
    if class_id is not None:
        rows = _class_rows(class_id, db)
        if rows:
            return rows
    return (
        db.query(Schedule)
        .filter(Schedule.student_id == student_id)
        .order_by(Schedule.weekday, Schedule.start_time)
        .all()
    )


@router.get(
    "/class/{class_id}",
    response_model=list[ClassScheduleEntryResponse],
)
def get_class_schedule(
    class_id: int,
    current_user: User = Depends(
        require_role(UserRole.teacher, UserRole.admin)
    ),
    db: Session = Depends(get_db),
):
    _class_access(class_id, current_user, db)
    _ensure_class_schedule(class_id, db)
    return _class_rows(class_id, db)


@router.put(
    "/class/{class_id}/{weekday}/{lesson_index}",
    response_model=ClassScheduleEntryResponse,
)
def save_class_lesson(
    class_id: int,
    weekday: int,
    lesson_index: int,
    body: ClassScheduleEntryCreate,
    current_user: User = Depends(
        require_role(UserRole.teacher, UserRole.admin)
    ),
    db: Session = Depends(get_db),
):
    _class_access(class_id, current_user, db)
    if not 0 <= weekday <= 6:
        raise HTTPException(400, "weekday must be between 0 and 6")
    if not 0 <= lesson_index <= 19:
        raise HTTPException(400, "lesson_index must be between 0 and 19")

    teacher_name = body.teacher_name
    if body.teacher_id is not None:
        teacher = (
            db.query(User).filter(User.id == body.teacher_id).first()
        )
        if not teacher:
            raise HTTPException(404, "Teacher not found")
        teacher_name = (
            f"{teacher.last_name} {teacher.first_name}".strip()
            or teacher.login
        )

    entry = (
        db.query(ClassSchedule)
        .filter(
            ClassSchedule.class_id == class_id,
            ClassSchedule.weekday == weekday,
            ClassSchedule.lesson_index == lesson_index,
        )
        .first()
    )
    if not entry:
        entry = ClassSchedule(
            class_id=class_id,
            weekday=weekday,
            lesson_index=lesson_index,
        )
        db.add(entry)
    entry.subject = body.subject
    entry.teacher_id = body.teacher_id
    entry.teacher_name = teacher_name
    entry.start_time = body.start_time
    entry.end_time = body.end_time
    entry.room = body.room
    db.commit()
    db.refresh(entry)
    return entry


@router.delete(
    "/class/{class_id}/{weekday}/{lesson_index}",
    status_code=204,
)
def delete_class_lesson(
    class_id: int,
    weekday: int,
    lesson_index: int,
    current_user: User = Depends(
        require_role(UserRole.teacher, UserRole.admin)
    ),
    db: Session = Depends(get_db),
):
    _class_access(class_id, current_user, db)
    entry = (
        db.query(ClassSchedule)
        .filter(
            ClassSchedule.class_id == class_id,
            ClassSchedule.weekday == weekday,
            ClassSchedule.lesson_index == lesson_index,
        )
        .first()
    )
    if not entry:
        raise HTTPException(404, "Lesson not found")
    db.delete(entry)
    db.commit()


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