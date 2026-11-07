"""Журнал учителя: уроки, оценки, «Н» и итоговые за период.

Экран журнала в SPA работает с колонками-датами. В БД каждая колонка —
``JournalLesson``, оценка — обычная ``Grade`` со ссылкой на урок, «Н» —
``JournalAbsence``, итоговая — ``JournalFinal``. Так оценки журнала сразу
попадают в дневник и сводки ученика, а не живут отдельной копией.
"""

from __future__ import annotations

import math
from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.enrollment import clean_subject
from app.models import (
    Assignment,
    Class,
    ClassMembership,
    ClassSchedule,
    Grade,
    JournalAbsence,
    JournalFinal,
    JournalLesson,
    JournalWorkType,
    User,
    UserRole,
)

router = APIRouter(prefix="/api/journal", tags=["journal"])

MAX_HOMEWORK_LENGTH = 4000
MAX_COMMENT_LENGTH = 500
MIN_GRADE = 1.0
MAX_GRADE = 5.0
MAX_PERIOD_LENGTH = 60


# ---------------------------------------------------------------------------
# Схемы
# ---------------------------------------------------------------------------


class JournalStudent(BaseModel):
    id: int
    name: str


class JournalKey(BaseModel):
    class_id: int
    class_name: str
    subject: str


class JournalLessonModel(BaseModel):
    date: date_type
    slot: int = 0
    type: str = "lesson"
    homework: str = ""


class JournalMarkModel(BaseModel):
    student_id: int
    date: date_type
    slot: int = 0
    value: float
    comment: str | None = None


class JournalAbsenceModel(BaseModel):
    student_id: int
    date: date_type
    slot: int = 0


class JournalFinalModel(BaseModel):
    student_id: int
    period: str
    value: float


class JournalWorkTypeModel(BaseModel):
    code: str
    name: str
    weight: int

    class Config:
        from_attributes = True


class JournalResponse(BaseModel):
    class_id: int
    class_name: str
    subject: str
    students: list[JournalStudent]
    lessons: list[JournalLessonModel]
    marks: list[JournalMarkModel]
    absences: list[JournalAbsenceModel]
    finals: list[JournalFinalModel]
    work_types: list[JournalWorkTypeModel]
    # Дата -> работы с весом >= 2 по другим предметам этого класса.
    class_load: dict[str, int] = {}


class JournalLessonIn(BaseModel):
    date: date_type
    slot: int = 0
    type: str = "lesson"
    homework: str = ""

    @field_validator("slot")
    @classmethod
    def _slot_range(cls, value: int) -> int:
        if not 0 <= value <= 20:
            raise ValueError("Lesson slot must be between 0 and 20")
        return value

    @field_validator("homework")
    @classmethod
    def _homework_length(cls, value: str) -> str:
        value = value.strip()
        if len(value) > MAX_HOMEWORK_LENGTH:
            raise ValueError(
                f"Homework must be <= {MAX_HOMEWORK_LENGTH} characters"
            )
        return value


class JournalMarkIn(BaseModel):
    student_id: int
    date: date_type
    slot: int = 0
    value: float
    comment: str | None = None

    @field_validator("slot")
    @classmethod
    def _slot_range(cls, value: int) -> int:
        if not 0 <= value <= 20:
            raise ValueError("Lesson slot must be between 0 and 20")
        return value

    @field_validator("value")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Grade must be a finite number")
        return value

    @field_validator("comment")
    @classmethod
    def _comment_length(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) > MAX_COMMENT_LENGTH:
            raise ValueError(
                f"Comment must be <= {MAX_COMMENT_LENGTH} characters"
            )
        return value or None


class JournalAbsenceIn(BaseModel):
    student_id: int
    date: date_type
    slot: int = 0

    @field_validator("slot")
    @classmethod
    def _slot_range(cls, value: int) -> int:
        if not 0 <= value <= 20:
            raise ValueError("Lesson slot must be between 0 and 20")
        return value


