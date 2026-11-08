"""Тексты и форматирование сообщений."""

from texts.formatters import (
    averages_block,
    children_block,
    esc,
    fmt_deadline,
    grades_block,
    schedule_block,
    summary_block,
    task_line,
    tasks_block,
)
from texts.messages import (
    ADMIN_HELP_TEXT,
    HELP_TEXT,
    PARENT_CANNOT_ADD_TEXT,
    PARENT_HELP_TEXT,
    PROCESSING_TEXT,
    TEACHER_HELP_TEXT,
    UNLINKED_TEXT,
    greeting,
    help_text,
)

__all__ = [
    "ADMIN_HELP_TEXT",
    "HELP_TEXT",
    "PARENT_CANNOT_ADD_TEXT",
    "PARENT_HELP_TEXT",
    "PROCESSING_TEXT",
    "TEACHER_HELP_TEXT",
    "UNLINKED_TEXT",
    "greeting",
    "help_text",
    "averages_block",
    "children_block",
    "esc",
    "fmt_deadline",
    "grades_block",
    "schedule_block",
    "summary_block",
    "task_line",
    "tasks_block",
]
