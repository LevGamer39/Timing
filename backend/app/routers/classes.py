"""Классы учителя и дашборд учителя."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.enrollment import (
    add_missing_assignment_tasks,
    refresh_student_grade,
)
from app.models import (
    Assignment,
    Class,
    ClassMembership,
    Grade,
    Task,
    TaskStatus,
    User,
    UserRole,
)

router = APIRouter(prefix="/api/classes", tags=["classes"])


# Схемы


class ClassResponse(BaseModel):
    """``student_count`` вычисляется, это не колонка в таблице ``classes``."""

    id: int
    name: str
    subject: str | None
    invite_code: str
    student_count: int
    created_at: datetime


class StudentResponse(BaseModel):
    id: int
    name: str
    email: str | None
    grade: str | None


class ClassOption(BaseModel):
    name: str


class StudentProgressRow(BaseModel):
    student_id: int
    name: str
    email: str | None
    status: TaskStatus | None
    score: float | None
    grade: float | None


class AssignmentOption(BaseModel):
    id: int
    title: str
    subject: str
    deadline: datetime


class ClassAssignmentMatrix(BaseModel):
    assignment_id: int
    title: str
    subject: str
    deadline: datetime
    submitted_count: int
    total_count: int


class StudentAssignmentCell(BaseModel):
    assignment_id: int
    status: TaskStatus | None
    grade: float | None


class ClassStudentDetail(BaseModel):
    id: int
    name: str
    email: str | None
    grade: str | None
    assignments: list[StudentAssignmentCell]


class ClassDetail(ClassResponse):
    students: list[StudentResponse]
    assignments: list[ClassAssignmentMatrix]


class DashboardStatistics(BaseModel):
    completed: int
    started: int
    not_started: int


class DashboardPagination(BaseModel):
    page: int
    page_size: int
    total_pages: int
    total_items: int


class TeacherDashboardResponse(BaseModel):
    class_id: int
    class_name: str
    class_subject: str | None
    subjects: list[str]
    selected_subject: str | None
    assignments: list[AssignmentOption]
    selected_assignment_id: int | None
    students: list[StudentProgressRow]
    statistics: DashboardStatistics
    pagination: DashboardPagination


# Вспомогательные функции


def _class_for_teacher(class_id: int, teacher_id: int, db: Session) -> Class:
    class_ = (
        db.query(Class)
        .filter(Class.id == class_id, Class.teacher_id == teacher_id)
        .first()
    )
    if not class_:
        raise HTTPException(404, "Class not found")
    return class_


def _student_label(student: User) -> str:
    return f"{student.last_name} {student.first_name}".strip()


def _score_from_grade(
    grade_value: float | None, status: TaskStatus | None
) -> float | None:
    """Приводит к шкале 0-100; оценки ограничены диапазоном 1-5."""
    if grade_value is not None:
        return round((grade_value / 5) * 100, 1)
    if status in {TaskStatus.done, TaskStatus.accepted}:
        return 100.0
    return None


def _member_ids(class_id: int, db: Session) -> list[int]:
    return [
        row[0]
        for row in db.query(ClassMembership.student_id)
        .filter(ClassMembership.class_id == class_id)
        .all()
    ]


def _students_by_id(student_ids: list[int], db: Session) -> dict[int, User]:
    """Забирает учеников одним запросом, а не через ленивую загрузку."""
    if not student_ids:
        return {}
    return {
        user.id: user
        for user in db.query(User).filter(User.id.in_(student_ids)).all()
    }


def _assignment_counts(
    assignment_ids: list[int], db: Session
) -> dict[int, tuple[int, int]]:
    """``{assignment_id: (сдано, всего)}``; только задачи верхнего уровня."""
    if not assignment_ids:
        return {}
    rows = (
        db.query(
            Task.assignment_id,
            func.count(Task.id),
            func.sum(case((Task.status == TaskStatus.done, 1), else_=0)),
        )
        .filter(
            Task.assignment_id.in_(assignment_ids),
            Task.parent_task_id.is_(None),
        )
        .group_by(Task.assignment_id)
        .all()
    )
    return {
        assignment_id: (int(done or 0), int(total or 0))
        for assignment_id, total, done in rows
    }


def _to_class_response(
    class_: Class, student_count: int
) -> ClassResponse:
    return ClassResponse(
        id=class_.id,
        name=class_.name,
        subject=class_.subject,
        invite_code=class_.invite_code,
        student_count=student_count,
        created_at=class_.created_at,
    )


def _class_counts(class_ids: list[int], db: Session) -> dict[int, int]:
    """``{class_id: число участников}`` для всего списка одним запросом."""
    if not class_ids:
        return {}
    rows = (
        db.query(ClassMembership.class_id, func.count(ClassMembership.id))
        .filter(ClassMembership.class_id.in_(class_ids))
        .group_by(ClassMembership.class_id)
        .all()
    )
    return {class_id: int(count) for class_id, count in rows}


# Роутеры


@router.get("", response_model=list[ClassResponse])
def list_classes(
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    classes = (
        db.query(Class)
        .filter(Class.teacher_id == current_user.id)
        .order_by(Class.name.asc())
        .all()
    )
    counts = _class_counts([class_.id for class_ in classes], db)
    return [
        _to_class_response(class_, counts.get(class_.id, 0)) for class_ in classes
    ]


@router.get("/available", response_model=list[ClassOption])
def list_available_class_names(
    current_user: User = Depends(require_role(UserRole.teacher, UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Различные названия классов, доступные вызывающему.

    Ограничены его собственными классами (для админа - всеми). Раньше любой
    авторизованный пользователь получал все названия классов из базы.
    """
    query = db.query(Class.name)
    if current_user.role != UserRole.admin:
        query = query.filter(Class.teacher_id == current_user.id)
    names = sorted({row[0] for row in query.all() if row[0]})
    return [ClassOption(name=name) for name in names]


