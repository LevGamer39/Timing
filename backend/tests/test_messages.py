"""Почта: список получателей и отправка.

Учётка без подтверждённой почты (``is_active=False, is_verified=False``)
остаётся человеком школы, а заблокированная админом
(``is_active=False, is_verified=True``) — нет.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.models import MailFile, Message, UserRole
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


# ---------------------------------------------------------------------------
# Вложения: отмена добавления и уборка сирот
# ---------------------------------------------------------------------------


def _attachment(db, owner, tmp_path, token, created_at=None):
    path = tmp_path / token
    path.write_bytes(b"data")
    row = MailFile(
        token=token,
        name="f.txt",
        path=str(path),
        size=4,
        uploader_id=owner.id,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row, path


def test_delete_attachment_removes_file_and_row(db, teacher, tmp_path):
    row, path = _attachment(db, teacher, tmp_path, "a" * 48)

    messages_router.delete_attachment(row.token, current_user=teacher, db=db)

    assert (
        db.query(MailFile).filter(MailFile.token == row.token).first() is None
    )
    assert not path.exists()


def test_delete_attachment_requires_owner(
    db, teacher, other_teacher, tmp_path
):
    row, path = _attachment(db, teacher, tmp_path, "b" * 48)

    with pytest.raises(HTTPException) as exc:
        messages_router.delete_attachment(
            row.token, current_user=other_teacher, db=db
        )

    assert exc.value.status_code == 404
    assert path.exists()


def test_delete_attachment_keeps_attached_file(
    db, teacher, student, tmp_path
):
    row, path = _attachment(db, teacher, tmp_path, "c" * 48)
    db.add(
        Message(
            sender_id=teacher.id,
            recipient_id=student.id,
            subject="s",
            body="b",
            files=json.dumps([{"token": row.token, "name": "f.txt"}]),
        )
    )
    db.flush()

    with pytest.raises(HTTPException) as exc:
        messages_router.delete_attachment(
            row.token, current_user=teacher, db=db
        )

    assert exc.value.status_code == 409
    assert path.exists()


def test_cleanup_removes_only_stale_unattached(
    db, teacher, student, tmp_path
):
    old = datetime.now(timezone.utc) - timedelta(hours=25)
    orphan, orphan_path = _attachment(db, teacher, tmp_path, "d" * 48, old)
    fresh, fresh_path = _attachment(db, teacher, tmp_path, "e" * 48)
    attached, attached_path = _attachment(db, teacher, tmp_path, "f" * 48, old)
    db.add(
        Message(
            sender_id=teacher.id,
            recipient_id=student.id,
            subject="s",
            body="b",
            files=json.dumps([{"token": attached.token}]),
        )
    )
    db.flush()

    removed = messages_router.cleanup_orphan_attachments(db)

    assert removed == 1
    assert not orphan_path.exists()
    assert fresh_path.exists()
    assert attached_path.exists()
    assert (
        db.query(MailFile).filter(MailFile.token == orphan.token).first()
        is None
    )
