"""Оценки: ручная выставка, массовая выставка и сводки ученика."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import assert_parent_of, require_role
from app.enrollment import MAX_SUBJECT_LENGTH, clean_subject
from app.models import (
    Assignment,
    Class,
    ClassMembership,
    Grade,
    User,
    UserRole,
)

router = APIRouter(prefix="/api/grades", tags=["grades"])

MIN_GRADE = 1.0
MAX_GRADE = 5.0


# Схемы


class GradeCreate(BaseModel):
    student_id: int
    subject: str
    value: float = Field(..., ge=MIN_GRADE, le=MAX_GRADE)
    assignment_id: int | None = None
    comment: str | None = None

    @field_validator("subject")
    @classmethod
    def _subject_length(cls, value: str) -> str:
        subject = clean_subject(value)
        if subject is None:
            raise ValueError("Subject must not be blank")
        return subject


class BulkGradeItem(BaseModel):
    student_id: int
    value: float = Field(..., ge=MIN_GRADE, le=MAX_GRADE)
    comment: str | None = None


class BulkGradeCreate(BaseModel):
    assignment_id: int
    subject: str
    items: list[BulkGradeItem]

    @field_validator("subject")
    @classmethod
    def _subject_length(cls, value: str) -> str:
        subject = clean_subject(value)
        if subject is None:
            raise ValueError("Subject must not be blank")
        return subject


class GradeResponse(BaseModel):
    id: int
    student_id: int
    subject: str
    value: float
    assignment_id: int | None
    comment: str | None
    graded_by_id: int
    graded_at: datetime

    class Config:
        from_attributes = True


class SubjectAverage(BaseModel):
    subject: str
    average: float
    count: int


class GradeSummary(BaseModel):
    overall_average: float | None
    class_rank: int | None
    class_size: int | None
    best_subjects: list[SubjectAverage]
    weak_subjects: list[SubjectAverage]


# Хелперы


def _teacher_class_ids(teacher_id: int, db: Session) -> list[int]:
    return [
        row[0]
        for row in db.query(Class.id)
        .filter(Class.teacher_id == teacher_id)
        .all()
    ]


def _is_class_member(class_id: int, student_id: int, db: Session) -> bool:
    return (
        db.query(ClassMembership)
        .filter(
            ClassMembership.class_id == class_id,
            ClassMembership.student_id == student_id,
        )
        .first()
        is not None
    )


def _assert_teacher_can_grade(
    current_user: User,
    db: Session,
    student_id: int,
    assignment_id: int | None,
) -> None:
    if assignment_id:
        assignment = (
            db.query(Assignment)
            .filter(
                Assignment.id == assignment_id,
                Assignment.teacher_id == current_user.id,
            )
            .first()
        )
        if not assignment:
            raise HTTPException(404, "Assignment not found")
        if not _is_class_member(assignment.class_id, student_id, db):
            raise HTTPException(400, "Student is not assigned to this class")
        return

    class_ids = _teacher_class_ids(current_user.id, db)
    if not class_ids:
        raise HTTPException(403, "Student does not belong to your classes")

    membership = (
        db.query(ClassMembership)
        .filter(
            ClassMembership.student_id == student_id,
            ClassMembership.class_id.in_(class_ids),
        )
        .first()
    )
    if not membership:
        raise HTTPException(403, "Student does not belong to your classes")


def _upsert_grade(
    db: Session,
    *,
    student_id: int,
    subject: str,
    value: float,
    assignment_id: int | None,
    comment: str | None,
    graded_by_id: int,
) -> Grade:
    """Создаёт или обновляет оценку одного ученика.

    В схеме нет уникального ограничения на (student_id, assignment_id),
    поэтому остаётся схема «сначала поиск, потом запись». Стоит завести
    миграцию.
    """
    grade = None
    if assignment_id is not None:
        grade = (
            db.query(Grade)
            .filter(
                Grade.student_id == student_id,
                Grade.assignment_id == assignment_id,
            )
            .first()
        )

    if grade:
        grade.subject = subject
        grade.value = value
        grade.comment = comment
        grade.graded_by_id = graded_by_id
    else:
        grade = Grade(
            student_id=student_id,
            subject=subject,
            value=value,
            assignment_id=assignment_id,
            comment=comment,
            graded_by_id=graded_by_id,
        )
        db.add(grade)
    return grade


def _averages_by_subject(grades: list[Grade]) -> list[SubjectAverage]:
    buckets: dict[str, list[float]] = {}
    for grade in grades:
        buckets.setdefault(grade.subject, []).append(grade.value)
    return [
        SubjectAverage(
            subject=subject,
            average=round(sum(values) / len(values), 2),
            count=len(values),
        )
        for subject, values in buckets.items()
    ]


# Роуты


@router.post("", response_model=GradeResponse, status_code=201)
def add_grade(
    body: GradeCreate,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    _assert_teacher_can_grade(
        current_user, db, body.student_id, body.assignment_id
    )
    grade = _upsert_grade(
        db,
        student_id=body.student_id,
        subject=body.subject.strip()[:MAX_SUBJECT_LENGTH],
        value=body.value,
        assignment_id=body.assignment_id,
        comment=body.comment,
        graded_by_id=current_user.id,
    )
    db.commit()
    db.refresh(grade)
    return grade


@router.post("/bulk", response_model=list[GradeResponse], status_code=201)
def add_bulk_grades(
    body: BulkGradeCreate,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    """Записывает оценки всего класса за одно задание одним коммитом."""
    assignment = (
        db.query(Assignment)
        .filter(
            Assignment.id == body.assignment_id,
            Assignment.teacher_id == current_user.id,
        )
        .first()
    )
    if not assignment:
        raise HTTPException(404, "Assignment not found")

    seen: set[int] = set()
    touched: list[Grade] = []
    for item in body.items:
        if item.student_id in seen:
            raise HTTPException(
                400, f"Duplicate student_id in payload: {item.student_id}"
            )
        seen.add(item.student_id)

        _assert_teacher_can_grade(
            current_user, db, item.student_id, body.assignment_id
        )
        touched.append(
            _upsert_grade(
                db,
                student_id=item.student_id,
                subject=body.subject.strip()[:MAX_SUBJECT_LENGTH],
                value=item.value,
                assignment_id=body.assignment_id,
                comment=item.comment,
                graded_by_id=current_user.id,
            )
        )

    db.commit()
    for grade in touched:
        db.refresh(grade)
    # Только строки, записанные этим вызовом: раньше эндпоинт возвращал
    # все оценки задания, включая выставленные предыдущими запросами.
    return touched


@router.get("/my", response_model=list[GradeResponse])
def my_grades(
    subject: str | None = None,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    query = db.query(Grade).filter(Grade.student_id == current_user.id)
    if subject:
        query = query.filter(Grade.subject == subject)
    return query.order_by(Grade.graded_at.desc()).all()


@router.get("/my/averages", response_model=list[SubjectAverage])
def my_averages(
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Grade.subject, func.avg(Grade.value), func.count(Grade.id))
        .filter(Grade.student_id == current_user.id)
        .group_by(Grade.subject)
        .all()
    )
    return [
        SubjectAverage(
            subject=subject,
            average=round(float(average), 2),
            count=int(count),
        )
        for subject, average, count in rows
    ]


@router.get("/my/summary", response_model=GradeSummary)
def my_grade_summary(
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    grades = db.query(Grade).filter(Grade.student_id == current_user.id).all()
    subject_averages = _averages_by_subject(grades)
    subject_averages.sort(key=lambda item: (-item.average, item.subject.lower()))

    overall_average = (
        round(sum(grade.value for grade in grades) / len(grades), 2)
        if grades
        else None
    )

    class_rank = None
    class_size = None

    membership = (
        db.query(ClassMembership)
        .filter(ClassMembership.student_id == current_user.id)
        .order_by(ClassMembership.joined_at.desc())
        .first()
    )
    if membership:
        peer_ids = [
            row[0]
            for row in db.query(ClassMembership.student_id)
            .filter(ClassMembership.class_id == membership.class_id)
            .all()
        ]
        class_size = len(peer_ids)

        # Один сгруппированный запрос вместо запроса на каждого одноклассника.
        averages = (
            db.query(Grade.student_id, func.avg(Grade.value))
            .filter(Grade.student_id.in_(peer_ids))
            .group_by(Grade.student_id)
            .all()
        )
        scored = [
            (student_id, float(average))
            for student_id, average in averages
            if average is not None
        ]
        # Место считается по единой шкале для всех; при равенстве
        # лучшее место достаётся тому, кто раньше по id.
        scored.sort(key=lambda item: (-item[1], item[0]))
        for index, (student_id, _) in enumerate(scored, start=1):
            if student_id == current_user.id:
                class_rank = index
                break

    return GradeSummary(
        overall_average=overall_average,
        class_rank=class_rank,
        class_size=class_size,
        best_subjects=subject_averages[:4],
        weak_subjects=sorted(
            subject_averages, key=lambda item: (item.average, item.subject.lower())
        )[:3],
    )


@router.get("/assignment/{assignment_id}", response_model=list[GradeResponse])
def list_assignment_grades(
    assignment_id: int,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    assignment = (
        db.query(Assignment)
        .filter(
            Assignment.id == assignment_id,
            Assignment.teacher_id == current_user.id,
        )
        .first()
    )
    if not assignment:
        raise HTTPException(404, "Assignment not found")
    return (
        db.query(Grade)
        .filter(Grade.assignment_id == assignment_id)
        .order_by(Grade.graded_at.desc())
        .all()
    )


@router.get("/child/{student_id}", response_model=list[GradeResponse])
def child_grades(
    student_id: int,
    current_user: User = Depends(require_role(UserRole.parent)),
    db: Session = Depends(get_db),
):
    assert_parent_of(current_user.id, student_id, db)
    return (
        db.query(Grade)
        .filter(Grade.student_id == student_id)
        .order_by(Grade.graded_at.desc())
        .all()
    )


@router.delete("/{grade_id}", status_code=204)
def delete_grade(
    grade_id: int,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    """Удалить оценку может только тот учитель, который её выставил."""
    grade = (
        db.query(Grade)
        .filter(Grade.id == grade_id, Grade.graded_by_id == current_user.id)
        .first()
    )
    if not grade:
        raise HTTPException(404, "Grade not found")
    db.delete(grade)
    db.commit()