@router.get("/dashboard", response_model=TeacherDashboardResponse)
def teacher_dashboard_data(
    class_id: int,
    assignment_id: int | None = None,
    subject: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    """Доска прогресса по классу.

    Список учеников возвращается всегда, даже если заданий ещё нет, - тогда
    ``status=None``. Раньше весь цикл был внутри ``if selected_assignment``,
    и только что созданный класс рендерился пустой таблицей.
    """
    class_ = _class_for_teacher(class_id, current_user.id, db)

    student_ids = _member_ids(class_id, db)
    students_by_id = _students_by_id(student_ids, db)

    all_assignments = (
        db.query(Assignment)
        .filter(Assignment.class_id == class_id)
        .order_by(Assignment.deadline.desc(), Assignment.created_at.desc())
        .all()
    )
    subjects = sorted(
        {assignment.subject for assignment in all_assignments if assignment.subject}
    )

    filtered_assignments = [
        assignment
        for assignment in all_assignments
        if not subject or assignment.subject == subject
    ]
    selected_assignment = None
    if assignment_id:
        selected_assignment = next(
            (
                assignment
                for assignment in filtered_assignments
                if assignment.id == assignment_id
            ),
            None,
        )
    if not selected_assignment and filtered_assignments:
        selected_assignment = filtered_assignments[0]

    student_rows: list[StudentProgressRow] = []
    completed = started = not_started = 0

    task_by_student: dict[int, Task] = {}
    grade_by_student: dict[int, Grade] = {}
    if selected_assignment:
        tasks = (
            db.query(Task)
            .filter(
                Task.assignment_id == selected_assignment.id,
                Task.parent_task_id.is_(None),
            )
            .all()
        )
        task_by_student = {task.student_id: task for task in tasks}
        grades = (
            db.query(Grade)
            .filter(Grade.assignment_id == selected_assignment.id)
            .all()
        )
        grade_by_student = {grade.student_id: grade for grade in grades}

    for student_id in student_ids:
        student = students_by_id.get(student_id)
        task = task_by_student.get(student_id)
        grade = grade_by_student.get(student_id)
        status = task.status if task else None

        if status in {TaskStatus.done, TaskStatus.accepted}:
            completed += 1
        elif status in {TaskStatus.in_progress, TaskStatus.overdue}:
            started += 1
        else:
            not_started += 1

        grade_value = grade.value if grade else None
        student_rows.append(
            StudentProgressRow(
                student_id=student_id,
                name=_student_label(student) if student else "Удалённый ученик",
                email=student.email if student else "",
                status=status,
                score=_score_from_grade(grade_value, status),
                grade=grade_value,
            )
        )

    student_rows.sort(key=lambda item: item.name.lower())
    total_items = len(student_rows)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    start = (page - 1) * page_size

    return TeacherDashboardResponse(
        class_id=class_.id,
        class_name=class_.name,
        class_subject=class_.subject,
        subjects=subjects,
        selected_subject=subject
        or (selected_assignment.subject if selected_assignment else None),
        assignments=[
            AssignmentOption(
                id=assignment.id,
                title=assignment.title,
                subject=assignment.subject,
                deadline=assignment.deadline,
            )
            for assignment in filtered_assignments
        ],
        selected_assignment_id=selected_assignment.id
        if selected_assignment
        else None,
        students=student_rows[start : start + page_size],
        statistics=DashboardStatistics(
            completed=completed,
            started=started,
            not_started=not_started,
        ),
        pagination=DashboardPagination(
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            total_items=total_items,
        ),
    )


@router.get("/{class_id}", response_model=ClassDetail)
def get_class(
    class_id: int,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    class_ = _class_for_teacher(class_id, current_user.id, db)

    student_ids = _member_ids(class_id, db)
    students_by_id = _students_by_id(student_ids, db)
    students = [
        StudentResponse(
            id=student_id,
            name=_student_label(students_by_id[student_id]),
            email=students_by_id[student_id].email,
            grade=students_by_id[student_id].grade,
        )
        for student_id in student_ids
        if student_id in students_by_id
    ]

    assignments = (
        db.query(Assignment)
        .filter(Assignment.class_id == class_id)
        .order_by(Assignment.deadline.desc())
        .all()
    )
    counts = _assignment_counts([a.id for a in assignments], db)
    matrix = [
        ClassAssignmentMatrix(
            assignment_id=assignment.id,
            title=assignment.title,
            subject=assignment.subject,
            deadline=assignment.deadline,
            submitted_count=counts.get(assignment.id, (0, 0))[0],
            total_count=counts.get(assignment.id, (0, 0))[1],
        )
        for assignment in assignments
    ]

    return ClassDetail(
        id=class_.id,
        name=class_.name,
        subject=class_.subject,
        invite_code=class_.invite_code,
        student_count=len(students),
        created_at=class_.created_at,
        students=students,
        assignments=matrix,
    )


@router.get("/{class_id}/overview", response_model=ClassDetail)
def get_class_overview(
    class_id: int,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    """Матрица «ученик x задание».

    Отдаёт те же типизированные данные, что и ``get_class``, вместо
    невалидируемого dict, собранного вручную.
    """
    class_ = _class_for_teacher(class_id, current_user.id, db)

    student_ids = _member_ids(class_id, db)
    students_by_id = _students_by_id(student_ids, db)

    assignments = (
        db.query(Assignment)
        .filter(Assignment.class_id == class_id)
        .order_by(Assignment.deadline.desc())
        .all()
    )
    assignment_ids = [assignment.id for assignment in assignments]

    task_lookup: dict[tuple[int, int], Task] = {}
    if assignment_ids:
        for task in (
            db.query(Task)
            .filter(
                Task.assignment_id.in_(assignment_ids),
                Task.parent_task_id.is_(None),
            )
            .all()
        ):
            task_lookup[(task.student_id, task.assignment_id)] = task

    grade_lookup: dict[tuple[int, int], Grade] = {}
    if assignment_ids:
        for grade in (
            db.query(Grade)
            .filter(Grade.assignment_id.in_(assignment_ids))
            .all()
        ):
            grade_lookup[(grade.student_id, grade.assignment_id)] = grade

    student_details: list[ClassStudentDetail] = []
    for student_id in student_ids:
        student = students_by_id.get(student_id)
        if not student:
            continue
        student_details.append(
            ClassStudentDetail(
                id=student.id,
                name=_student_label(student),
                email=student.email,
                grade=student.grade,
                assignments=[
                    StudentAssignmentCell(
                        assignment_id=assignment.id,
                        status=(
                            task_lookup[(student.id, assignment.id)].status
                            if (student.id, assignment.id) in task_lookup
                            else None
                        ),
                        grade=(
                            grade_lookup[(student.id, assignment.id)].value
                            if (student.id, assignment.id) in grade_lookup
                            else None
                        ),
                    )
                    for assignment in assignments
                ],
            )
        )

    student_details.sort(key=lambda item: item.name.lower())
    counts = _assignment_counts(assignment_ids, db)

    return ClassDetail(
        id=class_.id,
        name=class_.name,
        subject=class_.subject,
        invite_code=class_.invite_code,
        student_count=len(student_details),
        created_at=class_.created_at,
        students=[
            StudentResponse(
                id=item.id,
                name=item.name,
                email=item.email,
                grade=item.grade,
            )
            for item in student_details
        ],
        assignments=[
            ClassAssignmentMatrix(
                assignment_id=assignment.id,
                title=assignment.title,
                subject=assignment.subject,
                deadline=assignment.deadline,
                submitted_count=counts.get(assignment.id, (0, 0))[0],
                total_count=counts.get(assignment.id, (0, 0))[1],
            )
            for assignment in assignments
        ],
    )


@router.delete("/{class_id}/students/{student_id}", status_code=204)
def remove_student(
    class_id: int,
    student_id: int,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    _class_for_teacher(class_id, current_user.id, db)
    membership = (
        db.query(ClassMembership)
        .filter(
            ClassMembership.class_id == class_id,
            ClassMembership.student_id == student_id,
        )
        .first()
    )
    if not membership:
        raise HTTPException(404, "Student not in this class")
    student_id = membership.student_id
    db.delete(membership)
    db.flush()
    refresh_student_grade(student_id, db)
    db.commit()


@router.post("/join")
def join_class(
    invite_code: str,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    """Вступление в класс по инвайт-коду.

    Ученик принадлежит ровно одному классу (``User.grade`` хранит одно
    значение, и все запросы исходят из одной membership), поэтому вступление
    в другой класс переносит существующую membership. Прежнее название класса
    возвращается в ответе, а не теряется молча - раньше ``grade`` обновлялся
    на новый класс, а membership оставалась в старом.
    """
    class_ = (
        db.query(Class)
        .filter(Class.invite_code == invite_code.strip().upper())
        .first()
    )
    if not class_:
        raise HTTPException(404, "Invalid invite code")

    membership = (
        db.query(ClassMembership)
        .filter(ClassMembership.student_id == current_user.id)
        .first()
    )
    if membership and membership.class_id == class_.id:
        raise HTTPException(400, "Already in this class")

    previous_name = None
    if membership:
        previous = (
            db.query(Class)
            .filter(Class.id == membership.class_id)
            .first()
        )
        previous_name = previous.name if previous else None
        membership.class_id = class_.id
        membership.joined_at = datetime.now(timezone.utc)
    else:
        db.add(
            ClassMembership(student_id=current_user.id, class_id=class_.id)
        )

    db.flush()
    add_missing_assignment_tasks(current_user.id, class_.id, db)
    current_user.grade = class_.name
    db.commit()

    message = f"Joined class {class_.name}"
    if previous_name:
        message += f" (left {previous_name})"
    return {"message": message, "previous_class": previous_name}