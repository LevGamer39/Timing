"""Роутер задач: принадлежность, правила изменения, каскадное поведение."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.models import TaskStatus
from app.routers import tasks as tasks_router
from app.routers.tasks import TaskCreate, TaskUpdate

from .conftest import NOW


# ---------------------------------------------------------------------------
# Создание
# ---------------------------------------------------------------------------


def test_create_personal_task_trims_title(db, student):
    task = tasks_router.create_personal_task(
        TaskCreate(title="  My task  "), current_user=student, db=db
    )
    assert task.title == "My task"
    assert task.is_personal is True
    assert task.status == TaskStatus.pending


def test_create_task_rejects_blank_title():
    with pytest.raises(ValueError):
        TaskCreate(title="   ")


def test_create_task_rejects_overlong_title():
    with pytest.raises(ValueError):
        TaskCreate(title="x" * 300)


# ---------------------------------------------------------------------------
# Принадлежность
# ---------------------------------------------------------------------------


def test_other_student_cannot_read_task(db, student, student2, make_task):
    task = make_task(student.id, title="Private")
    with pytest.raises(HTTPException) as exc:
        tasks_router.get_task(task.id, current_user=student2, db=db)
    assert exc.value.status_code == 404


def test_other_student_cannot_delete_task(db, student, student2, make_task):
    task = make_task(student.id)
    with pytest.raises(HTTPException) as exc:
        tasks_router.delete_task(task.id, current_user=student2, db=db)
    assert exc.value.status_code == 404


def test_parent_can_read_child_tasks(
    db, parent, student, make_task, link_parent_child
):
    link_parent_child()
    make_task(student.id, title="Visible")
    tasks = tasks_router.get_child_tasks(student.id, current_user=parent, db=db)
    assert len(tasks) == 1


def test_parent_cannot_read_foreign_child(db, parent, student2):
    with pytest.raises(HTTPException) as exc:
        tasks_router.get_child_tasks(student2.id, current_user=parent, db=db)
    assert exc.value.status_code == 403


def test_list_tasks_hides_subtasks(db, student, make_task):
    parent = make_task(student.id, title="Main")
    make_task(student.id, title="Step", parent_task_id=parent.id)

    listed = tasks_router.list_tasks(current_user=student, db=db)
    assert [t.title for t in listed] == ["Main"]


def test_parent_list_hides_subtasks(
    db, parent, student, make_task, link_parent_child
):
    link_parent_child()
    main = make_task(student.id, title="Main")
    make_task(student.id, title="Step", parent_task_id=main.id)

    listed = tasks_router.get_child_tasks(student.id, current_user=parent, db=db)
    assert [t.title for t in listed] == ["Main"]


def test_list_tasks_filters_by_status(db, student, make_task):
    make_task(student.id, title="A", status=TaskStatus.pending)
    make_task(student.id, title="B", status=TaskStatus.done)

    listed = tasks_router.list_tasks(status=TaskStatus.done, current_user=student, db=db)
    assert [t.title for t in listed] == ["B"]


# ---------------------------------------------------------------------------
# Правила изменения
# ---------------------------------------------------------------------------


def test_student_cannot_edit_teacher_assignment(db, student, make_assignment, make_task):
    assignment = make_assignment()
    task = make_task(
        student.id, title="Homework", is_personal=False,
        assignment_id=assignment.id,
    )
    with pytest.raises(HTTPException) as exc:
        tasks_router.update_task(
            task.id, TaskUpdate(title="hacked"), current_user=student, db=db
        )
    assert exc.value.status_code == 403


def test_student_cannot_delete_teacher_assignment(db, student, make_assignment, make_task):
    assignment = make_assignment()
    task = make_task(
        student.id, is_personal=False, assignment_id=assignment.id
    )
    with pytest.raises(HTTPException) as exc:
        tasks_router.delete_task(task.id, current_user=student, db=db)
    assert exc.value.status_code == 400


def test_student_cannot_self_mark_overdue(db, student, make_task):
    task = make_task(student.id)
    updated = tasks_router.update_task(
        task.id, TaskUpdate(status=TaskStatus.overdue),
        current_user=student, db=db,
    )
    assert updated.status != TaskStatus.overdue


def test_update_sets_completed_at_when_marked_done(db, student, make_task):
    task = make_task(student.id)
    updated = tasks_router.update_task(
        task.id, TaskUpdate(status=TaskStatus.done),
        current_user=student, db=db,
    )
    assert updated.completed_at is not None


def test_update_clears_completed_at_when_reopened(db, student, make_task):
    task = make_task(student.id, status=TaskStatus.done)
    task.completed_at = datetime.now(timezone.utc)
    db.commit()

    updated = tasks_router.update_task(
        task.id, TaskUpdate(status=TaskStatus.in_progress),
        current_user=student, db=db,
    )
    assert updated.completed_at is None


def test_update_title_is_applied(db, student, make_task):
    task = make_task(student.id)
    updated = tasks_router.update_task(
        task.id, TaskUpdate(title="  Renamed  "),
        current_user=student, db=db,
    )
    assert updated.title == "Renamed"


# ---------------------------------------------------------------------------
# Завершение и каскадное удаление
# ---------------------------------------------------------------------------


def test_complete_task_cascades_to_subtasks(db, student, make_task):
    main = make_task(student.id, title="Main")
    sub = make_task(
        student.id, title="Step", parent_task_id=main.id,
        status=TaskStatus.pending,
    )
    done = tasks_router.complete_task(main.id, current_user=student, db=db)

    db.refresh(sub)
    assert done.status == TaskStatus.done
    assert sub.status == TaskStatus.done
    assert sub.completed_at is not None


def test_delete_task_cascades_to_subtasks(db, student, make_task):
    main = make_task(student.id)
    sub = make_task(student.id, title="Step", parent_task_id=main.id)

    tasks_router.delete_task(main.id, current_user=student, db=db)

    assert db.get(type(sub), sub.id) is None


def test_get_subtasks_returns_children(db, student, make_task):
    main = make_task(student.id)
    make_task(student.id, title="A", parent_task_id=main.id)
    make_task(student.id, title="B", parent_task_id=main.id)

    subs = tasks_router.get_subtasks(main.id, current_user=student, db=db)
    assert {t.title for t in subs} == {"A", "B"}