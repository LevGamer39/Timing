"""Админ-панель: инвайт-коды, пользователи и управление классами."""
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.enrollment import (
    add_missing_assignment_tasks,
    refresh_student_grade,
)
from app.models import Class, ClassMembership, InviteCode, User, UserRole

router = APIRouter(prefix="/api/admin", tags=["admin"])

MAX_NAME_LENGTH = 100


# ---------------------------------------------------------------------------
# Схемы
# ---------------------------------------------------------------------------


class InviteCodeResponse(BaseModel):
    id: int
    code: str
    is_used: bool
    used_by_id: int | None
    created_at: datetime

    class Config:
        from_attributes = True


class AdminTeacherResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str
    teacher_subject: str | None
    class_count: int


class AdminStudentResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str
    grade: str | None
    is_active: bool
    class_id: int | None
    class_name: str | None


class AdminClassResponse(BaseModel):
    id: int
    name: str
    invite_code: str
    teacher_id: int
    teacher_name: str
    student_count: int
    created_at: datetime


class AdminDashboardResponse(BaseModel):
    teachers_count: int
    students_count: int
    classes_count: int
    invite_codes_count: int
    classes: list[AdminClassResponse]
    teachers: list[AdminTeacherResponse]
    students: list[AdminStudentResponse]


class AdminClassCreate(BaseModel):
    name: str
    teacher_id: int
    student_ids: list[int] | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Class name must not be blank")
        if len(value) > MAX_NAME_LENGTH:
            raise ValueError(f"Name must be <= {MAX_NAME_LENGTH} characters")
        return value


class AdminClassUpdate(BaseModel):
    name: str | None = None
    teacher_id: int | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Class name must not be blank")
        if len(value) > MAX_NAME_LENGTH:
            raise ValueError(f"Name must be <= {MAX_NAME_LENGTH} characters")
        return value


class AdminClassStudentAdd(BaseModel):
    student_id: int


class AdminUserUpdate(BaseModel):
    is_active: bool | None = None
    grade: str | None = None
    teacher_subject: str | None = None


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _teacher_label(teacher: User | None) -> str:
    if not teacher:
        return "Не назначен"
    return f"{teacher.first_name} {teacher.last_name}".strip()


def _class_teacher_lookup(db: Session) -> dict[int, User]:
    return {
        user.id: user
        for user in db.query(User).filter(User.role == UserRole.teacher).all()
    }


def _class_student_counts(class_ids: list[int], db: Session) -> dict[int, int]:
    if not class_ids:
        return {}
    rows = (
        db.query(ClassMembership.class_id, func.count(ClassMembership.id))
        .filter(ClassMembership.class_id.in_(class_ids))
        .group_by(ClassMembership.class_id)
        .all()
    )
    return {class_id: int(count) for class_id, count in rows}


def _serialize_classes(
    classes: list[Class], db: Session
) -> list[AdminClassResponse]:
    teachers = _class_teacher_lookup(db)
    counts = _class_student_counts([class_.id for class_ in classes], db)
    return [
        AdminClassResponse(
            id=class_.id,
            name=class_.name,
            invite_code=class_.invite_code,
            teacher_id=class_.teacher_id,
            teacher_name=_teacher_label(teachers.get(class_.teacher_id)),
            student_count=counts.get(class_.id, 0),
            created_at=class_.created_at,
        )
        for class_ in classes
    ]


def _teacher_class_counts(teacher_ids: list[int], db: Session) -> dict[int, int]:
    if not teacher_ids:
        return {}
    rows = (
        db.query(Class.teacher_id, func.count(Class.id))
        .filter(Class.teacher_id.in_(teacher_ids))
        .group_by(Class.teacher_id)
        .all()
    )
    return {teacher_id: int(count) for teacher_id, count in rows}


