"""Журнал: несколько уроков на дату, «Н» по уроку и нагрузка дня."""
from datetime import date

import pytest
from fastapi import HTTPException

from app.models import (
    ClassMembership,
    JournalLesson,
    JournalWorkType,
)
from app.routers import journal as journal_router
from app.routers.journal import (
    JournalAbsenceIn,
    JournalLessonIn,
    JournalMarkIn,
    JournalSync,
)

DAY = date(2026, 11, 3)


@pytest.fixture()
def work_types(db):
    db.add_all([
        JournalWorkType(code="lesson", name="Работа на уроке", weight=1),
        JournalWorkType(code="test", name="Тест", weight=2),
        JournalWorkType(code="kr", name="Контрольная", weight=3),
    ])
    db.commit()


@pytest.fixture()
def students(db, klass, student, student2):
    db.add(ClassMembership(student_id=student.id, class_id=klass.id))
    db.add(ClassMembership(student_id=student2.id, class_id=klass.id))
    db.commit()
    return student, student2


def test_two_lessons_on_same_date_keep_their_marks(
    db, teacher, klass, students, work_types
):
    student, _ = students
    saved = journal_router.save_journal(
        klass.id,
        "Math",
        JournalSync(
            lessons=[
                JournalLessonIn(date=DAY, slot=0, type="kr"),
                JournalLessonIn(date=DAY, slot=1, type="test"),
            ],
            marks=[
                JournalMarkIn(
                    student_id=student.id, date=DAY, slot=1, value=4
                )
            ],
            absences=[
                JournalAbsenceIn(student_id=student.id, date=DAY, slot=0)
            ],
        ),
        current_user=teacher,
        db=db,
    )

    assert [(l.date, l.slot, l.type) for l in saved.lessons] == [
        (DAY, 0, "kr"),
        (DAY, 1, "test"),
    ]
    assert [(m.date, m.slot, m.value) for m in saved.marks] == [
        (DAY, 1, 4.0)
    ]
    assert [(a.date, a.slot) for a in saved.absences] == [(DAY, 0)]


def test_overlong_subject_is_rejected_before_the_database(
    db, teacher, klass, students, work_types
):
    # Раньше путь /{subject} уходил в VARCHAR(100) как есть: DataError -> 500.
    with pytest.raises(HTTPException) as exc:
        journal_router.save_journal(
            klass.id,
            "я" * 150,
            JournalSync(lessons=[JournalLessonIn(date=DAY, slot=0)]),
            current_user=teacher,
            db=db,
        )
    assert exc.value.status_code == 400


def test_duplicate_lesson_slot_is_rejected(
    db, teacher, klass, students, work_types
):
    with pytest.raises(HTTPException) as exc:
        journal_router.save_journal(
            klass.id,
            "Math",
            JournalSync(
                lessons=[
                    JournalLessonIn(date=DAY, slot=0),
                    JournalLessonIn(date=DAY, slot=0),
                ]
            ),
            current_user=teacher,
            db=db,
        )
    assert exc.value.status_code == 400


def test_class_load_counts_other_heavy_subjects(
    db, teacher, klass, students, work_types
):
    student, _ = students
    journal_router.save_journal(
        klass.id,
        "Math",
        JournalSync(lessons=[JournalLessonIn(date=DAY, slot=0)]),
        current_user=teacher,
        db=db,
    )
    for slot in (0, 1):
        db.add(
            JournalLesson(
                class_id=klass.id,
                subject="Physics",
                date=DAY,
                slot=slot,
                type_code="kr",
                created_by_id=teacher.id,
            )
        )
    db.commit()

    data = journal_router.get_journal(
        klass.id, "Math", current_user=teacher, db=db
    )
    assert data.class_load == {DAY.isoformat(): 2}
