"""Роутер заданий: раскладка по ученикам, вычисляемые поля, каскадное удаление."""
import pytest
from fastapi import HTTPException

from app.models import ClassMembership, Task, TaskPriority, UserRole
from app.routers import assignments as assign_router
from app.routers.assignments import AssignmentCreate

from .conftest import NOW


@pytest.fixture()
def member(db, klass, student, student2):
    """Оба ученика записаны в класс учителя."""
    db.add(ClassMembership(student_id=student.id, class_id=klass.id))
    db.add(ClassMembership(student_id=student2.id, class_id=klass.id))
    db.commit()
    return klass


# ---------------------------------------------------------------------------
# Создание
# ---------------------------------------------------------------------------


def test_create_fans_out_one_task_per_class_member(
    db, teacher, member, student, student2
):
    created = assign_router.create_assignment(
        AssignmentCreate(
            title="Essay", deadline=NOW, class_id=member.id
        ),
        current_user=teacher, db=db,
    )
    assert created.total_count == 2
    assert created.submitted_count == 0
    assert created.class_name == "10A"


def test_created_tasks_are_not_personal(
    db, teacher, member, student
):
    assign_router.create_assignment(
        AssignmentCreate(
            title="Essay", deadline=NOW, class_id=member.id
        ),
        current_user=teacher, db=db,
    )
    tasks = db.query(Task).all()
    assert tasks
    assert all(task.is_personal is False for task in tasks)


def test_create_targets_only_requested_students(
    db, teacher, member, student, student2
):
    created = assign_router.create_assignment(
        AssignmentCreate(
            title="Essay", deadline=NOW, class_id=member.id,
            student_ids=[student.id],
        ),
        current_user=teacher, db=db,
    )
    assert created.total_count == 1


def test_create_rejects_students_outside_the_class(
    db, teacher, member, make_user
):
    outsider = make_user("outsider@test.local", UserRole.student)
    with pytest.raises(HTTPException) as exc:
        assign_router.create_assignment(
            AssignmentCreate(
                title="Essay", deadline=NOW, class_id=member.id,
                student_ids=[outsider.id],
            ),
            current_user=teacher, db=db,
        )
    assert exc.value.status_code == 400


def test_create_rejects_duplicate_student_ids():
    with pytest.raises(ValueError):
        AssignmentCreate(
            title="Essay", deadline=NOW, class_id=1, student_ids=[1, 1]
        )


def test_create_rejects_foreign_class(db, teacher, member, other_teacher):
    with pytest.raises(HTTPException) as exc:
        assign_router.create_assignment(
            AssignmentCreate(
                title="X", deadline=NOW, class_id=member.id
            ),
            current_user=other_teacher, db=db,
        )
    assert exc.value.status_code == 404


def test_create_requires_matching_subject(db, teacher, member):
    with pytest.raises(HTTPException) as exc:
        assign_router.create_assignment(
            AssignmentCreate(
                title="X", subject="Chemistry", deadline=NOW,
                class_id=member.id,
            ),
            current_user=teacher, db=db,
        )
    assert exc.value.status_code == 400


def test_create_falls_back_to_teacher_subject(db, teacher, member):
    created = assign_router.create_assignment(
        AssignmentCreate(
            title="X", deadline=NOW, class_id=member.id
        ),
        current_user=teacher, db=db,
    )
    assert created.subject == "Math"


def test_create_accepts_any_of_teacher_subjects(db, teacher, member):
    """Учитель ведёт несколько предметов — задание можно выдать по любому."""
    from app.models import TeacherSubject

    db.add(TeacherSubject(user_id=teacher.id, subject="Literature"))
    db.commit()
    db.refresh(teacher)

    created = assign_router.create_assignment(
        AssignmentCreate(
            title="Essay", subject="Literature", deadline=NOW,
            class_id=member.id,
        ),
        current_user=teacher, db=db,
    )
    assert created.subject == "Literature"


# ---------------------------------------------------------------------------
# Вычисляемые поля
# ---------------------------------------------------------------------------


def test_counts_exclude_ai_subtasks(
    db, teacher, member, student, make_assignment, make_task
):
    assignment = make_assignment(class_id=member.id)
    main = make_task(
        student.id, title="Homework", is_personal=False,
        assignment_id=assignment.id,
    )
    make_task(
        student.id, title="Step 1", parent_task_id=main.id,
        assignment_id=assignment.id,
    )

    item = assign_router.get_assignment(
        assignment.id, current_user=teacher, db=db
    )
    assert item.total_count == 1