def _collect_teachers(db: Session) -> list[AdminTeacherResponse]:
    teachers = (
        db.query(User)
        .filter(User.role == UserRole.teacher)
        .order_by(User.last_name.asc())
        .all()
    )
    counts = _teacher_class_counts([teacher.id for teacher in teachers], db)
    return [
        AdminTeacherResponse(
            id=teacher.id,
            first_name=teacher.first_name,
            last_name=teacher.last_name,
            email=teacher.email,
            teacher_subject=teacher.teacher_subject,
            class_count=counts.get(teacher.id, 0),
        )
        for teacher in teachers
    ]


def _student_class_lookup(db: Session) -> dict[int, tuple[int, str]]:
    """``{student_id: (class_id, class_name)}`` двумя запросами."""
    memberships = (
        db.query(ClassMembership.student_id, ClassMembership.class_id).all()
    )
    class_names = {
        row[0]: row[1] for row in db.query(Class.id, Class.name).all()
    }
    return {
        student_id: (class_id, class_names.get(class_id, ""))
        for student_id, class_id in memberships
    }


def _collect_students(
    db: Session, class_id: int | None = None
) -> list[AdminStudentResponse]:
    """Ученики вместе со своим классом; ``class_id`` фильтруется в SQL."""
    query = db.query(User).filter(User.role == UserRole.student)
    if class_id is not None:
        members = {
            row[0]
            for row in db.query(ClassMembership.student_id)
            .filter(ClassMembership.class_id == class_id)
            .all()
        }
        if not members:
            return []
        query = query.filter(User.id.in_(members))

    students = query.order_by(User.last_name.asc()).all()
    lookup = _student_class_lookup(db)

    response: list[AdminStudentResponse] = []
    for student in students:
        assigned = lookup.get(student.id)
        response.append(
            AdminStudentResponse(
                id=student.id,
                first_name=student.first_name,
                last_name=student.last_name,
                email=student.email,
                grade=student.grade,
                is_active=bool(student.is_active),
                class_id=assigned[0] if assigned else None,
                class_name=assigned[1] if assigned else None,
            )
        )
    return response


def _enroll(db: Session, class_id: int, student_id: int) -> None:
    """Привязывает ученика к классу, создавая membership только при отсутствии.

    Раньше существующая membership переназначалась, и ученик молча пропадал
    из своего прежнего класса.
    """
    existing = (
        db.query(ClassMembership)
        .filter(ClassMembership.student_id == student_id)
        .first()
    )
    if existing:
        if existing.class_id == class_id:
            return
        existing.class_id = class_id
    else:
        db.add(ClassMembership(student_id=student_id, class_id=class_id))

    db.flush()
    add_missing_assignment_tasks(student_id, class_id, db)
    refresh_student_grade(student_id, db)


# ---------------------------------------------------------------------------
# Инвайт-коды
# ---------------------------------------------------------------------------


@router.post("/invite-codes", response_model=InviteCodeResponse, status_code=201)
def generate_invite_code(
    current_user: User = Depends(require_role(UserRole.admin)),
    db: Session = Depends(get_db),
):
    # token_hex(8) -> 16 символов, как InviteCode.code String(16).
    code = secrets.token_hex(8).upper()
    invite = InviteCode(code=code, created_by_id=current_user.id)
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite


@router.get("/invite-codes", response_model=list[InviteCodeResponse])
def list_invite_codes(
    current_user: User = Depends(require_role(UserRole.admin)),
    db: Session = Depends(get_db),
):
    return (
        db.query(InviteCode)
        .order_by(InviteCode.created_at.desc())
        .limit(200)
        .all()
    )


@router.delete("/invite-codes/{code_id}", status_code=204)
def delete_invite_code(
    code_id: int,
    current_user: User = Depends(require_role(UserRole.admin)),
    db: Session = Depends(get_db),
):
    invite = db.query(InviteCode).filter(InviteCode.id == code_id).first()
    if not invite:
        raise HTTPException(404, "Invite code not found")
    if invite.is_used:
        raise HTTPException(400, "Cannot delete already used code")
    db.delete(invite)
    db.commit()


