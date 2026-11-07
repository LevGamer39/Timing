"""Задания учителя и прогресс по ним у каждого ученика."""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.dependencies import require_role
from app.enrollment import clean_subject
from app.models import (
    Assignment,
    Class,
    ClassMembership,
    Grade,
    NotificationChannel,
    Task,
    TaskPriority,
    TaskStatus,
    User,
    UserRole,
)
from app.routers.notifications import create_notification
from app.stats import day_bounds, zone

router = APIRouter(prefix="/api/assignments", tags=["assignments"])

MAX_TITLE_LENGTH = 255
MAX_DESCRIPTION_LENGTH = 10_000


# ---------------------------------------------------------------------------
# Схемы
# ---------------------------------------------------------------------------


class AssignmentCreate(BaseModel):
    title: str
    subject: str | None = None
    description: str | None = None
    deadline: datetime
    class_id: int
    priority: TaskPriority = TaskPriority.medium
    student_ids: list[int] | None = None
    files: list[str] = []  # токены MailFile, загруженных через /api/messages/attachments

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        if len(value) > MAX_TITLE_LENGTH:
            raise ValueError(f"Title must be <= {MAX_TITLE_LENGTH} characters")
        return value

    @field_validator("student_ids")
    @classmethod
    def _unique_students(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if len(set(value)) != len(value):
            raise ValueError("student_ids must not contain duplicates")
        return value

    @field_validator("subject")
    @classmethod
    def _subject_length(cls, value: str | None) -> str | None:
        return clean_subject(value)

    @field_validator("description")
    @classmethod
    def _description_length(cls, value: str | None) -> str | None:
        if value is not None and len(value) > MAX_DESCRIPTION_LENGTH:
            raise ValueError(
                f"Description must be <= {MAX_DESCRIPTION_LENGTH} characters"
            )
        return value


class AcceptBody(BaseModel):
    """Кого принять: без списка — всех, кто отметил «сделано»."""

    student_ids: list[int] | None = None
    accepted: bool = True


class AssignmentResponse(BaseModel):
    """``total_count`` / ``submitted_count`` / ``class_name`` вычисляются.

    Раньше их подвешивали к экземпляру ORM и полагались на
    ``from_attributes``; это молча ломалось, когда ответ пересобирался без
    патча. Теперь они передаются явно.
    """

    id: int
    title: str
    subject: str
    description: str | None
    deadline: datetime
    class_id: int
    priority: TaskPriority
    created_at: datetime
    submitted_count: int
    total_count: int
    class_name: str | None
    files: list[dict] = []


class AssignmentStudentProgress(BaseModel):
    student_id: int
    student_name: str
    email: str | None
    status: TaskStatus | None
    grade: float | None


class AssignmentProgressResponse(BaseModel):
    id: int
    title: str
    subject: str
    class_id: int
    class_name: str
    deadline: datetime
    priority: TaskPriority
    completed: int
    accepted: int
    started: int
    not_started: int
    students: list[AssignmentStudentProgress]


class WorkloadNearby(BaseModel):
    date: date
    count: int
    minutes: int


class WorkloadResponse(BaseModel):
    """Нагрузка класса на дату — подсказка, а не запрет."""

    date: date
    count: int
    minutes: int
    subjects: list[str]
    heavy: bool  # рядом крупная сдача: priority high/critical в день ±1
    nearby: list[WorkloadNearby]  # 7 следующих дней, от лёгких к тяжёлым


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _teacher_assignment(
    assignment_id: int, teacher_id: int, db: Session
) -> Assignment:
    assignment = (
        db.query(Assignment)
        .filter(
            Assignment.id == assignment_id,
            Assignment.teacher_id == teacher_id,
        )
        .first()
    )
    if not assignment:
        raise HTTPException(404, "Assignment not found")
    return assignment


def _class_name_lookup(teacher_id: int, db: Session) -> dict[int, str]:
    return {
        row[0]: row[1]
        for row in db.query(Class.id, Class.name)
        .filter(Class.teacher_id == teacher_id)
        .all()
    }


def _completion_counts(
    assignment_ids: list[int], db: Session
) -> dict[int, tuple[int, int]]:
    """``{assignment_id: (submitted, total)}`` одним сгруппированным запросом.

    Считаются только верхнеуровневые задачи: подзадачи ИИ копируют
    ``assignment_id`` и иначе завышали бы ``total_count``.
    """
    if not assignment_ids:
        return {}

    rows = (
        db.query(
            Task.assignment_id,
            func.count(Task.id),
            func.sum(
                case(
                    (
                        Task.status.in_(
                            [TaskStatus.done, TaskStatus.accepted]
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
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


def _to_response(
    assignment: Assignment,
    counts: tuple[int, int],
    class_name: str | None,
) -> AssignmentResponse:
    import json as _json

    def _parse(v):
        try:
            d = _json.loads(v) if v else []
            return [x for x in d if isinstance(x, dict) and x.get("token")] if isinstance(d, list) else []
        except Exception:
            return []

    submitted, total = counts
    return AssignmentResponse(
        id=assignment.id,
        title=assignment.title,
        subject=assignment.subject,
        description=assignment.description,
        deadline=assignment.deadline,
        class_id=assignment.class_id,
        priority=assignment.priority,
        created_at=assignment.created_at,
        submitted_count=submitted,
        total_count=total,
        class_name=class_name,
        files=_parse(getattr(assignment, "files", None)),
    )


# ---------------------------------------------------------------------------
# Роуты
# ---------------------------------------------------------------------------


@router.post("", response_model=AssignmentResponse, status_code=201)
def create_assignment(
    body: AssignmentCreate,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    class_ = (
        db.query(Class)
        .filter(Class.id == body.class_id, Class.teacher_id == current_user.id)
        .first()
    )
    if not class_:
        raise HTTPException(404, "Class not found")

    assigned = current_user.teacher_subjects
    subject = (body.subject or (assigned[0] if assigned else "")).strip()
    if not subject:
        raise HTTPException(400, "Teacher subject is not assigned")
    if assigned and subject.lower() not in {item.lower() for item in assigned}:
        raise HTTPException(
            400, "You can create assignments only for your assigned subjects"
        )

    available = {
        row[0]
        for row in db.query(ClassMembership.student_id)
        .filter(ClassMembership.class_id == body.class_id)
        .all()
    }
    if body.student_ids is not None:
        unknown = set(body.student_ids) - available
        if unknown:
            raise HTTPException(
                400, f"Students not in this class: {sorted(unknown)}"
            )
        student_ids = list(dict.fromkeys(body.student_ids))
    else:
        student_ids = sorted(available)

    assignment = Assignment(
        title=body.title,
        subject=subject,
        description=body.description,
        deadline=body.deadline,
        class_id=body.class_id,
        teacher_id=current_user.id,
        priority=body.priority,
    )
    if body.files:
        from app.models import MailFile
        import json as _json

        rows = db.query(MailFile).filter(
            MailFile.token.in_(body.files),
            MailFile.uploader_id == current_user.id,
        ).all()
        by_token = {r.token: r for r in rows}
        resolved = [
            {"token": by_token[t].token, "name": by_token[t].name, "size": by_token[t].size}
            for t in body.files if t in by_token
        ]
        assignment.files = _json.dumps(resolved, ensure_ascii=False) if resolved else None
    db.add(assignment)
    db.flush()

    for student_id in student_ids:
        db.add(
            Task(
                student_id=student_id,
                assignment_id=assignment.id,
                title=body.title,
                subject=subject,
                description=body.description,
                deadline=body.deadline,
                priority=body.priority,
                is_personal=False,
                status=TaskStatus.pending,
            )
        )

    db.commit()
    db.refresh(assignment)
    return _to_response(
        assignment,
        counts=(0, len(student_ids)),
        class_name=class_.name,
    )


@router.get("", response_model=list[AssignmentResponse])
def list_assignments(
    class_id: int | None = None,
    subject: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    query = db.query(Assignment).filter(Assignment.teacher_id == current_user.id)
    if class_id:
        query = query.filter(Assignment.class_id == class_id)
    if subject:
        query = query.filter(Assignment.subject == subject)
    if date_from:
        query = query.filter(Assignment.deadline >= date_from)
    if date_to:
        query = query.filter(Assignment.deadline <= date_to)

    assignments = query.order_by(Assignment.deadline.asc()).all()
    counts = _completion_counts(
        [assignment.id for assignment in assignments], db
    )
    names = _class_name_lookup(current_user.id, db)

    return [
        _to_response(
            assignment,
            counts=counts.get(assignment.id, (0, 0)),
            class_name=names.get(assignment.class_id),
        )
        for assignment in assignments
    ]


@router.get("/{assignment_id}", response_model=AssignmentResponse)
def get_assignment(
    assignment_id: int,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    assignment = _teacher_assignment(assignment_id, current_user.id, db)
    counts = _completion_counts([assignment.id], db)
    class_ = db.query(Class).filter(Class.id == assignment.class_id).first()
    return _to_response(
        assignment,
        counts=counts.get(assignment.id, (0, 0)),
        class_name=class_.name if class_ else None,
    )


@router.get("/{assignment_id}/progress", response_model=AssignmentProgressResponse)
def get_assignment_progress(
    assignment_id: int,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    assignment = _teacher_assignment(assignment_id, current_user.id, db)
    class_ = db.query(Class).filter(Class.id == assignment.class_id).first()

    memberships = (
        db.query(ClassMembership)
        .filter(ClassMembership.class_id == assignment.class_id)
        .all()
    )
    student_ids = [membership.student_id for membership in memberships]

    tasks = (
        db.query(Task)
        .filter(
            Task.assignment_id == assignment.id,
            Task.parent_task_id.is_(None),
        )
        .all()
    )
    grades = (
        db.query(Grade)
        .filter(Grade.assignment_id == assignment.id)
        .all()
    )

    task_by_student = {task.student_id: task for task in tasks}
    grade_by_student = {grade.student_id: grade for grade in grades}

    students: list[AssignmentStudentProgress] = []
    students_by_id: dict[int, User] = {}
    if student_ids:
        students_by_id = {
            user.id: user
            for user in db.query(User).filter(User.id.in_(student_ids)).all()
        }

    completed = accepted = started = not_started = 0
    for student_id in student_ids:
        student = students_by_id.get(student_id)
        task = task_by_student.get(student_id)
        grade = grade_by_student.get(student_id)
        status = task.status if task else None

        if status == TaskStatus.accepted:
            completed += 1
            accepted += 1
        elif status == TaskStatus.done:
            completed += 1
        elif status in {TaskStatus.in_progress, TaskStatus.overdue}:
            started += 1
        else:
            not_started += 1

        students.append(
            AssignmentStudentProgress(
                student_id=student_id,
                student_name=(
                    f"{student.last_name} {student.first_name}".strip()
                    if student
                    else "Удалённый ученик"
                ),
                email=student.email if student else "",
                status=status,
                grade=grade.value if grade else None,
            )
        )

    students.sort(key=lambda item: item.student_name.lower())

    return AssignmentProgressResponse(
        id=assignment.id,
        title=assignment.title,
        subject=assignment.subject,
        class_id=assignment.class_id,
        class_name=class_.name if class_ else "",
        deadline=assignment.deadline,
        priority=assignment.priority,
        completed=completed,
        accepted=accepted,
        started=started,
        not_started=not_started,
        students=students,
    )


@router.post(
    "/{assignment_id}/accept", response_model=AssignmentProgressResponse
)
def accept_assignment(
    assignment_id: int,
    body: AcceptBody | None = None,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    """Приём работ: «сделано» ученика становится «принято».

    ``accepted=false`` возвращает работу: иначе фальшивая отметка висела бы
    вечно и в статистике считалась бы подготовкой.
    """
    assignment = _teacher_assignment(assignment_id, current_user.id, db)
    payload = body or AcceptBody()

    query = db.query(Task).filter(
        Task.assignment_id == assignment.id,
        Task.parent_task_id.is_(None),
    )
    if payload.student_ids is not None:
        query = query.filter(Task.student_id.in_(payload.student_ids))

    now = datetime.now(timezone.utc)
    changed = []
    for task in query.all():
        if payload.accepted and task.status == TaskStatus.done:
            task.status = TaskStatus.accepted
            task.accepted_at = now
        elif not payload.accepted and task.status == TaskStatus.accepted:
            task.accepted_at = None
            task.status = (
                TaskStatus.overdue
                if task.deadline is not None and task.deadline < now
                else TaskStatus.in_progress
            )
        else:
            continue
        changed.append(task)
        # Событие шлём только на реальный переход статуса, поэтому
        # повторный приём того же задания ничего не дублирует.
        create_notification(
            db=db,
            user_id=task.student_id,
            title=(
                "Работа принята"
                if payload.accepted
                else "Работа возвращена"
            ),
            body=f"«{task.title}» — {assignment.subject}.",
            channel=NotificationChannel.max,
            commit=False,
        )

    if changed:
        db.commit()
    return get_assignment_progress(assignment_id, current_user, db)


# Пауза на одно задание: high/critical считаем крупной сдачей.
WORKLOAD_MINUTES = {
    TaskPriority.critical: 60,
    TaskPriority.high: 60,
    TaskPriority.medium: 30,
    TaskPriority.low: 15,
}
HEAVY_PRIORITIES = {TaskPriority.high, TaskPriority.critical}
WORKLOAD_DAYS = 7


@router.get("/workload/{class_id}", response_model=WorkloadResponse)
def class_workload(
    class_id: int,
    day: date = Query(alias="date"),
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    """Что уже задано классу на эту дату — подсказка в форме выдачи."""
    class_ = (
        db.query(Class)
        .filter(Class.id == class_id, Class.teacher_id == current_user.id)
        .first()
    )
    if not class_:
        raise HTTPException(404, "Class not found")

    tz_name = config.TIMEZONE
    first = day - timedelta(days=1)
    last = day + timedelta(days=WORKLOAD_DAYS + 1)
    start_utc, _ = day_bounds(first, tz_name)
    _, end_utc = day_bounds(last, tz_name)

    rows = (
        db.query(Assignment.deadline, Assignment.subject, Assignment.priority)
        .filter(
            Assignment.class_id == class_id,
            Assignment.deadline >= start_utc,
            Assignment.deadline < end_utc,
        )
        .all()
    )

    buckets: dict[date, list[tuple[str, TaskPriority]]] = {}
    for deadline, subject, priority in rows:
        local_day = deadline.astimezone(zone(tz_name)).date()
        buckets.setdefault(local_day, []).append((subject, priority))

    def day_stats(target: date) -> tuple[int, int]:
        items = buckets.get(target, [])
        minutes = sum(
            WORKLOAD_MINUTES.get(priority, 30) for _, priority in items
        )
        return len(items), minutes

    count, minutes = day_stats(day)
    heavy = any(
        priority in HEAVY_PRIORITIES
        for offset in (-1, 0, 1)
        for _, priority in buckets.get(day + timedelta(days=offset), [])
    )
    nearby: list[WorkloadNearby] = []
    for offset in range(WORKLOAD_DAYS):
        target = day + timedelta(days=offset)
        target_count, target_minutes = day_stats(target)
        nearby.append(
            WorkloadNearby(
                date=target, count=target_count, minutes=target_minutes
            )
        )
    nearby.sort(key=lambda item: (item.minutes, item.date))

    return WorkloadResponse(
        date=day,
        count=count,
        minutes=minutes,
        subjects=sorted({subject for subject, _ in buckets.get(day, [])}),
        heavy=heavy,
        nearby=nearby,
    )


@router.delete("/{assignment_id}", status_code=204)
def delete_assignment(
    assignment_id: int,
    current_user: User = Depends(require_role(UserRole.teacher)),
    db: Session = Depends(get_db),
):
    """Удаляет задание вместе с его задачами и оценками.

    Раньше удалялась только строка в ``assignments``, а задачи и оценки
    оставались с мёртвым ``assignment_id``; коммит падал на нарушении
    внешнего ключа, и API отдавал 500.
    """
    assignment = _teacher_assignment(assignment_id, current_user.id, db)
    db.delete(assignment)
    db.commit()