def test_submitted_count_tracks_done_status(
    db, teacher, member, student, make_assignment, make_task
):
    from app.models import TaskStatus

    assignment = make_assignment(class_id=member.id)
    make_task(
        student.id, is_personal=False, assignment_id=assignment.id,
        status=TaskStatus.done,
    )
    item = assign_router.get_assignment(
        assignment.id, current_user=teacher, db=db
    )
    assert item.submitted_count == 1


def test_listing_carries_computed_counts(
    db, teacher, member, student, make_assignment, make_task
):
    assignment = make_assignment(class_id=member.id)
    make_task(student.id, is_personal=False, assignment_id=assignment.id)

    items = assign_router.list_assignments(current_user=teacher, db=db)
    found = next(i for i in items if i.id == assignment.id)
    assert found.total_count == 1
    assert found.class_name == "10A"


def test_foreign_teacher_cannot_read(db, make_assignment, other_teacher):
    assignment = make_assignment()
    with pytest.raises(HTTPException) as exc:
        assign_router.get_assignment(
            assignment.id, current_user=other_teacher, db=db
        )
    assert exc.value.status_code == 404


def test_foreign_teacher_cannot_delete(db, make_assignment, other_teacher):
    assignment = make_assignment()
    with pytest.raises(HTTPException) as exc:
        assign_router.delete_assignment(
            assignment.id, current_user=other_teacher, db=db
        )
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Каскадное удаление
# ---------------------------------------------------------------------------


def test_delete_cascades_to_tasks(
    db, teacher, student, make_assignment, make_task
):
    assignment = make_assignment()
    make_task(student.id, is_personal=False, assignment_id=assignment.id)

    assign_router.delete_assignment(
        assignment.id, current_user=teacher, db=db
    )
    assert db.query(Task).filter(
        Task.assignment_id == assignment.id
    ).count() == 0


def test_delete_cascades_to_subtasks(
    db, teacher, student, make_assignment, make_task
):
    assignment = make_assignment()
    main = make_task(
        student.id, is_personal=False, assignment_id=assignment.id
    )
    make_task(student.id, title="Step", parent_task_id=main.id)

    assign_router.delete_assignment(
        assignment.id, current_user=teacher, db=db
    )
    assert db.query(Task).filter(Task.id == main.id).count() == 0


def test_delete_cascades_to_grades(
    db, teacher, student, make_assignment, make_task
):
    from app.models import Grade

    assignment = make_assignment()
    make_task(student.id, is_personal=False, assignment_id=assignment.id)
    db.add(
        Grade(
            student_id=student.id, assignment_id=assignment.id,
            subject="Math", value=5.0, graded_by_id=teacher.id,
        )
    )
    db.commit()

    assign_router.delete_assignment(
        assignment.id, current_user=teacher, db=db
    )
    assert db.query(Grade).filter(
        Grade.assignment_id == assignment.id
    ).count() == 0


# ---------------------------------------------------------------------------
# Прогресс
# ---------------------------------------------------------------------------


def test_progress_classifies_every_member(
    db, teacher, member, student, student2, make_assignment, make_task
):
    from app.models import TaskStatus

    assignment = make_assignment(class_id=member.id)
    make_task(
        student.id, is_personal=False, assignment_id=assignment.id,
        status=TaskStatus.done,
    )
    make_task(
        student2.id, is_personal=False, assignment_id=assignment.id,
        status=TaskStatus.overdue,
    )

    progress = assign_router.get_assignment_progress(
        assignment.id, current_user=teacher, db=db
    )
    assert progress.completed == 1
    assert progress.started == 1
    assert progress.not_started == 0
    assert len(progress.students) == 2


def test_progress_counts_members_without_tasks(
    db, teacher, member, make_assignment
):
    assignment = make_assignment(class_id=member.id)

    progress = assign_router.get_assignment_progress(
        assignment.id, current_user=teacher, db=db
    )
    assert progress.not_started == 2
    assert progress.class_name == "10A"


def test_progress_includes_grade_when_present(
    db, teacher, member, student, make_assignment, make_task
):
    from app.models import Grade

    assignment = make_assignment(class_id=member.id)
    make_task(student.id, is_personal=False, assignment_id=assignment.id)
    db.add(
        Grade(
            student_id=student.id, assignment_id=assignment.id,
            subject="Math", value=4.0, graded_by_id=teacher.id,
        )
    )
    db.commit()

    progress = assign_router.get_assignment_progress(
        assignment.id, current_user=teacher, db=db
    )
    graded = next(s for s in progress.students if s.student_id == student.id)
    assert graded.grade == 4.0