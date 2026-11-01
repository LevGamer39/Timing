"""Посещаемость: отметки «Н» по урокам для класса и даты.

Журнальные «Н» живут отдельно (``JournalAbsence``): там отсутствие
привязано к предмету, а не к номеру урока. Экран «Мой класс» ставит
отметки по номеру урока, экран «Пропуски» только читает оба источника.
"""

from __future__ import annotations

from datetime import date as date_type, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models import (
    AttendanceMark,
    Class,
    ClassMembership,
    ClassSchedule,
    User,
    UserRole,
)

router = APIRouter(prefix="/api/attendance", tags=["attendance"])

MAX_LESSON_INDEX = 20


class AttendanceMarkIn(BaseModel):
    student_id: int
    lesson_index: int

    @field_validator("lesson_index")
    @classmethod
    def _index_range(cls, value: int) -> int:
        if not 1 <= value <= MAX_LESSON_INDEX:
            raise ValueError(
                f"lesson_index must be between 1 and {MAX_LESSON_INDEX}"
            )
        return value


class AttendanceSave(BaseModel):
    marks: list[AttendanceMarkIn] = []


class AttendanceMarkResponse(BaseModel):
    student_id: int
    date: date_type
    lesson_index: int
    subject: str | None

    class Config:
        from_attributes = True


def _class_access(class_id: int, user: User, db: Session) -> Class:
    query = db.query(Class).filter(Class.id == class_id)
    if user.role != UserRole.admin:
        query = query.filter(Class.teacher_id == user.id)
    class_ = query.first()
    if not class_:
        raise HTTPException(404, "Class not found")
    return class_


def _roster_ids(class_id: int, db: Session) -> set[int]:
    return {
        row[0]
        for row in db.query(ClassMembership.student_id)
        .filter(ClassMembership.class_id == class_id)
        .all()
    }


def _subject_for_slot(
    class_id: int, weekday: int, lesson_index: int, db: Session
) -> str | None:
    row = (
        db.query(ClassSchedule.subject)
        .filter(
            ClassSchedule.class_id == class_id,
            ClassSchedule.weekday == weekday,
            ClassSchedule.lesson_index == lesson_index - 1,
        )
        .first()
    )
    return row[0] if row else None


@router.get(
    "/class/{class_id}",
    response_model=list[AttendanceMarkResponse],
)
def list_class_attendance(
    class_id: int,
    date_from: date_type | None = Query(None, alias="from"),
    date_to: date_type | None = Query(None, alias="to"),
    current_user: User = Depends(
        require_role(UserRole.teacher, UserRole.admin)
    ),
    db: Session = Depends(get_db),
):
    _class_access(class_id, current_user, db)
    query = db.query(AttendanceMark).filter(
        AttendanceMark.class_id == class_id
    )
    if date_from:
        query = query.filter(AttendanceMark.date >= date_from)
    if date_to:
        query = query.filter(AttendanceMark.date <= date_to)
    return query.order_by(
        AttendanceMark.date, AttendanceMark.lesson_index
    ).all()


@router.put(
    "/class/{class_id}/{day}",
    response_model=list[AttendanceMarkResponse],
)
def save_class_attendance(
    class_id: int,
    day: date_type,
    body: AttendanceSave,
    current_user: User = Depends(
        require_role(UserRole.teacher, UserRole.admin)
    ),
    db: Session = Depends(get_db),
):
    """Заменяет отметки класса за день целиком.

    Экран присылает полный набор «кого нет» на дату, поэтому удаление
    одной отметки — это просто отсутствие пары в списке.
    """
    _class_access(class_id, current_user, db)
    roster = _roster_ids(class_id, db)

    seen: set[tuple[int, int]] = set()
    for mark in body.marks:
        if mark.student_id not in roster:
            raise HTTPException(400, "Student is not in this class")
        key = (mark.student_id, mark.lesson_index)
        if key in seen:
            raise HTTPException(400, "Duplicate student and lesson")
        seen.add(key)

    db.query(AttendanceMark).filter(
        AttendanceMark.class_id == class_id,
        AttendanceMark.date == day,
    ).delete(synchronize_session=False)

    weekday = datetime.combine(day, datetime.min.time()).weekday()
    for mark in body.marks:
        db.add(
            AttendanceMark(
                class_id=class_id,
                student_id=mark.student_id,
                date=day,
                lesson_index=mark.lesson_index,
                subject=_subject_for_slot(
                    class_id, weekday, mark.lesson_index, db
                ),
                marked_by_id=current_user.id,
            )
        )

    db.commit()
    return list_class_attendance(
        class_id, date_from=day, date_to=day,
        current_user=current_user, db=db,
    )
