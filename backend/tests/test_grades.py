"""Оценки: авторизация, массовая выставка и сводки ученика."""
import pytest
from fastapi import HTTPException

from app.models import ClassMembership, Grade, UserRole
from app.routers import grades as grades_router
from app.routers.grades import BulkGradeCreate, BulkGradeItem, GradeCreate

from .conftest import NOW


@pytest.fixture()
def enrolled(db, klass, student, student2):
    db.add(ClassMembership(student_id=student.id, class_id=klass.id))
    db.add(ClassMembership(student_id=student2.id, class_id=klass.id))
    db.commit()
    return klass


@pytest.fixture()
def graded_assignment(db, teacher, enrolled, student):
    return None


# Одиночная оценка


def test_teacher_can_grade_own_student(db, teacher, enrolled, student):
    grade = grades_router.add_grade(
        GradeCreate(student_id=student.id, subject="Math", value=4.5),
        current_user=teacher, db=db,
    )
    assert grade.value == 4.5
    assert grade.graded_by_id == teacher.id


def test_grade_value_is_range_checked():
    with pytest.raises(ValueError):
        GradeCreate(student_id=1, subject="Math", value=6.0)
    with pytest.raises(ValueError):
        GradeCreate(student_id=1, subject="Math", value=0.0)


def test_teacher_cannot_grade_foreign_student(
    db, teacher, make_user, other_teacher
):
    stranger = make_user("stranger@test.local", UserRole.student)
    with pytest.raises(HTTPException) as exc:
        grades_router.add_grade(
            GradeCreate(student_id=stranger.id, subject="Math", value=4.0),
            current_user=other_teacher, db=db,
        )
    assert exc.value.status_code == 403


def test_regrading_updates_in_place(
    db, teacher, enrolled, student, make_assignment
):
    assignment = make_assignment()
    for value in (4.0, 2.0):
        grades_router.add_grade(
            GradeCreate(
                student_id=student.id, subject="Math", value=value,
                assignment_id=assignment.id,
            ),
            current_user=teacher, db=db,
        )

    rows = db.query(Grade).filter(
        Grade.student_id == student.id,
        Grade.assignment_id == assignment.id,
    ).all()
    assert len(rows) == 1
    assert rows[0].value == 2.0


# Массовая выставка


def test_bulk_returns_only_touched_rows(
    db, teacher, enrolled, student, student2, make_assignment
):
    assignment = make_assignment()
    result = grades_router.add_bulk_grades(
        BulkGradeCreate(
            assignment_id=assignment.id,
            subject="Math",
            items=[
                BulkGradeItem(student_id=student.id, value=4.0),
                BulkGradeItem(student_id=student2.id, value=5.0),
            ],
        ),
        current_user=teacher, db=db,
    )
    assert len(result) == 2
    assert {g.student_id for g in result} == {student.id, student2.id}


def test_bulk_resubmission_returns_one_row(
    db, teacher, enrolled, student, student2, make_assignment
):
    assignment = make_assignment()
    grades_router.add_bulk_grades(
        BulkGradeCreate(
            assignment_id=assignment.id,
            subject="Math",
            items=[
                BulkGradeItem(student_id=student.id, value=4.0),
                BulkGradeItem(student_id=student2.id, value=5.0),
            ],
        ),
        current_user=teacher, db=db,
    )

    again = grades_router.add_bulk_grades(
        BulkGradeCreate(
            assignment_id=assignment.id,
            subject="Math",
            items=[BulkGradeItem(student_id=student.id, value=3.0)],
        ),
        current_user=teacher, db=db,
    )
    assert len(again) == 1


def test_bulk_rejects_duplicate_students(
    db, teacher, enrolled, student, make_assignment
):
    assignment = make_assignment()
    with pytest.raises(HTTPException) as exc:
        grades_router.add_bulk_grades(
            BulkGradeCreate(
                assignment_id=assignment.id,
                subject="Math",
                items=[
                    BulkGradeItem(student_id=student.id, value=4.0),
                    BulkGradeItem(student_id=student.id, value=5.0),
                ],
            ),
            current_user=teacher, db=db,
        )
    assert exc.value.status_code == 400


