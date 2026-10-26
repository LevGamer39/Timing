"""Регистрация аккаунта из MAX: логин -> инвайт-код -> имя -> бэкенд.

Диалог живёт в памяти процесса (``ctx.registration``): у незарегистрированного
пользователя ещё нет ни JWT, ни строки в БД, поэтому хранить состояние больше
негде. Перезапуск бота просто начинает диалог заново.
"""

from __future__ import annotations

from typing import Any

from core.context import AppContext
from core.security import hash_user_id
from core.ui import event_user, respond
from keyboards import main_menu, name_confirm_keyboard
from services.backend import BackendError
from texts import esc
from texts.messages import (
    REGISTER_CHECK_FAILED_TEXT,
    REGISTER_INVITE_CHECK_FAILED_TEXT,
    REGISTER_INVITE_INVALID_TEXT,
    REGISTER_INVITE_TEXT,
    REGISTER_LOGIN_EXISTS_TEXT,
    REGISTER_LOGIN_INVALID_TEXT,
    REGISTER_NAME_BAD_TEXT,
    REGISTER_NAME_CONFIRM_TEXT,
    REGISTER_NAME_EDIT_TEXT,
    REGISTER_NAME_FAILED_TEXT,
    REGISTER_WELCOME_TEXT,
)

LOGIN = "login"
INVITE = "invite"
NAME_CONFIRM = "name_confirm"
NAME_EDIT = "name_edit"

MAX_LOGIN_LENGTH = 255
MAX_NAME_LENGTH = 100


def begin(ctx: AppContext, user_id: int) -> None:
    """Начинает диалог регистрации заново."""

    ctx.registration[user_id] = {"step": LOGIN}


def _valid_login(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= MAX_LOGIN_LENGTH
        and not any(char.isspace() for char in value)
    )


def _split_name(value: str) -> tuple[str, str] | None:
    """Разбирает «Имя\\nФамилия».

    Разделитель — именно перевод строки: в имени и фамилии бывают пробелы,
    и склейка «Иван Иванов» не даёт понять, где кончается имя.
    """

    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if len(lines) != 2:
        return None
    first, last = lines
    if len(first) > MAX_NAME_LENGTH or len(last) > MAX_NAME_LENGTH:
        return None
    return first, last


async def _show_name(event: Any, state: dict[str, Any]) -> None:
    class_name = state.get("class_name")
    header = f"Класс: {esc(class_name)}\n\n" if class_name else ""
    text = header + REGISTER_NAME_CONFIRM_TEXT.format(
        first=esc(state.get("first_name", "")),
        last=esc(state.get("last_name", "")),
    )
    await respond(event, text, keyboard=name_confirm_keyboard())


async def _ask_name(event: Any, user: Any, state: dict[str, Any]) -> None:
    """Подставляет имя из профиля MAX и просит его подтвердить."""

    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    if first and last:
        state["first_name"] = first
        state["last_name"] = last
        state["step"] = NAME_CONFIRM
        await _show_name(event, state)
        return

    # В профиле MAX нет полного имени — просим ввести вручную.
    state["step"] = NAME_EDIT
    await respond(event, REGISTER_NAME_EDIT_TEXT)


async def handle_text(
    ctx: AppContext, event: Any, user: Any, text: str
) -> bool:
    """Обрабатывает шаг диалога. ``True`` — сообщение поглощено регистрацией."""

    state = ctx.registration.get(user.user_id)
    if state is None:
        return False

    answer = text.strip()
    step = state.get("step")

    if step == LOGIN:
        if not _valid_login(answer):
            await respond(event, REGISTER_LOGIN_INVALID_TEXT)
            return True
        try:
            available = await ctx.backend.login_available(answer)
        except BackendError:
            await respond(event, REGISTER_CHECK_FAILED_TEXT)
            return True
        if not available:
            await respond(event, REGISTER_LOGIN_EXISTS_TEXT)
            return True
        state["login"] = answer
        state["step"] = INVITE
        await respond(event, REGISTER_INVITE_TEXT)
        return True

    if step == INVITE:
        if not answer:
            await respond(event, REGISTER_INVITE_TEXT)
            return True
        try:
            info = await ctx.backend.invite_code_info(answer)
        except BackendError:
            await respond(event, REGISTER_INVITE_CHECK_FAILED_TEXT)
            return True
        if not info.get("valid"):
            await respond(event, REGISTER_INVITE_INVALID_TEXT)
            return True
        state["invite_code"] = answer
        state["role"] = info.get("role")
        state["class_name"] = info.get("class_name")
        await _ask_name(event, user, state)
        return True

    if step == NAME_EDIT:
        parsed = _split_name(answer)
        if parsed is None:
            await respond(event, REGISTER_NAME_BAD_TEXT)
            return True
        state["first_name"], state["last_name"] = parsed
        state["step"] = NAME_CONFIRM
        # Переспрашиваем: бот мог понять имя не так, как его ввели.
        await _show_name(event, state)
        return True

    # Ждём нажатия кнопки, а пришёл текст — показываем подтверждение снова.
    await _show_name(event, state)
    return True


async def handle_callback(
    ctx: AppContext, event: Any, payload: str
) -> bool:
    """Кнопки ``reg:name:ok`` и ``reg:name:edit``."""

    user = event_user(event)
    if user is None:
        return False
    state = ctx.registration.get(user.user_id)
    if state is None:
        return False

    action = payload.rsplit(":", 1)[-1]
    if action == "edit":
        state["step"] = NAME_EDIT
        await respond(event, REGISTER_NAME_EDIT_TEXT)
        return True
    if action == "ok":
        await _submit(ctx, event, user, state)
        return True
    return False


async def _submit(
    ctx: AppContext, event: Any, user: Any, state: dict[str, Any]
) -> None:
    hashed = hash_user_id(user.user_id)
    try:
        payload = await ctx.backend.max_register(
            hashed,
            login=str(state.get("login", "")),
            invite_code=str(state.get("invite_code", "")),
            first_name=str(state.get("first_name", "")),
            last_name=str(state.get("last_name", "")),
        )
    except BackendError as exc:
        await _recover(event, state, exc)
        return

    ctx.registration.pop(user.user_id, None)
    token = str(payload.get("access_token", ""))
    if token:
        ctx.sessions.remember(user.user_id, token, payload)
    await ctx.store.save()

    first_name = str(payload.get("first_name") or state.get("first_name", ""))
    await respond(
        event,
        REGISTER_WELCOME_TEXT.format(name=esc(first_name)),
        keyboard=main_menu(payload.get("role")),
    )


async def _recover(
    event: Any,
    state: dict[str, Any],
    exc: BackendError,
) -> None:
    """Возвращает диалог на шаг, который нужно исправить.

    ``400`` от бэкенда означает занятый логин или мёртвый инвайт-код;
    остальные ошибки (сеть, 5xx) оставляют пользователя на подтверждении
    имени, чтобы он мог просто нажать кнопку ещё раз.
    """

    detail = (exc.detail or "").lower()
    if exc.status == 400 and "login" in detail:
        state.clear()
        state["step"] = LOGIN
        await respond(event, REGISTER_LOGIN_EXISTS_TEXT)
        return
    if exc.status == 400 and "invite" in detail:
        for key in ("invite_code", "role", "class_name"):
            state.pop(key, None)
        state["step"] = INVITE
        await respond(event, REGISTER_INVITE_INVALID_TEXT)
        return
    await respond(event, REGISTER_NAME_FAILED_TEXT)
