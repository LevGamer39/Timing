"""Юнит-тесты app.ai_text — чистого слоя парсинга и матчинга ИИ."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.ai_text import (
    MATCH_THRESHOLD,
    build_plan,
    clean_ai_text,
    default_priority_from_text,
    detect_action,
    extract_json,
    format_deadline,
    format_plan,
    format_priority,
    has_date_hint,
    looks_like_activity,
    match_tasks,
    normalize_text,
    parse_deadline_hint,
    parse_priority,
    resolve_timezone,
    score_task,
)
from app.models import TaskPriority, TaskStatus


class FakeTask:
    def __init__(self, id, title, subject=None):
        self.id = id
        self.title = title
        self.subject = subject


# ---------------------------------------------------------------------------
# Текстовые хелперы
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("<b>bold</b>", "bold"),
        ("line<br>break", "line\nbreak"),
        ("line<br/>break", "line\nbreak"),
        ("a&nbsp;b", "a b"),
        ("bullet • item", "bullet - item"),
        ("<script>alert(1)</script>hi", "alert(1)hi"),
    ],
)
def test_clean_ai_text_strips_markup(raw, expected):
    assert clean_ai_text(raw) == expected


def test_clean_ai_text_collapses_blank_runs():
    assert clean_ai_text("a\n\n\n\nb") == "a\n\nb"


def test_clean_ai_text_handles_none():
    assert clean_ai_text(None) == ""


def test_clean_ai_text_strips_markdown():
    raw = "#### Четверг\n- **09:00** урок\n* пункт"
    assert clean_ai_text(raw) == "Четверг\n- 09:00 урок\n- пункт"


def test_normalize_text_lowercases_and_strips_punctuation():
    assert normalize_text("Физика! Математика-2") == "физика математика 2"


def test_normalize_text_removes_accents():
    assert normalize_text("café") == "cafe"


def test_extract_json_from_fenced_block():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_from_prose():
    assert extract_json('Вот: {"action": "chat"} готово') == {"action": "chat"}


def test_extract_json_rejects_non_object():
    with pytest.raises(ValueError):
        extract_json("[1, 2, 3]")


# ---------------------------------------------------------------------------
# Приоритет
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("срочно сделать", TaskPriority.critical),
        ("подготовка к олимпиаде", TaskPriority.high),
        ("домашнее задание", TaskPriority.medium),
        ("просто заметка", TaskPriority.medium),
    ],
)
def test_default_priority_from_text(text, expected):
    assert default_priority_from_text(text) == expected


def test_parse_priority_prefers_explicit_value():
    assert parse_priority("high", "срочно") == TaskPriority.high


def test_parse_priority_falls_back_on_bad_value():
    assert parse_priority("nonsense", "срочно") == TaskPriority.critical


def test_format_priority_covers_every_enum_member():
    for priority in TaskPriority:
        assert isinstance(format_priority(priority), str)


# ---------------------------------------------------------------------------
# Дедлайны
# ---------------------------------------------------------------------------


def test_parse_deadline_hint_returns_none_for_empty():
    assert parse_deadline_hint(None, "") is None


def test_parse_deadline_hint_today_defaults_to_18_00():
    result = parse_deadline_hint("сегодня")
    today = datetime.now(timezone.utc).date()
    assert result is not None
    assert (result.year, result.month, result.day) == (
        today.year, today.month, today.day,
    )
    assert (result.hour, result.minute) == (18, 0)


def test_parse_deadline_hint_tomorrow():
    result = parse_deadline_hint("завтра")
    expected = datetime.now(timezone.utc).date() + timedelta(days=1)
    assert result.date() == expected


def test_parse_deadline_hint_day_after_tomorrow():
    result = parse_deadline_hint("послезавтра")
    expected = datetime.now(timezone.utc).date() + timedelta(days=2)
    assert result.date() == expected


def test_parse_deadline_hint_parses_explicit_time():
    result = parse_deadline_hint("завтра", "в 09:30")
    assert (result.hour, result.minute) == (9, 30)


def test_parse_deadline_hint_parses_numeric_date():
    result = parse_deadline_hint("15.06.2030")
    assert (result.year, result.month, result.day) == (2030, 6, 15)


def test_parse_deadline_hint_rejects_impossible_date():
    """31 февраля не должен ни бросать исключение, ни молча превратиться в другую дату."""
    result = parse_deadline_hint("31.02")
    assert result is not None
    assert result.date() == datetime.now(timezone.utc).date()


def test_parse_deadline_hint_rejects_out_of_range_month():
    result = parse_deadline_hint("01.13")
    assert result.date() == datetime.now(timezone.utc).date()


def test_parse_deadline_hint_two_digit_year_is_2000s():
    result = parse_deadline_hint("15.06.30")
    assert result.year == 2030


def test_parse_deadline_hint_is_utc_aware():
    assert parse_deadline_hint("сегодня").tzinfo is timezone.utc


# ---------------------------------------------------------------------------
# Матчинг — часть, которая раньше падала
# ---------------------------------------------------------------------------


def test_match_tasks_returns_empty_when_no_tasks_exist():
    """Регрессия: раньше бросался IndexError и возвращался HTTP 500."""
    assert match_tasks([], ["математика"]) == []


def test_match_tasks_returns_empty_for_no_queries():
    tasks = [FakeTask(1, "Math homework")]
    assert match_tasks(tasks, []) == []
    assert match_tasks(tasks, None) == []


def test_match_tasks_returns_empty_for_blank_queries():
    tasks = [FakeTask(1, "Math homework")]
    assert match_tasks(tasks, ["   ", "!!!"]) == []


def test_match_tasks_by_numeric_id():
    tasks = [FakeTask(7, "Math homework"), FakeTask(9, "Physics lab")]
    assert [t.id for t in match_tasks(tasks, ["7"])] == [7]


def test_match_tasks_numeric_id_absent_is_no_match():
    tasks = [FakeTask(7, "Math homework")]
    assert match_tasks(tasks, ["999"]) == []


def test_match_tasks_by_title():
    tasks = [FakeTask(1, "Математика параграф 12"), FakeTask(2, "Физика лаба")]
    assert [t.id for t in match_tasks(tasks, ["математика"])] == [1]


def test_match_tasks_rejects_below_threshold():
    tasks = [FakeTask(1, "Математика")]
    assert match_tasks(tasks, ["космический корабль"]) == []


def test_match_tasks_deduplicates_repeated_queries():
    tasks = [FakeTask(1, "Математика параграф 12")]
    assert len(match_tasks(tasks, ["математика", "математика"])) == 1


def test_match_tasks_handles_none_subject():
    tasks = [FakeTask(1, "Математика", subject=None)]
    assert [t.id for t in match_tasks(tasks, ["математика"])] == [1]


def test_match_tasks_is_case_and_accent_insensitive():
    tasks = [FakeTask(1, "Физика лабораторная")]
    assert [t.id for t in match_tasks(tasks, ["ФИЗИКА"])] == [1]


def test_match_tasks_respects_query_cap():
    tasks = [FakeTask(i, f"Задача про тему {i}") for i in range(20)]
    assert len(match_tasks(tasks, [f"тема {i}" for i in range(20)])) <= 5


def test_score_task_is_symmetric_enough_for_identical_text():
    task = FakeTask(1, "Математика")
    assert score_task("математика", task) >= MATCH_THRESHOLD


# ---------------------------------------------------------------------------
# Форматирование и определение действия
# ---------------------------------------------------------------------------


def test_format_deadline_without_value():
    assert format_deadline(None) == "без срока"


def test_format_deadline_accepts_naive_datetime():
    naive = datetime(2030, 6, 15, 9, 30)
    assert format_deadline(naive) == "15.06 09:30"


def test_detect_action_proposes_subtasks():
    action, data = detect_action("1. read 2. write", "разбей на шаги")
    assert action == "create_subtasks"
    assert "hint" in data


def test_detect_action_ignores_plain_chat():
    assert detect_action("1. one", "как дела") == (None, None)


def test_detect_action_requires_numbered_reply():
    assert detect_action("just prose", "разбей") == (None, None)


# ---------------------------------------------------------------------------
# Часовой пояс и наличие даты
# ---------------------------------------------------------------------------


def test_parse_deadline_hint_uses_local_timezone():
    """18:00 по Москве — это 15:00 UTC, а не 18:00 UTC."""
    zone = ZoneInfo("Europe/Moscow")
    result = parse_deadline_hint("завтра", "", zone)
    local = result.astimezone(zone)
    assert (local.hour, local.minute) == (18, 0)
    assert result.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("завтра сдать", True),
        ("в пятницу", True),
        ("15.06.2030", True),
        ("в 09:30", True),
        ("просто задача", False),
        ("", False),
    ],
)
def test_has_date_hint(text, expected):
    assert has_date_hint(text) is expected


def test_resolve_timezone_falls_back_to_default():
    assert resolve_timezone("Not/AZone", "Europe/Moscow").key == "Europe/Moscow"


def test_resolve_timezone_never_raises():
    assert resolve_timezone(None, None) is not None


# ---------------------------------------------------------------------------
# Расписание на неделю
# ---------------------------------------------------------------------------


class FakeSchedule:
    def __init__(self, weekday, start_time, end_time, subject, room=None):
        self.weekday = weekday
        self.start_time = start_time
        self.end_time = end_time
        self.subject = subject
        self.room = room


class PlanTask:
    def __init__(self, id, title, deadline=None, status=TaskStatus.pending):
        self.id = id
        self.title = title
        self.deadline = deadline
        self.status = status
        self.subject = "Алгебра"
        self.priority = TaskPriority.high


MONDAY = date(2030, 6, 17)  # понедельник


def test_build_plan_groups_lessons_and_tasks_by_day():
    schedule = [FakeSchedule(0, "09:00", "09:45", "Алгебра")]
    tasks = [
        PlanTask(1, "Параграф", datetime(2030, 6, 17, 15, 0, tzinfo=timezone.utc))
    ]
    plan = build_plan(schedule, tasks, MONDAY, 2, timezone.utc)
    assert len(plan["days"]) == 2
    assert [t.id for t in plan["days"][0]["tasks"]] == [1]
    assert plan["days"][1]["lessons"] == []


def test_build_plan_skips_completed_tasks():
    tasks = [PlanTask(1, "Готово", status=TaskStatus.done)]
    plan = build_plan([], tasks, MONDAY, 1, timezone.utc)
    assert plan["days"][0]["tasks"] == []


def test_build_plan_separates_activities_from_study():
    deadline = datetime(2030, 6, 17, 15, 0, tzinfo=timezone.utc)
    tasks = [PlanTask(1, "Кружок", deadline), PlanTask(2, "Домашка", deadline)]
    plan = build_plan([], tasks, MONDAY, 1, timezone.utc, activity_ids={1})
    day = plan["days"][0]
    assert [t.id for t in day["activities"]] == [1]
    assert [t.id for t in day["tasks"]] == [2]


def test_build_plan_marks_overload():
    deadline = datetime(2030, 6, 17, 15, 0, tzinfo=timezone.utc)
    tasks = [
        PlanTask(1, "Раз", deadline),
        PlanTask(2, "Два", deadline),
        PlanTask(3, "Три", deadline),
    ]
    plan = build_plan([], tasks, MONDAY, 1, timezone.utc)
    day = plan["days"][0]
    assert day["overloaded"] is True
    assert "3 дедлайна" in day["reason"]


def test_build_plan_keeps_undated_tasks_separate():
    plan = build_plan([], [PlanTask(1, "Без срока")], MONDAY, 1, timezone.utc)
    assert [t.id for t in plan["undated"]] == [1]


def test_format_plan_mentions_days_and_overload():
    tasks = [
        PlanTask(
            i, f"Задача {i}", datetime(2030, 6, 17, 15, 0, tzinfo=timezone.utc)
        )
        for i in range(3)
    ]
    text = format_plan(build_plan([], tasks, MONDAY, 1, timezone.utc))
    assert "Задания:" in text
    assert "перегруз" in text
    assert "3 дедлайна" in text


def test_format_plan_handles_empty_plan():
    assert format_plan({"days": [], "undated": []}) == (
        "На эти дни уроков и задач нет."
    )


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Кружок по физике", True),
        ("Секция баскетбола", True),
        ("Занятие с репетитором", True),
        ("Прочитать параграф 12", False),
        ("Лабораторная по химии", False),
        (None, False),
    ],
)
def test_looks_like_activity(title, expected):
    assert looks_like_activity(title) is expected
