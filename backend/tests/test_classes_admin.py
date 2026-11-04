"""Управление классами, дашборд учителя и админ-панель."""
import pytest
from fastapi import HTTPException

from app.models import Assignment, ClassMembership, TaskStatus, UserRole
from app.routers import admin as admin_router
from app.routers import classes as classes_router
from app.routers.admin import AdminClassCreate, AdminClassUpdate, AdminUserUpdate


@pytest.fixture()
def enrolled(db, klass, student, student2):
    db.add(ClassMembership(student_id=student.id, class_id=klass.id))
    db.add(ClassMembership(student_id=student2.id, class_id=klass.id))
    db.commit()
    return klass


# ---------------------------------------------------------------------------
# Представления классов учителя
# ---------------------------------------------------------------------------


def test_list_classes_counts_members(db, teacher, enrolled):
    classes = classes_router.list_classes(current_user=teacher, db=db)
    assert len(classes) == 1
    assert classes[0].student_count == 2


def test_teacher_does_not_see_other_teachers_classes(db, teacher, other_teacher):
    assert classes_router.list_classes(current_user=teacher, db=db) == []


def test_class_detail_includes_students_and_assignments(
    db, teacher, enrolled, make_assignment
):
    make_assignment()
    detail = classes_router.get_class(enrolled.id, current_user=teacher, db=db)
    assert detail.student_count == 2
    assert len(detail.students) == 2
    assert len(detail.assignments) == 1


def test_class_detail_forbidden_for_other_teacher(db, klass, other_teacher):
    with pytest.raises(HTTPException) as exc:
        classes_router.get_class(klass.id, current_user=other_teacher, db=db)
    assert exc.value.status_code == 404


def test_overview_returns_typed_matrix(db, teacher, enrolled, make_assignment):
    make_assignment()
    overview = classes_router.get_class_overview(
        enrolled.id, current_user=teacher, db=db
    )
    assert overview.student_count == 2
    assert len(overview.assignments) == 1


def test_available_names_are_scoped_to_caller(
    db, teacher, other_teacher, klass
):
    mine = classes_router.list_available_class_names(
        current_user=teacher, db=db
    )
    theirs = classes_router.list_available_class_names(
        current_user=other_teacher, db=db
    )
    assert [c.name for c in mine] == ["10A"]
    assert theirs == []


def test_students_cannot_enumerate_class_names(db, student, klass):
    assert classes_router.list_available_class_names(
        current_user=student, db=db
    ) == []


def test_remove_student(db, teacher, enrolled, student):
    classes_router.remove_student(
        enrolled.id, student.id, current_user=teacher, db=db
    )
    assert db.query(ClassMembership).filter(
        ClassMembership.student_id == student.id
    ).count() == 0


# ---------------------------------------------------------------------------
# Дашборд учителя
# ---------------------------------------------------------------------------


def test_dashboard_classifies_students(
    db, teacher, enrolled, student, student2, make_assignment, make_task
):
    assignment = make_assignment(class_id=enrolled.id)
    make_task(
        student.id, is_personal=False, assignment_id=assignment.id,
        status=TaskStatus.done,
    )
    make_task(
        student2.id, is_personal=False, assignment_id=assignment.id,
        status=TaskStatus.in_progress,
    )

    dashboard = classes_router.teacher_dashboard_data(
        class_id=enrolled.id, current_user=teacher, db=db
    )
    assert dashboard.statistics.completed == 1
    assert dashboard.statistics.started == 1
    assert dashboard.statistics.not_started == 0


def test_dashboard_paginates(db, teacher, enrolled):
    dashboard = classes_router.teacher_dashboard_data(
        class_id=enrolled.id, page=1, page_size=1, current_user=teacher, db=db
    )
    assert dashboard.pagination.total_items == 2
    assert dashboard.pagination.total_pages == 2
    assert len(dashboard.students) == 1


def test_dashboard_requires_ownership(db, klass, other_teacher):
    with pytest.raises(HTTPException) as exc:
        classes_router.teacher_dashboard_data(
            class_id=klass.id, current_user=other_teacher, db=db
        )
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Вступление в класс по инвайт-коду
# ---------------------------------------------------------------------------


def test_join_rejects_unknown_code(db, student):
    with pytest.raises(HTTPException) as exc:
        classes_router.join_class(
            invite_code="NOPE", current_user=student, db=db
        )
    assert exc.value.status_code == 404


def test_join_sets_grade(db, student, klass):
    classes_router.join_class(
        invite_code=klass.invite_code, current_user=student, db=db
    )
    db.refresh(student)
    assert student.grade == "10A"


def test_rejoining_same_class_is_rejected(db, student, klass):
    classes_router.join_class(
        invite_code=klass.invite_code, current_user=student, db=db
    )
    with pytest.raises(HTTPException) as exc:
        classes_router.join_class(
            invite_code=klass.invite_code, current_user=student, db=db
        )
    assert exc.value.status_code == 400


