"""Импорт и экспорт учеников в JSON по строгой схеме.

Один сервер — одна школа: перенос между установками идёт файлом, а классы
на принимающей стороне остаются локальными. Оценки привязаны к ученику, а не
к классу, поэтому переезд не требует переносить журнал и не ломает историю.
Схема публикуется статикой: ``/static/schemas/student-migration.schema.json``.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.dependencies import require_role
from app.enrollment import clean_subject, enroll_student
from app.models import (
    Class,
    ClassMembership,
    Grade,
    JournalFinal,
    User,
    UserRole,
)

router = APIRouter(prefix="/api/admin/migration", tags=["migration"])

SCHEMA_PATH = "/static/schemas/student-migration.schema.json"
VERSION = 1
MAX_GRADE = 5.0
MAX_NAME_LENGTH = 100
MAX_PERIOD_LENGTH = 60
MAX_LOGIN_LENGTH = 255
MAX_EMAIL_LENGTH = 255


# ---------------------------------------------------------------------------
# Схемы документа
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    """Строгая база: лишние поля отвергаются, а не теряются молча."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class GradeEntry(_Strict):
    subject: str
    value: float = Field(..., ge=1, le=MAX_GRADE)
    comment: str | None = None
    graded_at: datetime | None = None

    @field_validator("subject")
    @classmethod
    def _subject(cls, value: str) -> str:
        subject = clean_subject(value)
        if subject is None:
            raise ValueError("Subject must be 1-100 characters")
        return subject