# ---------------------------------------------------------------------------
# Пользователи
# ---------------------------------------------------------------------------


@router.get("/teachers", response_model=list[AdminTeacherResponse])
def list_teachers(
    current_user: User = Depends(require_role(UserRole.admin)),
    db: Session = Depends(get_db),
):
    return _collect_teachers(db)


@router.get("/students", response_model=list[AdminStudentResponse])
def list_students(
    class_id: int | None = None,
    current_user: User = Depends(require_role(UserRole.admin)),
    db: Session = Depends(get_db),
):
    return _collect_students(db, class_id=class_id)


@router.patch("/users/{user_id}", response_model=AdminStudentResponse)
def update_student_account(
    user_id: int,
    body: AdminUserUpdate,
    current_user: User = Depends(require_role(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Активация учётной записи и правка предмета/класса.

    Раньше учителя возвращались как ``AdminStudentResponse`` с
    ``class_name="Учитель"``, а в ``grade`` лежал предмет. Теперь учителя
    отдают корректный ответ учителя.
    """
    user = (
        db.query(User)
        .filter(
            User.id == user_id,
            User.role.in_([UserRole.student, UserRole.teacher]),
        )
        .first()
    )
    if not user:
        raise HTTPException(404, "User not found")

    if body.is_active is not None:
        user.is_active = body.is_active
        # Повторная активация учётной записи означает и доверие к ней.
        if body.is_active and not user.is_verified:
            user.is_verified = True
    if body.grade is not None and user.role == UserRole.student:
        user.grade = body.grade
    if body.teacher_subject is not None and user.role == UserRole.teacher:
        user.teacher_subject = body.teacher_subject.strip() or None

    db.commit()
    db.refresh(user)

    if user.role == UserRole.teacher:
        counts = _teacher_class_counts([user.id], db)
        return AdminStudentResponse(
            id=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            email=user.email,
            grade=None,
            is_active=bool(user.is_active),
            class_id=None,
            class_name=f"Учитель · предметов: {counts.get(user.id, 0)}",
        )

    lookup = _student_class_lookup(db)
    assigned = lookup.get(user.id)
    return AdminStudentResponse(
        id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        grade=user.grade,
        is_active=bool(user.is_active),
        class_id=assigned[0] if assigned else None,
        class_name=assigned[1] if assigned else None,
    )


# ---------------------------------------------------------------------------
# Классы
# ---------------------------------------------------------------------------


@router.get("/classes", response_model=list[AdminClassResponse])
def list_classes(
    current_user: User = Depends(require_role(UserRole.admin)),
    db: Session = Depends(get_db),
):
    classes = db.query(Class).order_by(Class.name.asc()).all()
    return _serialize_classes(classes, db)


@router.post("/classes", response_model=AdminClassResponse, status_code=201)
def create_class(
    body: AdminClassCreate,
    current_user: User = Depends(require_role(UserRole.admin)),
    db: Session = Depends(get_db),
):
    teacher = (
        db.query(User)
        .filter(User.id == body.teacher_id, User.role == UserRole.teacher)
        .first()
    )
    if not teacher:
        raise HTTPException(404, "Teacher not found")

    class_ = Class(
        name=body.name,
        teacher_id=body.teacher_id,
        # token_hex(6) -> 12 символов, как Class.invite_code String(12).
        invite_code=secrets.token_hex(6).upper(),
    )
    db.add(class_)
    db.flush()

    for student_id in dict.fromkeys(body.student_ids or []):
        student = (
            db.query(User)
            .filter(User.id == student_id, User.role == UserRole.student)
            .first()
        )
        if not student:
            continue
        _enroll(db, class_.id, student_id)

    db.commit()
    db.refresh(class_)
    return _serialize_classes([class_], db)[0]


@router.patch("/classes/{class_id}", response_model=AdminClassResponse)
def update_class(
    class_id: int,
    body: AdminClassUpdate,
    current_user: User = Depends(require_role(UserRole.admin)),
    db: Session = Depends(get_db),
):
    class_ = db.query(Class).filter(Class.id == class_id).first()
    if not class_:
        raise HTTPException(404, "Class not found")

    if body.name is not None:
        class_.name = body.name
        db.flush()
        # grade — денормализованная копия названия класса.
        for row in (
            db.query(ClassMembership.student_id)
            .filter(ClassMembership.class_id == class_id)
            .all()
        ):
            refresh_student_grade(row[0], db)
    if body.teacher_id is not None:
        teacher = (
            db.query(User)
            .filter(User.id == body.teacher_id, User.role == UserRole.teacher)
            .first()
        )
        if not teacher:
            raise HTTPException(404, "Teacher not found")
        class_.teacher_id = body.teacher_id

    db.commit()
    db.refresh(class_)
    return _serialize_classes([class_], db)[0]


@router.post("/classes/{class_id}/students", response_model=AdminClassResponse)
def add_student_to_class(
    class_id: int,
    body: AdminClassStudentAdd,
    current_user: User = Depends(require_role(UserRole.admin)),
    db: Session = Depends(get_db),
):
    class_ = db.query(Class).filter(Class.id == class_id).first()
    if not class_:
        raise HTTPException(404, "Class not found")

    student = (
        db.query(User)
        .filter(User.id == body.student_id, User.role == UserRole.student)
        .first()
    )
    if not student:
        raise HTTPException(404, "Student not found")

    _enroll(db, class_id, body.student_id)
    db.commit()
    return _serialize_classes([class_], db)[0]


@router.delete("/classes/{class_id}/students/{student_id}", status_code=204)
def remove_student_from_class(
    class_id: int,
    student_id: int,
    current_user: User = Depends(require_role(UserRole.admin)),
    db: Session = Depends(get_db),
):
    if not db.query(Class).filter(Class.id == class_id).first():
        raise HTTPException(404, "Class not found")

    membership = (
        db.query(ClassMembership)
        .filter(
            ClassMembership.class_id == class_id,
            ClassMembership.student_id == student_id,
        )
        .first()
    )
    if not membership:
        raise HTTPException(404, "Student not found in class")
    db.delete(membership)
    db.flush()
    refresh_student_grade(student_id, db)
    db.commit()


@router.delete("/classes/{class_id}", status_code=204)
def delete_class(
    class_id: int,
    current_user: User = Depends(require_role(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Удаляет класс вместе с memberships и заданиями.

    Каскады объявлены в ``models.Class``; без них удаление падало по
    внешним ключам.
    """
    class_ = db.query(Class).filter(Class.id == class_id).first()
    if not class_:
        raise HTTPException(404, "Class not found")
    student_ids = [
        row[0]
        for row in db.query(ClassMembership.student_id)
        .filter(ClassMembership.class_id == class_id)
        .all()
    ]
    db.delete(class_)
    db.flush()
    for student_id in student_ids:
        refresh_student_grade(student_id, db)
    db.commit()


# ---------------------------------------------------------------------------
# Дашборд
# ---------------------------------------------------------------------------


@router.get("/dashboard", response_model=AdminDashboardResponse)
def dashboard(
    current_user: User = Depends(require_role(UserRole.admin)),
    db: Session = Depends(get_db),
):
    classes = db.query(Class).order_by(Class.name.asc()).all()
    teachers = _collect_teachers(db)
    students = _collect_students(db)
    return AdminDashboardResponse(
        teachers_count=len(teachers),
        students_count=len(students),
        classes_count=len(classes),
        invite_codes_count=db.query(func.count(InviteCode.id)).scalar() or 0,
        classes=_serialize_classes(classes, db),
        teachers=teachers,
        students=students,
    )