class JournalFinalIn(BaseModel):
    student_id: int
    period: str
    value: float

    @field_validator("period")
    @classmethod
    def _period_length(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > MAX_PERIOD_LENGTH:
            raise ValueError("Period must be 1..60 characters")
        return value


class JournalSync(BaseModel):
    lessons: list[JournalLessonIn] = []
    marks: list[JournalMarkIn] = []
    absences: list[JournalAbsenceIn] = []
    finals: list[JournalFinalIn] = []


class WorkTypeCreate(BaseModel):
    code: str
    name: str
    weight: int = 1

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 40:
            raise ValueError("Code must be 1..40 characters")
        if not all(c.isalnum() or c in "-_" for c in value):
            raise ValueError("Code may contain letters, digits, - and _")
        return value

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 80:
            raise ValueError("Name must be 1..80 characters")
        return value

    @field_validator("weight")
    @classmethod
    def _weight(cls, value: int) -> int:
        if not 1 <= value <= 10:
            raise ValueError("Weight must be between 1 and 10")
        return value


class WorkTypeUpdate(BaseModel):
    name: str
    weight: int

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 80:
            raise ValueError("Name must be 1..80 characters")
        return value

    @field_validator("weight")
    @classmethod
    def _weight(cls, value: int) -> int:
        if not 1 <= value <= 10:
            raise ValueError("Weight must be between 1 and 10")
        return value


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _teacher_class(class_id: int, user: User, db: Session) -> Class:
    class_ = (
        db.query(Class)
        .filter(Class.id == class_id, Class.teacher_id == user.id)
        .first()
    )
    if not class_:
        raise HTTPException(404, "Class not found")
    return class_


def _journal_subject(value: str) -> str:
    """Предмет приходит путём в URL, поэтому Pydantic его не проверяет."""
    try:
        subject = clean_subject(value)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if subject is None:
        raise HTTPException(400, "Subject must not be blank")
    return subject


def _roster(class_id: int, db: Session) -> list[User]:
    student_ids = [
        row[0]
        for row in db.query(ClassMembership.student_id)
        .filter(ClassMembership.class_id == class_id)
        .order_by(ClassMembership.id)
        .all()
    ]
    if not student_ids:
        return []
    students = (
        db.query(User)
        .filter(User.id.in_(student_ids))
        .all()
    )
    by_id = {student.id: student for student in students}
    return [by_id[sid] for sid in student_ids if sid in by_id]


def _student_name(student: User) -> str:
    name = f"{student.last_name} {student.first_name}".strip()
    return name or student.login or f"Ученик {student.id}"


def _subjects_for_class(class_id: int, db: Session) -> list[str]:
    subjects: list[str] = []

    def add(value: str | None) -> None:
        value = (value or "").strip()
        if value and value not in subjects:
            subjects.append(value)

    for row in (
        db.query(ClassSchedule.subject)
        .filter(ClassSchedule.class_id == class_id)
        .order_by(ClassSchedule.weekday, ClassSchedule.lesson_index)
        .all()
    ):
        add(row[0])
    for row in (
        db.query(JournalLesson.subject)
        .filter(JournalLesson.class_id == class_id)
        .order_by(JournalLesson.date)
        .all()
    ):
        add(row[0])
    for row in (
        db.query(Assignment.subject)
        .filter(Assignment.class_id == class_id)
        .all()
    ):
        add(row[0])
    student_ids = [
        row[0]
        for row in db.query(ClassMembership.student_id)
        .filter(ClassMembership.class_id == class_id)
        .all()
    ]
    if student_ids:
        for row in (
            db.query(Grade.subject)
            .filter(Grade.student_id.in_(student_ids))
            .distinct()
            .all()
        ):
            add(row[0])
    return subjects


def _to_grade_value(raw: float) -> float:
    """Приводит оценку из журнала к шкале БД 1..5.

    SPA в серверном режиме работает по 100-балльной шкале, поэтому 80
    превращается в 4. Значения в пределах 1..5 остаются как есть.
    """
    value = float(raw)
    if value > MAX_GRADE:
        value = value / 20.0
    value = min(MAX_GRADE, max(MIN_GRADE, value))
    return round(value, 2)


def _lesson_type(code: str, known: set[str]) -> str:
    code = (code or "").strip()
    return code if code in known else "lesson"


def _work_types(db: Session) -> list[JournalWorkType]:
    return (
        db.query(JournalWorkType)
        .order_by(JournalWorkType.id)
        .all()
    )


# ---------------------------------------------------------------------------
# Роуты
# ---------------------------------------------------------------------------


@router.get("", response_model=list[JournalKey])
def list_journals(
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    """Классы и предметы, по которым у учителя есть журнал."""
    classes = (
        db.query(Class)
        .filter(Class.teacher_id == current_user.id)
        .order_by(Class.name.asc())
        .all()
    )
    result: list[JournalKey] = []
    for class_ in classes:
        for subject in _subjects_for_class(class_.id, db):
            result.append(
                JournalKey(
                    class_id=class_.id,
                    class_name=class_.name,
                    subject=subject,
                )
            )
    return result


@router.get("/work-types", response_model=list[JournalWorkTypeModel])
def list_work_types(
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    return _work_types(db)


@router.post(
    "/work-types",
    response_model=JournalWorkTypeModel,
    status_code=201,
)
def create_work_type(
    body: WorkTypeCreate,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    exists = (
        db.query(JournalWorkType)
        .filter(JournalWorkType.code == body.code)
        .first()
    )
    if exists:
        raise HTTPException(400, "Work type with this code already exists")
    item = JournalWorkType(
        code=body.code,
        name=body.name,
        weight=body.weight,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/work-types/{code}", response_model=JournalWorkTypeModel)
def update_work_type(
    code: str,
    body: WorkTypeUpdate,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    item = (
        db.query(JournalWorkType)
        .filter(JournalWorkType.code == code)
        .first()
    )
    if not item:
        raise HTTPException(404, "Work type not found")
    if item.code == "lesson" and body.weight != item.weight:
        raise HTTPException(400, "Default work type weight is fixed")
    item.name = body.name
    item.weight = body.weight
    db.commit()
    db.refresh(item)
    return item


@router.delete("/work-types/{code}", status_code=204)
def delete_work_type(
    code: str,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    if code == "lesson":
        raise HTTPException(400, "Default work type cannot be deleted")
    item = (
        db.query(JournalWorkType)
        .filter(JournalWorkType.code == code)
        .first()
    )
    if not item:
        raise HTTPException(404, "Work type not found")
    db.query(JournalLesson).filter(
        JournalLesson.type_code == code
    ).update({JournalLesson.type_code: "lesson"})
    db.delete(item)
    db.commit()


@router.get(
    "/{class_id}/{subject}",
    response_model=JournalResponse,
)
def get_journal(
    class_id: int,
    subject: str,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    class_ = _teacher_class(class_id, current_user, db)
    subject = _journal_subject(subject)

    students = _roster(class_id, db)
    student_ids = [student.id for student in students]

    lessons = (
        db.query(JournalLesson)
        .filter(
            JournalLesson.class_id == class_id,
            JournalLesson.subject == subject,
        )
        .order_by(JournalLesson.date, JournalLesson.slot)
        .all()
    )
    lesson_ids = [lesson.id for lesson in lessons]
    marks = (
        db.query(Grade)
        .filter(Grade.journal_lesson_id.in_(lesson_ids))
        .all()
        if lesson_ids
        else []
    )
    absences = (
        db.query(JournalAbsence)
        .filter(
            JournalAbsence.class_id == class_id,
            JournalAbsence.subject == subject,
        )
        .all()
    )
    finals = (
        db.query(JournalFinal)
        .filter(
            JournalFinal.student_id.in_(student_ids),
            JournalFinal.subject == subject,
        )
        .all()
        if student_ids
        else []
    )
    lesson_by_id = {lesson.id: lesson for lesson in lessons}
    # Нагрузка дня по другим предметам: с фронта сюда прибавляются работы
    # этого журнала, поэтому счётчик не устаревает при правках на месте.
    class_load = {
        day.isoformat(): int(count)
        for day, count in (
            db.query(JournalLesson.date, func.count(JournalLesson.id))
            .join(
                JournalWorkType,
                JournalWorkType.code == JournalLesson.type_code,
            )
            .filter(
                JournalLesson.class_id == class_id,
                JournalLesson.subject != subject,
                JournalWorkType.weight >= 2,
            )
            .group_by(JournalLesson.date)
            .all()
        )
    }

    return JournalResponse(
        class_id=class_.id,
        class_name=class_.name,
        subject=subject,
        students=[
            JournalStudent(id=s.id, name=_student_name(s)) for s in students
        ],
        lessons=[
            JournalLessonModel(
                date=lesson.date,
                slot=lesson.slot,
                type=lesson.type_code,
                homework=lesson.homework or "",
            )
            for lesson in lessons
        ],
        marks=[
            JournalMarkModel(
                student_id=grade.student_id,
                date=lesson_by_id[grade.journal_lesson_id].date,
                slot=lesson_by_id[grade.journal_lesson_id].slot,
                value=grade.value,
                comment=grade.comment,
            )
            for grade in marks
            if grade.journal_lesson_id in lesson_by_id
        ],
        absences=[
            JournalAbsenceModel(
                student_id=absence.student_id,
                date=absence.date,
                slot=absence.slot,
            )
            for absence in absences
        ],
        finals=[
            JournalFinalModel(
                student_id=final.student_id,
                period=final.period,
                value=final.value,
            )
            for final in finals
        ],
        work_types=[
            JournalWorkTypeModel(
                code=item.code,
                name=item.name,
                weight=item.weight,
            )
            for item in _work_types(db)
        ],
        class_load=class_load,
    )


@router.put("/{class_id}/{subject}", response_model=JournalResponse)
def save_journal(
    class_id: int,
    subject: str,
    body: JournalSync,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    """Полная синхронизация журнала: уроки, оценки, «Н» и итоговые.

    Клиент присылает снимок целиком, поэтому сервер приводит БД к нему:
    лишние уроки удаляются вместе с оценками, оценки перезаписываются.
    """
    class_ = _teacher_class(class_id, current_user, db)
    subject = _journal_subject(subject)

    students = _roster(class_id, db)
    student_ids = {student.id for student in students}

    known_types = {item.code for item in _work_types(db)}
    slots = {(lesson.date, lesson.slot) for lesson in body.lessons}
    if len(slots) != len(body.lessons):
        raise HTTPException(400, "Duplicate lesson slots in payload")

    for mark in body.marks:
        if mark.student_id not in student_ids:
            raise HTTPException(400, "Student is not in this class")
        if (mark.date, mark.slot) not in slots:
            raise HTTPException(400, "Grade date has no lesson")
    for absence in body.absences:
        if absence.student_id not in student_ids:
            raise HTTPException(400, "Student is not in this class")
        if (absence.date, absence.slot) not in slots:
            raise HTTPException(400, "Absence date has no lesson")
    for final in body.finals:
        if final.student_id not in student_ids:
            raise HTTPException(400, "Student is not in this class")

    # --- уроки -------------------------------------------------------------
    existing = {
        (lesson.date, lesson.slot): lesson
        for lesson in db.query(JournalLesson)
        .filter(
            JournalLesson.class_id == class_id,
            JournalLesson.subject == subject,
        )
        .all()
    }
    for key, lesson in existing.items():
        if key not in slots:
            db.delete(lesson)
    db.flush()

    lessons_by_key: dict[tuple[date_type, int], JournalLesson] = {}
    for item in body.lessons:
        lesson = existing.get((item.date, item.slot))
        if lesson is None:
            lesson = JournalLesson(
                class_id=class_id,
                subject=subject,
                date=item.date,
                slot=item.slot,
                created_by_id=current_user.id,
            )
            db.add(lesson)
        lesson.type_code = _lesson_type(item.type, known_types)
        lesson.homework = item.homework or None
        lessons_by_key[(item.date, item.slot)] = lesson
    db.flush()

    lesson_ids = [lesson.id for lesson in lessons_by_key.values()]

    # --- оценки ------------------------------------------------------------
    if lesson_ids:
        db.query(Grade).filter(
            Grade.journal_lesson_id.in_(lesson_ids)
        ).delete(synchronize_session=False)
    for mark in body.marks:
        lesson = lessons_by_key[(mark.date, mark.slot)]
        db.add(
            Grade(
                student_id=mark.student_id,
                journal_lesson_id=lesson.id,
                subject=subject,
                value=_to_grade_value(mark.value),
                comment=mark.comment,
                graded_by_id=current_user.id,
            )
        )

    # --- «Н» ---------------------------------------------------------------
    db.query(JournalAbsence).filter(
        JournalAbsence.class_id == class_id,
        JournalAbsence.subject == subject,
    ).delete(synchronize_session=False)
    for absence in body.absences:
        db.add(
            JournalAbsence(
                class_id=class_id,
                student_id=absence.student_id,
                subject=subject,
                date=absence.date,
                slot=absence.slot,
                marked_by_id=current_user.id,
            )
        )

    # --- итоговые ----------------------------------------------------------
    if student_ids:
        db.query(JournalFinal).filter(
            JournalFinal.student_id.in_(student_ids),
            JournalFinal.subject == subject,
        ).delete(synchronize_session=False)
    for final in body.finals:
        db.add(
            JournalFinal(
                student_id=final.student_id,
                subject=subject,
                period=final.period,
                value=_to_grade_value(final.value),
                updated_by_id=current_user.id,
            )
        )

    db.commit()
    return get_journal(class_id, subject, current_user, db)