class FinalEntry(_Strict):
    subject: str
    period: str
    value: float = Field(..., ge=1, le=MAX_GRADE)

    @field_validator("subject")
    @classmethod
    def _subject(cls, value: str) -> str:
        subject = clean_subject(value)
        if subject is None:
            raise ValueError("Subject must be 1-100 characters")
        return subject

    @field_validator("period")
    @classmethod
    def _period(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > MAX_PERIOD_LENGTH:
            raise ValueError(
                f"Period must be 1-{MAX_PERIOD_LENGTH} characters"
            )
        return value


class StudentEntry(_Strict):
    login: str
    first_name: str
    last_name: str
    email: str | None = None
    class_name: str | None = None
    grades: list[GradeEntry] = Field(default_factory=list)
    finals: list[FinalEntry] = Field(default_factory=list)

    @field_validator("login")
    @classmethod
    def _login(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > MAX_LOGIN_LENGTH:
            raise ValueError(
                f"Login must be 1-{MAX_LOGIN_LENGTH} characters"
            )
        if any(char.isspace() for char in value):
            raise ValueError("Login must not contain spaces")
        return value

    @field_validator("first_name", "last_name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > MAX_NAME_LENGTH:
            raise ValueError(
                f"Name must be 1-{MAX_NAME_LENGTH} characters"
            )
        return value

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if "@" not in value or len(value) > MAX_EMAIL_LENGTH:
            raise ValueError("Email must contain @ and be <= 255 characters")
        return value

    @field_validator("class_name")
    @classmethod
    def _class_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value[:MAX_NAME_LENGTH] or None


class MigrationDocument(_Strict):
    schema_uri: str = Field(default="", alias="$schema")
    version: int
    exported_at: datetime | None = None
    students: list[StudentEntry]

    @field_validator("version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != VERSION:
            raise ValueError(f"Unsupported document version: {value}")
        return value

    @field_validator("students")
    @classmethod
    def _students(cls, value: list[StudentEntry]) -> list[StudentEntry]:
        if not value:
            raise ValueError("Document must contain at least one student")
        logins = [entry.login.lower() for entry in value]
        if len(set(logins)) != len(logins):
            raise ValueError("Duplicate login in document")
        return value


class ImportResult(BaseModel):
    created: int
    updated: int
    grades_added: int
    finals_saved: int
    classes_matched: list[str]
    unknown_classes: list[str]
    warnings: list[str]


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _schema_url() -> str:
    return (config.APP_URL or "").rstrip("/") + SCHEMA_PATH


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _utc(value: datetime | None) -> datetime | None:
    """Приводит время к UTC: сравнение дат не должно зависеть от tzinfo."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _class_by_name(db: Session) -> dict[str, int]:
    """``{имя класса строчными: id}``; при дублях имён берётся первое."""
    lookup: dict[str, int] = {}
    for class_id, name in db.query(Class.id, Class.name).all():
        lookup.setdefault(name.strip().lower(), class_id)
    return lookup


# ---------------------------------------------------------------------------
# Экспорт
# ---------------------------------------------------------------------------


@router.get("/students")
def export_students(
    class_id: int | None = None,
    current_user: User = Depends(require_role(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Выгружает всех учеников (или один класс) в JSON-документ."""
    query = db.query(User).filter(User.role == UserRole.student)
    if class_id is not None:
        members = {
            row[0]
            for row in db.query(ClassMembership.student_id)
            .filter(ClassMembership.class_id == class_id)
            .all()
        }
        if not members:
            members = {0}
        query = query.filter(User.id.in_(members))

    students = query.order_by(
        User.last_name.asc(), User.first_name.asc()
    ).all()
    ids = [student.id for student in students]

    class_names = {
        row[0]: row[1] for row in db.query(Class.id, Class.name).all()
    }
    membership = {
        row[0]: row[1]
        for row in db.query(
            ClassMembership.student_id, ClassMembership.class_id
        ).all()
    }

    grades: dict[int, list[Grade]] = {}
    finals: dict[int, list[JournalFinal]] = {}
    if ids:
        for grade in (
            db.query(Grade)
            .filter(Grade.student_id.in_(ids))
            .order_by(Grade.graded_at.asc())
            .all()
        ):
            grades.setdefault(grade.student_id, []).append(grade)
        for final in (
            db.query(JournalFinal)
            .filter(JournalFinal.student_id.in_(ids))
            .order_by(JournalFinal.subject.asc())
            .all()
        ):
            finals.setdefault(final.student_id, []).append(final)

    payload = {
        "$schema": _schema_url(),
        "version": VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "students": [
            {
                "login": student.login,
                "first_name": student.first_name,
                "last_name": student.last_name,
                "email": student.email,
                "class_name": class_names.get(membership.get(student.id)),
                "grades": [
                    {
                        "subject": grade.subject,
                        "value": grade.value,
                        "comment": grade.comment,
                        "graded_at": _iso(grade.graded_at),
                    }
                    for grade in grades.get(student.id, [])
                ],
                "finals": [
                    {
                        "subject": final.subject,
                        "period": final.period,
                        "value": final.value,
                    }
                    for final in finals.get(student.id, [])
                ],
            }
            for student in students
        ],
    }
    return JSONResponse(
        payload,
        headers={
            "Content-Disposition": 'attachment; filename="students.json"'
        },
    )


# ---------------------------------------------------------------------------
# Импорт
# ---------------------------------------------------------------------------


@router.post("/students", response_model=ImportResult)
def import_students(
    body: MigrationDocument,
    current_user: User = Depends(require_role(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Создаёт или дополняет учеников документом из другой установки.

    Повторный импорт того же файла ничего не дублирует: оценка ищется по
    ``(предмет, балл, комментарий, дата)``, итоговая — по ``(предмет,
    период)``. Класс берётся только из существующих локальных: новых не
    заводим, чтобы не выдумывать руководителя.
    """
    classes = _class_by_name(db)
    matched: set[str] = set()
    unknown: set[str] = set()
    warnings: list[str] = []
    created = updated = grades_added = finals_saved = 0

    for entry in body.students:
        student = (
            db.query(User)
            .filter(func.lower(User.login) == entry.login.lower())
            .first()
        )
        if student and student.role != UserRole.student:
            warnings.append(
                f"Логин {entry.login} занят не учеником — пропущен"
            )
            continue

        if student is None:
            student = User(
                login=entry.login,
                email=None,
                password_hash=None,
                first_name=entry.first_name,
                last_name=entry.last_name,
                role=UserRole.student,
                # Перенесённый аккаунт уже существовал: почту не подтверждаем.
                is_active=True,
                is_verified=True,
            )
            db.add(student)
            created += 1
        else:
            student.first_name = entry.first_name
            student.last_name = entry.last_name
            updated += 1
        db.flush()

        if entry.email and entry.email != student.email:
            taken = (
                db.query(User.id)
                .filter(User.email == entry.email, User.id != student.id)
                .first()
            )
            if taken:
                warnings.append(
                    f"{entry.login}: почта {entry.email} уже занята"
                )
            else:
                student.email = entry.email

        if entry.class_name:
            target = classes.get(entry.class_name.lower())
            if target is None:
                unknown.add(entry.class_name)
            else:
                matched.add(entry.class_name)
                enroll_student(db, target, student.id)

        existing = [
            (row.subject, row.value, row.comment, _utc(row.graded_at))
            for row in db.query(Grade)
            .filter(Grade.student_id == student.id)
            .all()
        ]
        for grade in entry.grades:
            # Без даты в файле сравниваем без неё: иначе повторный импорт
            # того же документа задублировал бы оценку.
            if grade.graded_at is None:
                duplicate = any(
                    subject == grade.subject
                    and value == grade.value
                    and comment == grade.comment
                    for subject, value, comment, _ in existing
                )
            else:
                duplicate = (
                    grade.subject,
                    grade.value,
                    grade.comment,
                    _utc(grade.graded_at),
                ) in existing
            if duplicate:
                continue
            graded_at = grade.graded_at or datetime.now(timezone.utc)
            existing.append(
                (grade.subject, grade.value, grade.comment, graded_at)
            )
            db.add(
                Grade(
                    student_id=student.id,
                    subject=grade.subject,
                    value=grade.value,
                    comment=grade.comment,
                    graded_at=graded_at,
                    graded_by_id=current_user.id,
                )
            )
            grades_added += 1

        finals = {
            (final.subject, final.period): final
            for final in db.query(JournalFinal)
            .filter(JournalFinal.student_id == student.id)
            .all()
        }
        for final in entry.finals:
            row = finals.get((final.subject, final.period))
            if row is None:
                row = JournalFinal(
                    student_id=student.id,
                    subject=final.subject,
                    period=final.period,
                    value=final.value,
                    updated_by_id=current_user.id,
                )
                db.add(row)
                finals[(final.subject, final.period)] = row
            else:
                row.value = final.value
                row.updated_by_id = current_user.id
            finals_saved += 1

    db.commit()
    return ImportResult(
        created=created,
        updated=updated,
        grades_added=grades_added,
        finals_saved=finals_saved,
        classes_matched=sorted(matched),
        unknown_classes=sorted(unknown),
        warnings=warnings,
    )
