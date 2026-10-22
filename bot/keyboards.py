"""Клавиатуры и callback-кнопки на штатных средствах maxapi."""

from __future__ import annotations

from maxapi.types.attachments.buttons import CallbackButton
from maxapi.types.attachments.buttons.attachment_button import AttachmentButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder


def _button(text: str, payload: str) -> CallbackButton:
    return CallbackButton(text=text, payload=payload)


def main_menu(role: str | None) -> AttachmentButton:
    """Главное меню. Набор кнопок зависит от роли."""

    builder = InlineKeyboardBuilder()

    if role in {None, "student"}:
        builder.row(_button("📅 На сегодня", "menu:today"))
        builder.row(
            _button("➕ Добавить", "menu:add"),
            _button("🤖 ИИ-чат", "menu:ai"),
        )
        builder.row(
            _button("📊 Оценки", "menu:grades"),
            _button("🗓 Расписание", "menu:schedule"),
        )
        builder.row(_button("📈 Сводка", "menu:summary"))
    elif role == "parent":
        builder.row(_button("👶 Дети", "menu:children"))
        builder.row(_button("📈 Сводка", "menu:summary"))
    elif role == "teacher":
        builder.row(_button("📊 Мои классы", "menu:classes"))

    builder.row(_button("❓ Помощь", "menu:help"))
    return builder.as_markup()


def tasks_keyboard(tasks: list[dict]) -> AttachmentButton:
    """Кнопки «Сделал» под списком заданий."""

    builder = InlineKeyboardBuilder()
    for task in tasks[:6]:
        task_id = int(task["id"])
        title = str(task.get("title", "Задание"))[:32]
        builder.row(_button(f"✅ {title}", f"task:done:{task_id}"))
    return builder.as_markup()


def task_actions(task_id: int, done: bool) -> AttachmentButton:
    builder = InlineKeyboardBuilder()
    if not done:
        builder.row(_button("✅ Сделал", f"task:done:{task_id}"))
    builder.row(_button("🗑 Удалить", f"task:del:{task_id}"))
    return builder.as_markup()


def homework_keyboard(index: int) -> AttachmentButton:
    """Кнопки под карточкой найденного ДЗ."""

    builder = InlineKeyboardBuilder()
    builder.row(_button("➕ Добавить себе", f"hw:add:{index}"))
    builder.row(_button("🚫 Не задание", f"hw:skip:{index}"))
    return builder.as_markup()


__all__ = [
    "main_menu",
    "tasks_keyboard",
    "task_actions",
    "homework_keyboard",
]
