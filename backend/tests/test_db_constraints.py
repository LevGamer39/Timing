"""Ограничения целостности базы данных.

Проверяют, что сама база отвергает плохие данные. Ограничения объявлены в
``app.models`` и попадают в схему при её создании.
"""
import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    ClassMembership,
    Grade,
    TaskStatus,
    UserRole,
)


@pytest.fixture()
def enrolled(db, klass, student, student2):
    db.add(ClassMembership(student_id=student.id, class_id=klass.id))
    db.add(ClassMembership(student_id=student2.id, class_id=klass.id))
    db.commit()
    return klass


# Один класс на ученика


def test_student_cannot_join_two_classes(db, klass, student, teacher):
    from app.models import Class

    second = Class(
        name="9B", teacher_id=teacher.id, invite_code="BBBB222222"
    )
    db.add(second)
    db.commit()
    db.add(ClassMembership(student_id=student.id, class_id=klass.id))
    db.commit()

    db.add(ClassMembership(student_id=student.id, class_id=second.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_one_membership_per_class_is_allowed(db, klass, student, student2):
    db.add(ClassMembership(student_id=student.id, class_id=klass.id))
    db.add(ClassMembership(student_id=student2.id, class_id=klass.id))
    db.commit()
    assert db.query(ClassMembership).filter(
        ClassMembership.class_id == klass.id
    ).count() == 2


# Одна оценка на ученика на задание


def test_duplicate_grade_is_rejected(
    db, teacher, enrolled, student, make_assignment
):
    assignment = make_assignment()
    for value in (4.0, 5.0, 3.0):
        db.add(
            Grade(
                student_id=student.id,
                assignment_id=assignment.id,
                subject="Math",
                value=value,
                graded_by_id=teacher.id,
            )
        )

    # Все три сталкиваются по (student_id, assignment_id); первый commit падает.
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_standalone_grades_are_allowed(
    db, teacher, enrolled, student
):
    """NULL в assignment_id не должен сталкиваться сам с собой."""
    for value in (4.0, 5.0):
        db.add(
            Grade(
                student_id=student.id,
                assignment_id=None,
                subject="Math",
                value=value,
                graded_by_id=teacher.id,
            )
        )
        db.commit()
    assert db.query(Grade).filter(
        Grade.student_id == student.id, Grade.assignment_id.is_(None)
    ).count() == 2


def test_same_assignment_graded_for_different_students(
    db, teacher, enrolled, student, student2, make_assignment
):
    assignment = make_assignment()
    for target in (student, student2):
        db.add(
            Grade(
                student_id=target.id,
                assignment_id=assignment.id,
                subject="Math",
                value=4.0,
                graded_by_id=teacher.id,
            )
        )
        db.commit()
    assert db.query(Grade).filter(
        Grade.assignment_id == assignment.id
    ).count() == 2


# Диапазон оценок


@pytest.mark.parametrize("bad", [0.0, 5.1, 6.0, -1.0])
def test_out_of_range_grade_is_rejected(
    db, teacher, enrolled, student, bad
):
    db.add(
        Grade(
            student_id=student.id,
            subject="Math",
            value=bad,
            graded_by_id=teacher.id,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


@pytest.mark.parametrize("ok", [1.0, 3.5, 5.0])
def test_in_range_grade_is_accepted(db, teacher, enrolled, student, ok):
    db.add(
        Grade(
            student_id=student.id,
            subject="Math",
            value=ok,
            graded_by_id=teacher.id,
        )
    )
    db.commit()


# Внешние ключи


def test_task_requires_a_real_student(db):
    db.add(
        __import__("app.models", fromlist=["Task"]).Task(
            student_id=999999,
            title="Orphan",
            is_personal=True,
            status=TaskStatus.pending,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_notification_requires_a_real_user(db):
    from app.models import Notification, NotificationChannel

    db.add(
        Notification(
            user_id=999999,
            title="Orphan",
            body="x",
            channel=NotificationChannel.browser,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# Enum-типы


def test_role_is_a_native_enum(db, make_user):
    user = make_user("enum@test.local", UserRole.teacher)
    db.refresh(user)
    assert user.role == UserRole.teacher


def test_task_status_round_trips(db, student, make_task):
    task = make_task(student.id, status=TaskStatus.overdue)
    db.expire_all()
    from app.models import Task

    reloaded = db.get(Task, task.id)
    assert reloaded.status == TaskStatus.overdue