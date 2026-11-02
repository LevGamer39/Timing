"""Согласование класса, оценки в профиле и задач по заданиям.

Ученик состоит ровно в одном классе, а ``User.grade`` — денормализованная
копия его названия. Раньше эти значения расходились: при выходе из класса
``grade`` оставался прежним, а ученик, вступивший в класс после создания
задания, не получал задачу. Обе операции собраны здесь.
"""
from sqlalchemy.orm import Session

from app.models import (
    Assignment,
    Class,
    ClassMembership,
    Task,
    TaskStatus,
    User,
    UserRole,
)


def refresh_student_grade(student_id: int, db: Session) -> None:
    """Пересчитывает ``User.grade`` по текущему членству в классе."""
    student = (
        db.query(User)
        .filter(User.id == student_id, User.role == UserRole.student)
        .first()
    )
    if not student:
        return

    membership = (
        db.query(ClassMembership)
        .filter(ClassMembership.student_id == student_id)
        .first()
    )
    if not membership:
        student.grade = None
        return

    class_ = db.query(Class).filter(Class.id == membership.class_id).first()
    student.grade = class_.name if class_ else None


def add_missing_assignment_tasks(
    student_id: int, class_id: int, db: Session
) -> int:
    """Создаёт задачи по уже существующим заданиям класса.

    Вызывается при вступлении ученика в класс, в том числе при переводе:
    задания, созданные до этого момента, иначе остались бы без его задачи.
    """
    assignments = (
        db.query(Assignment).filter(Assignment.class_id == class_id).all()
    )
    if not assignments:
        return 0

    existing = {
        row[0]
        for row in db.query(Task.assignment_id)
        .filter(
            Task.student_id == student_id,
            Task.assignment_id.isnot(None),
            Task.parent_task_id.is_(None),
        )
        .all()
    }

    created = 0
    for assignment in assignments:
        if assignment.id in existing:
            continue
        db.add(
            Task(
                student_id=student_id,
                assignment_id=assignment.id,
                title=assignment.title,
                subject=assignment.subject,
                description=assignment.description,
                deadline=assignment.deadline,
                priority=assignment.priority,
                is_personal=False,
                status=TaskStatus.pending,
            )
        )
        created += 1
    return created
