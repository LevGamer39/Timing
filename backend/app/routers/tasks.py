"""CRUD задач ученика."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import assert_parent_of, require_role
from app.models import (
    Assignment,
    NotificationChannel,
    Task,
    TaskPriority,
    TaskStatus,
    User,
    UserRole,
)
from app.routers.notifications import create_notification

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

MAX_TITLE_LENGTH = 255
MAX_SUBJECT_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 10_000


class TaskCreate(BaseModel):
    title: str
    subject: str | None = None
    description: str | None = None
    deadline: datetime | None = None
    priority: TaskPriority = TaskPriority.medium
    parent_task_id: int | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        if len(value) > MAX_TITLE_LENGTH:
            raise ValueError(f"Title must be <= {MAX_TITLE_LENGTH} characters")
        return value

    @field_validator("subject")
    @classmethod
    def _subject_length(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if len(value) > MAX_SUBJECT_LENGTH:
            raise ValueError(
                f"Subject must be <= {MAX_SUBJECT_LENGTH} characters"
            )
        return value

    @field_validator("description")
    @classmethod
    def _description_length(cls, value: str | None) -> str | None:
        if value is not None and len(value) > MAX_DESCRIPTION_LENGTH:
            raise ValueError(
                f"Description must be <= {MAX_DESCRIPTION_LENGTH} characters"
            )
        return value


class TaskUpdate(BaseModel):
    title: str | None = None
    subject: str | None = None
    description: str | None = None
    deadline: datetime | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        if len(value) > MAX_TITLE_LENGTH:
            raise ValueError(f"Title must be <= {MAX_TITLE_LENGTH} characters")
        return value

    @field_validator("subject")
    @classmethod
    def _subject_length(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if len(value) > MAX_SUBJECT_LENGTH:
            raise ValueError(
                f"Subject must be <= {MAX_SUBJECT_LENGTH} characters"
            )
        return value

    @field_validator("description")
    @classmethod
    def _description_length(cls, value: str | None) -> str | None:
        if value is not None and len(value) > MAX_DESCRIPTION_LENGTH:
            raise ValueError(
                f"Description must be <= {MAX_DESCRIPTION_LENGTH} characters"
            )
        return value


class TaskResponse(BaseModel):
    """Повторяет таблицу ``tasks``.

    ``overdue`` проставляется планировщиком и недоступен клиентам, поэтому
    в ``TaskUpdate`` его намеренно нет среди пользовательских переходов.
    """

    id: int
    student_id: int
    title: str
    subject: str | None
    description: str | None
    deadline: datetime | None
    status: TaskStatus
    priority: TaskPriority
    is_personal: bool
    assignment_id: int | None
    parent_task_id: int | None
    ai_suggested_time: str | None
    created_at: datetime
    completed_at: datetime | None
    accepted_at: datetime | None
    files: list[dict] = []

    class Config:
        from_attributes = True


def _own_task(task_id: int, user: User, db: Session) -> Task:
    task = (
        db.query(Task)
        .filter(Task.id == task_id, Task.student_id == user.id)
        .first()
    )
    if not task:
        raise HTTPException(404, "Task not found")
    return task


def _assert_mutable(task: Task) -> None:
    """Запрещает правку задания, выданного учителем.

    Раньше ученик мог переименовать, перенести и переприоритизировать
    задание учителя; редактируются только его собственные личные задачи.
    """
    if not task.is_personal:
        raise HTTPException(
            403, "Cannot modify an assignment created by a teacher"
        )


def _sync_completion(task: Task) -> None:
    if task.status == TaskStatus.done:
        if task.completed_at is None:
            task.completed_at = datetime.now(timezone.utc)
    else:
        task.completed_at = None


def _base_task_query(student_id: int):
    """Только верхнеуровневые задачи: подзадачи ИИ исключены везде."""
    return Task.student_id == student_id, Task.parent_task_id.is_(None)


@router.post("", response_model=TaskResponse, status_code=201)
def create_personal_task(
    body: TaskCreate,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    task = Task(
        student_id=current_user.id,
        title=body.title,
        subject=body.subject,
        description=body.description,
        deadline=body.deadline,
        priority=body.priority,
        is_personal=True,
        status=TaskStatus.pending,
    )
    if body.parent_task_id is not None:
        parent = (
            db.query(Task)
            .filter(
                Task.id == body.parent_task_id,
                Task.student_id == current_user.id,
                Task.parent_task_id.is_(None),
            )
            .first()
        )
        if not parent:
            raise HTTPException(404, "Parent task not found")
        task.parent_task_id = parent.id
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.get("", response_model=list[TaskResponse])
def list_tasks(
    status: TaskStatus | None = None,
    subject: str | None = None,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    owner, top_level = _base_task_query(current_user.id)
    query = db.query(Task).filter(owner, top_level)
    if status:
        query = query.filter(Task.status == status)
    if subject:
        query = query.filter(Task.subject == subject)
    return query.order_by(Task.deadline.asc().nullslast()).all()


@router.get("/child/{student_id}", response_model=list[TaskResponse])
def get_child_tasks(
    student_id: int,
    current_user: User = Depends(require_role(UserRole.parent)),
    db: Session = Depends(get_db),
):
    """Объявлен раньше ``/{task_id}``, чтобы выиграл литеральный путь."""
    assert_parent_of(current_user.id, student_id, db)
    owner, top_level = _base_task_query(student_id)
    return (
        db.query(Task)
        .filter(owner, top_level)
        .order_by(Task.deadline.asc().nullslast())
        .all()
    )


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: int,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    return _own_task(task_id, current_user, db)


@router.get("/{task_id}/subtasks", response_model=list[TaskResponse])
def get_subtasks(
    task_id: int,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    _own_task(task_id, current_user, db)
    return (
        db.query(Task)
        .filter(Task.parent_task_id == task_id)
        .order_by(Task.id.asc())
        .all()
    )


@router.patch("/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: int,
    body: TaskUpdate,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    task = _own_task(task_id, current_user, db)
    _assert_mutable(task)

    updates = body.model_dump(exclude_unset=True)
    # Клиент не может сам проставить overdue (это делает планировщик)
    # и accepted (это делает учитель через /api/assignments/{id}/accept).
    updates.pop("status", None)
    if body.status is not None and body.status not in {
        TaskStatus.overdue,
        TaskStatus.accepted,
    }:
        task.status = body.status
    for field, value in updates.items():
        setattr(task, field, value)

    _sync_completion(task)
    db.commit()
    db.refresh(task)
    return task


def _notify_waiting(task: Task, db: Session) -> None:
    """Учителю: по заданию есть отмеченные работы, ждут проверки.

    Ключ дедупликации — час: тридцать учеников, отметивших одну работу,
    не должны превратиться в тридцать сообщений.
    """
    if task.assignment_id is None:
        return
    assignment = (
        db.query(Assignment)
        .filter(Assignment.id == task.assignment_id)
        .first()
    )
    if assignment is None:
        return
    create_notification(
        db=db,
        user_id=assignment.teacher_id,
        title="Ждут проверки",
        body=(
            f"«{assignment.title}» — {assignment.subject}: "
            "отмечены работы, которые ждут проверки."
        ),
        channel=NotificationChannel.max,
        dedupe_key=(
            f"waiting:{assignment.id}:{datetime.now(timezone.utc):%Y-%m-%d-%H}"
        ),
        commit=False,
    )


@router.post("/{task_id}/complete", response_model=TaskResponse)
def complete_task(
    task_id: int,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    """Выполнение задачи закрывает и её подзадачи."""
    task = _own_task(task_id, current_user, db)
    now = datetime.now(timezone.utc)

    task.status = TaskStatus.done
    task.completed_at = now
    _notify_waiting(task, db)

    subtasks = db.query(Task).filter(Task.parent_task_id == task.id).all()
    for subtask in subtasks:
        subtask.status = TaskStatus.done
        if subtask.completed_at is None:
            subtask.completed_at = now

    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=204)
def delete_task(
    task_id: int,
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    task = _own_task(task_id, current_user, db)
    if not task.is_personal:
        raise HTTPException(400, "Cannot delete teacher-assigned tasks")
    # Подзадачи удалятся каскадом через связь (см. models.Task.subtasks).
    db.delete(task)
    db.commit()