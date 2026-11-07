"""Перенос учеников: экспорт и импорт JSON по строгой схеме."""
import json

import pytest
from pydantic import ValidationError

from app.models import (
    Class,
    ClassMembership,
    Grade,
    JournalFinal,
    User,
    UserRole,
)
from app.routers import migration
from app.routers.migration import MigrationDocument
from app.routers.users import create_access_token


def _document(**student) -> dict:
    entry = {
        "login": "ivanov.ivan",
        "first_name": "Иван",
        "last_name": "Иванов",
        "class_name": "10A",
        **student,
    }
    return {"version": 1, "students": [entry]}


def _import(db, admin, document):
    return migration.import_students(
        MigrationDocument.model_validate(document),
        current_user=admin,
        db=db,
    )


def test_import_creates_student_class_and_grades(db, admin, klass):
    result = _import(
        db,
        admin,
        _document(
            grades=[
                {
                    "subject": "Математика",
                    "value": 4,
                    "comment": None,
                    "graded_at": "2026-10-02T08:00:00+00:00",
                }
            ],
            finals=[
                {"subject": "Математика", "period": "1 четверть", "value": 5}
            ],
        ),
    )

    student = db.query(User).filter(User.login == "ivanov.ivan").one()
    assert result.created == 1 and result.updated == 0
    assert student.role == UserRole.student
    assert student.grade == klass.name
    membership = db.query(ClassMembership).filter_by(
        student_id=student.id
    ).one()
    assert membership.class_id == klass.id
    assert result.grades_added == 1
    assert db.query(Grade).filter_by(student_id=student.id).count() == 1
    assert db.query(JournalFinal).filter_by(student_id=student.id).count() == 1


def test_repeat_import_does_not_duplicate_grades(db, admin, klass):
    document = _document(
        grades=[{"subject": "Физика", "value": 5, "graded_at": None}]
    )
    _import(db, admin, document)
    again = _import(db, admin, document)

    assert again.created == 0
    assert again.grades_added == 0
    student = db.query(User).filter(User.login == "ivanov.ivan").one()
    assert db.query(Grade).filter_by(student_id=student.id).count() == 1


def test_import_moves_student_and_keeps_old_grades(
    db, admin, teacher, klass, student
):
    db.add(ClassMembership(student_id=student.id, class_id=klass.id))
    db.add(
        Grade(
            student_id=student.id,
            subject="История",
            value=4,
            graded_by_id=teacher.id,
        )
    )
    other = Class(name="10B", teacher_id=teacher.id, invite_code="BBBB111111")
    db.add(other)
    db.commit()

    result = _import(
        db,
        admin,
        {
            "version": 1,
            "students": [
                {
                    "login": student.login,
                    "first_name": "Иван",
                    "last_name": "Иванов",
                    "class_name": "10B",
                }
            ],
        },
    )

    moved = db.query(ClassMembership).filter_by(student_id=student.id).one()
    assert moved.class_id == other.id
    assert result.updated == 1 and result.created == 0
    # Оценка осталась при ученике: класс к ней не привязан.
    assert db.query(Grade).filter_by(student_id=student.id).count() == 1


def test_unknown_class_is_reported_not_invented(db, admin, klass):
    result = _import(db, admin, _document(class_name="11Z"))

    assert result.unknown_classes == ["11Z"]
    assert db.query(Class).filter(Class.name == "11Z").first() is None
    student = db.query(User).filter(User.login == "ivanov.ivan").one()
    membership = db.query(ClassMembership).filter_by(student_id=student.id)
    assert membership.first() is None


def test_export_publishes_schema_and_grades(db, admin, klass, student):
    db.add(ClassMembership(student_id=student.id, class_id=klass.id))
    db.add(
        Grade(
            student_id=student.id,
            subject="Химия",
            value=3,
            graded_by_id=admin.id,
        )
    )
    db.commit()

    response = migration.export_students(None, admin, db)
    payload = json.loads(response.body)

    assert payload["$schema"].endswith(migration.SCHEMA_PATH)
    assert payload["version"] == 1
    exported = payload["students"][0]
    assert exported["login"] == student.login
    assert exported["class_name"] == klass.name
    assert exported["grades"][0]["subject"] == "Химия"


def test_document_rejects_extra_fields_and_duplicate_logins():
    with pytest.raises(ValidationError):
        MigrationDocument.model_validate(
            _document(extra="не по схеме")
        )
    with pytest.raises(ValidationError):
        MigrationDocument.model_validate(
            {
                "version": 1,
                "students": [
                    {"login": "a", "first_name": "A", "last_name": "B"},
                    {"login": "A", "first_name": "C", "last_name": "D"},
                ],
            }
        )


def test_http_flow_serves_schema_and_guards_import(client, real_db):
    """Эндпоинты подключены, схема отдаётся статикой, чужая роль — 403."""
    teacher = User(
        login="http-teacher",
        email="http-teacher@test.dev",
        first_name="T",
        last_name="T",
        role=UserRole.teacher,
        is_active=True,
        is_verified=True,
        password_hash="x",
    )
    real_db.add(teacher)
    real_db.flush()
    real_db.add(
        Class(name="10A", teacher_id=teacher.id, invite_code="HHHH111111")
    )
    admin_user = User(
        login="http-admin",
        email="http-admin@test.dev",
        first_name="A",
        last_name="D",
        role=UserRole.admin,
        is_active=True,
        is_verified=True,
        password_hash="x",
    )
    real_db.add(admin_user)
    real_db.commit()

    headers = {"Authorization": "Bearer " + create_access_token(admin_user.id)}

    schema = client.get(migration.SCHEMA_PATH)
    assert schema.status_code == 200
    assert schema.json()["title"].startswith("Timing")

    body = {
        "$schema": "https://example.org/whatever.json",
        "version": 1,
        "students": [
            {
                "login": "http.student",
                "first_name": "Пётр",
                "last_name": "Петров",
                "class_name": "10A",
                "grades": [{"subject": "Математика", "value": 5}],
            }
        ],
    }
    created = client.post(
        "/api/admin/migration/students", json=body, headers=headers
    )
    assert created.status_code == 200, created.text
    assert created.json()["created"] == 1
    assert created.json()["classes_matched"] == ["10A"]

    again = client.post(
        "/api/admin/migration/students", json=body, headers=headers
    )
    assert again.json()["created"] == 0
    assert again.json()["grades_added"] == 0

    exported = client.get("/api/admin/migration/students", headers=headers)
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    payload = exported.json()
    student = next(
        s for s in payload["students"] if s["login"] == "http.student"
    )
    assert student["class_name"] == "10A"
    assert student["grades"][0]["value"] == 5.0
    assert payload["$schema"].endswith(migration.SCHEMA_PATH)

    # Строгая схема: лишнее поле — 422, а не молчаливое игнорирование.
    bad = {
        "version": 1,
        "students": [
            {"login": "x", "first_name": "X", "last_name": "Y", "sex": "m"}
        ],
    }
    assert (
        client.post(
            "/api/admin/migration/students", json=bad, headers=headers
        ).status_code
        == 422
    )

    student_user = (
        real_db.query(User).filter(User.login == "http.student").one()
    )
    student_headers = {
        "Authorization": "Bearer " + create_access_token(student_user.id)
    }
    assert (
        client.get(
            "/api/admin/migration/students", headers=student_headers
        ).status_code
        == 403
    )
