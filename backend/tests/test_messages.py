"""Почта: список получателей и отправка.

Учётка без подтверждённой почты (``is_active=False, is_verified=False``)
остаётся человеком школы, а заблокированная админом
(``is_active=False, is_verified=True``) — нет.
"""
from app.models import UserRole
from app.routers import messages as messages_router
from app.routers.messages import MessageCreate


def _ids(people):
    return [person.id for person in people]


def test_people_include_unverified_student(db, teacher, make_user):
    pending = make_user(
        "pending@test.local",
        UserRole.student,
        is_active=False,
        is_verified=False,
    )
    assert pending.id in _ids(
        messages_router.list_people(current_user=teacher, db=db)
    )


def test_people_hide_blocked_student(db, teacher, make_user):
    blocked = make_user(
        "blocked@test.local",
        UserRole.student,
        is_active=False,
        is_verified=True,
    )
    assert blocked.id not in _ids(
        messages_router.list_people(current_user=teacher, db=db)
    )


def test_teacher_can_send_to_unverified_student(db, teacher, make_user):
    pending = make_user(
        "pending2@test.local",
        UserRole.student,
        is_active=False,
        is_verified=False,
    )
    message = messages_router.send_message(
        MessageCreate(recipient_id=pending.id, subject="Тема", body="Текст"),
        current_user=teacher,
        db=db,
    )
    assert message.recipient.id == pending.id