def test_joining_second_class_keeps_one_membership(
    db, student, klass, other_teacher
):
    from app.models import Class

    second = Class(
        name="9B", teacher_id=other_teacher.id, invite_code="BBBB222222"
    )
    db.add(second)
    db.commit()

    classes_router.join_class(
        invite_code=klass.invite_code, current_user=student, db=db
    )
    classes_router.join_class(
        invite_code=second.invite_code, current_user=student, db=db
    )

    rows = db.query(ClassMembership).filter(
        ClassMembership.student_id == student.id
    ).all()
    assert len(rows) == 1
    assert rows[0].class_id == second.id


# ---------------------------------------------------------------------------
# Админ
# ---------------------------------------------------------------------------


def test_admin_generates_16_char_invite(db, admin):
    invite = admin_router.generate_invite_code(current_user=admin, db=db)
    assert len(invite.code) == 16


def test_admin_creates_class_with_12_char_code(db, admin, other_teacher, student):
    created = admin_router.create_class(
        AdminClassCreate(
            name="9B", teacher_id=other_teacher.id, student_ids=[student.id]
        ),
        current_user=admin, db=db,
    )
    assert len(created.invite_code) == 12
    assert created.student_count == 1


def test_admin_create_requires_known_teacher(db, admin, make_user):
    stranger = make_user("x@test.local", UserRole.student)
    with pytest.raises(HTTPException) as exc:
        admin_router.create_class(
            AdminClassCreate(name="9B", teacher_id=stranger.id),
            current_user=admin, db=db,
        )
    assert exc.value.status_code == 404


def test_admin_moves_student_between_classes(db, admin, klass, other_teacher, student):
    db.add(ClassMembership(student_id=student.id, class_id=klass.id))
    db.commit()

    created = admin_router.create_class(
        AdminClassCreate(
            name="9B", teacher_id=other_teacher.id, student_ids=[student.id]
        ),
        current_user=admin, db=db,
    )

    rows = db.query(ClassMembership).filter(
        ClassMembership.student_id == student.id
    ).all()
    assert len(rows) == 1
    assert rows[0].class_id == created.id


def test_admin_deletes_class_cascade(db, admin, klass, student, make_assignment):
    db.add(ClassMembership(student_id=student.id, class_id=klass.id))
    db.commit()
    assignment = make_assignment(class_id=klass.id)

    admin_router.delete_class(klass.id, current_user=admin, db=db)

    assert db.get(Assignment, assignment.id) is None
    assert db.query(ClassMembership).filter(
        ClassMembership.class_id == klass.id
    ).count() == 0


def test_admin_filters_students_by_class(db, admin, klass, student, student2, other_teacher):
    from app.models import Class

    second = Class(
        name="9B", teacher_id=other_teacher.id, invite_code="BBBB222222"
    )
    db.add(second)
    db.commit()
    db.add(ClassMembership(student_id=student.id, class_id=klass.id))
    db.commit()

    rows = admin_router._collect_students(db, class_id=klass.id)
    assert [r.id for r in rows] == [student.id]
    assert rows[0].class_name == "10A"


def test_admin_can_deactivate_student(db, admin, student):
    row = admin_router.update_student_account(
        student.id, AdminUserUpdate(is_active=False),
        current_user=admin, db=db,
    )
    assert row.is_active is False


def test_admin_cannot_touch_admin_account(db, admin, make_user):
    other_admin = make_user("admin2@test.local", UserRole.admin)
    with pytest.raises(HTTPException) as exc:
        admin_router.update_student_account(
            other_admin.id, AdminUserUpdate(is_active=False),
            current_user=admin, db=db,
        )
    assert exc.value.status_code == 404


def test_admin_dashboard_aggregates(db, admin, teacher, enrolled):
    dashboard = admin_router.dashboard(current_user=admin, db=db)
    assert dashboard.classes_count == 1
    assert dashboard.teachers_count == 1
    assert dashboard.students_count == 2


def test_list_teachers_returns_homeroom_candidates(db, admin, teacher, enrolled):
    teachers = admin_router.list_teachers(current_user=admin, db=db)
    assert [t.id for t in teachers] == [teacher.id]
    assert teachers[0].class_count == 1
    assert teachers[0].teacher_subjects == ["Math"]


def test_admin_sets_several_teacher_subjects(db, admin, teacher, enrolled):
    from app.models import TeacherSubject

    updated = admin_router.update_student_account(
        teacher.id,
        admin_router.AdminUserUpdate(
            teacher_subjects=["Math", "Literature"]
        ),
        current_user=admin, db=db,
    )
    assert "предметов: 2" in updated.class_name
    db.refresh(teacher)
    assert sorted(
        row.subject for row in db.query(TeacherSubject).all()
    ) == ["Literature", "Math"]


def test_admin_updates_class_teacher(db, admin, klass, other_teacher):
    updated = admin_router.update_class(
        klass.id,
        AdminClassUpdate(teacher_id=other_teacher.id),
        current_user=admin, db=db,
    )
    assert updated.teacher_id == other_teacher.id


def test_used_invite_cannot_be_deleted(db, admin, invite_code, student):
    invite_code.is_used = True
    db.commit()
    with pytest.raises(HTTPException) as exc:
        admin_router.delete_invite_code(
            invite_code.id, current_user=admin, db=db
        )
    assert exc.value.status_code == 400