def test_bulk_rejects_student_outside_the_class(
    db, teacher, enrolled, make_user, make_assignment
):
    assignment = make_assignment()
    outsider = make_user("outsider@test.local", UserRole.student)
    with pytest.raises(HTTPException) as exc:
        grades_router.add_bulk_grades(
            BulkGradeCreate(
                assignment_id=assignment.id,
                subject="Math",
                items=[BulkGradeItem(student_id=outsider.id, value=4.0)],
            ),
            current_user=teacher, db=db,
        )
    assert exc.value.status_code == 400


def test_bulk_foreign_assignment_is_404(
    db, teacher, make_assignment, other_teacher, student
):
    assignment = make_assignment()
    with pytest.raises(HTTPException) as exc:
        grades_router.add_bulk_grades(
            BulkGradeCreate(
                assignment_id=assignment.id,
                subject="Math",
                items=[BulkGradeItem(student_id=student.id, value=4.0)],
            ),
            current_user=other_teacher, db=db,
        )
    assert exc.value.status_code == 404


# Сводки


def test_averages_are_grouped(db, teacher, enrolled, student):
    for value in (4.0, 5.0):
        grades_router.add_grade(
            GradeCreate(student_id=student.id, subject="Math", value=value),
            current_user=teacher, db=db,
        )
    grades_router.add_grade(
        GradeCreate(student_id=student.id, subject="Physics", value=3.0),
        current_user=teacher, db=db,
    )

    averages = {
        row.subject: row
        for row in grades_router.my_averages(current_user=student, db=db)
    }
    assert averages["Math"].average == 4.5
    assert averages["Math"].count == 2
    assert averages["Physics"].count == 1


def test_summary_overall_average(db, teacher, enrolled, student):
    for value in (4.0, 5.0):
        grades_router.add_grade(
            GradeCreate(student_id=student.id, subject="Math", value=value),
            current_user=teacher, db=db,
        )
    summary = grades_router.my_grade_summary(current_user=student, db=db)
    assert summary.overall_average == 4.5


def test_summary_without_grades(db, student):
    summary = grades_router.my_grade_summary(current_user=student, db=db)
    assert summary.overall_average is None
    assert summary.best_subjects == []


def test_summary_reports_class_size(db, teacher, enrolled, student, student2):
    summary = grades_router.my_grade_summary(current_user=student, db=db)
    assert summary.class_size == 2


def test_summary_ranks_student(db, teacher, enrolled, student, student2):
    grades_router.add_grade(
        GradeCreate(student_id=student.id, subject="Math", value=3.0),
        current_user=teacher, db=db,
    )
    grades_router.add_grade(
        GradeCreate(student_id=student2.id, subject="Math", value=5.0),
        current_user=teacher, db=db,
    )
    summary = grades_router.my_grade_summary(current_user=student, db=db)
    assert summary.class_rank == 2


# Доступ


def test_parent_can_read_child_grades(
    db, parent, student, link_parent_child, teacher, enrolled, make_assignment
):
    link_parent_child()
    assignment = make_assignment()
    grades_router.add_grade(
        GradeCreate(
            student_id=student.id, subject="Math", value=4.0,
            assignment_id=assignment.id,
        ),
        current_user=teacher, db=db,
    )
    rows = grades_router.child_grades(
        student.id, current_user=parent, db=db
    )
    assert len(rows) == 1


def test_parent_cannot_read_foreign_grades(db, parent, student2):
    with pytest.raises(HTTPException) as exc:
        grades_router.child_grades(student2.id, current_user=parent, db=db)
    assert exc.value.status_code == 403


def test_teacher_cannot_delete_colleague_grade(
    db, teacher, enrolled, student, make_assignment, other_teacher
):
    assignment = make_assignment()
    grade = grades_router.add_grade(
        GradeCreate(
            student_id=student.id, subject="Math", value=4.0,
            assignment_id=assignment.id,
        ),
        current_user=teacher, db=db,
    )
    with pytest.raises(HTTPException) as exc:
        grades_router.delete_grade(
            grade.id, current_user=other_teacher, db=db
        )
    assert exc.value.status_code == 404


def test_author_can_delete_own_grade(
    db, teacher, enrolled, student, make_assignment
):
    assignment = make_assignment()
    grade = grades_router.add_grade(
        GradeCreate(
            student_id=student.id, subject="Math", value=4.0,
            assignment_id=assignment.id,
        ),
        current_user=teacher, db=db,
    )
    grades_router.delete_grade(grade.id, current_user=teacher, db=db)
    assert db.get(Grade, grade.id) is None