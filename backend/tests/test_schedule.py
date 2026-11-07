"""Валидация и принадлежность расписания."""
import pytest
from fastapi import HTTPException

from app.routers import schedule as sched_router
from app.routers.schedule import ScheduleEntryCreate


@pytest.mark.parametrize(
    "start,end,expected_start",
    [
        ("09:00", "09:45", "09:00"),
        ("00:00", "23:59", "00:00"),
        # Часы из одной цифры принимаются и дополняются нулём, чтобы в колонке
        # всегда лежало сортируемое 5-символьное значение.
        ("8:00", "9:30", "08:00"),
    ],
)
def test_valid_times_are_normalised(start, end, expected_start):
    entry = ScheduleEntryCreate(
        subject="Math", weekday=0, start_time=start, end_time=end
    )
    assert entry.start_time == expected_start
    assert entry.end_time == f"{int(end.split(':')[0]):02d}:{end.split(':')[1]}"


@pytest.mark.parametrize("bad", ["abc", "9", "24:00", "09:60", "", "9:5:0"])
def test_invalid_times_are_rejected(bad):
    with pytest.raises(ValueError):
        ScheduleEntryCreate(
            subject="Math", weekday=0, start_time=bad, end_time="10:00"
        )


@pytest.mark.parametrize("weekday", [0, 6])
def test_weekday_bounds_accepted(weekday):
    assert ScheduleEntryCreate(
        subject="Math", weekday=weekday, start_time="09:00",
        end_time="09:45",
    ).weekday == weekday


@pytest.mark.parametrize("weekday", [-1, 7])
def test_weekday_out_of_range_rejected(weekday):
    with pytest.raises(ValueError):
        ScheduleEntryCreate(
            subject="Math", weekday=weekday, start_time="09:00",
            end_time="09:45",
        )


def test_blank_subject_rejected():
    with pytest.raises(ValueError):
        ScheduleEntryCreate(
            subject="   ", weekday=0, start_time="09:00", end_time="09:45"
        )


# CRUD


def test_add_entry_persists(db, student):
    entry = sched_router.add_entry(
        ScheduleEntryCreate(
            subject="Math", teacher_name="Ivanov", weekday=0,
            start_time="09:00", end_time="09:45", room="101",
        ),
        current_user=student, db=db,
    )
    assert entry.id is not None
    assert entry.room == "101"


def test_my_schedule_is_ordered(db, student):
    for weekday, start in ((2, "10:00"), (0, "09:00"), (0, "08:00")):
        sched_router.add_entry(
            ScheduleEntryCreate(
                subject="Math", weekday=weekday,
                start_time=start, end_time="09:45",
            ),
            current_user=student, db=db,
        )

    rows = sched_router.my_schedule(current_user=student, db=db)
    assert [(r.weekday, r.start_time) for r in rows] == [
        (0, "08:00"), (0, "09:00"), (2, "10:00"),
    ]


def test_my_schedule_filters_by_weekday(db, student):
    sched_router.add_entry(
        ScheduleEntryCreate(
            subject="Math", weekday=1, start_time="09:00", end_time="09:45"
        ),
        current_user=student, db=db,
    )
    sched_router.add_entry(
        ScheduleEntryCreate(
            subject="Physics", weekday=3, start_time="10:00", end_time="10:45"
        ),
        current_user=student, db=db,
    )

    rows = sched_router.my_schedule(weekday=1, current_user=student, db=db)
    assert [r.subject for r in rows] == ["Math"]


def test_other_student_cannot_update(db, student, student2):
    entry = sched_router.add_entry(
        ScheduleEntryCreate(
            subject="Math", weekday=0, start_time="09:00", end_time="09:45"
        ),
        current_user=student, db=db,
    )
    with pytest.raises(HTTPException) as exc:
        sched_router.update_entry(
            entry.id,
            ScheduleEntryCreate(
                subject="Hacked", weekday=0,
                start_time="09:00", end_time="09:45",
            ),
            current_user=student2, db=db,
        )
    assert exc.value.status_code == 404


def test_other_student_cannot_delete(db, student, student2):
    entry = sched_router.add_entry(
        ScheduleEntryCreate(
            subject="Math", weekday=0, start_time="09:00", end_time="09:45"
        ),
        current_user=student, db=db,
    )
    with pytest.raises(HTTPException) as exc:
        sched_router.delete_entry(entry.id, current_user=student2, db=db)
    assert exc.value.status_code == 404


def test_parent_reads_child_schedule(db, parent, student, link_parent_child):
    link_parent_child()
    sched_router.add_entry(
        ScheduleEntryCreate(
            subject="Math", weekday=0, start_time="09:00", end_time="09:45"
        ),
        current_user=student, db=db,
    )
    rows = sched_router.child_schedule(student.id, current_user=parent, db=db)
    assert len(rows) == 1


def test_parent_cannot_read_foreign_schedule(db, parent, student2):
    with pytest.raises(HTTPException) as exc:
        sched_router.child_schedule(student2.id, current_user=parent, db=db)
    assert exc.value.status_code == 403