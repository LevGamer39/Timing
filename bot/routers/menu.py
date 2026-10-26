"""Callback-кнопки главного меню."""

from __future__ import annotations

from maxapi import Router
from maxapi.types.updates.message_callback import MessageCallback

from core.context import AppContext
from core.filters import PayloadPrefix
from core.ui import event_user, require_token, respond, role_of
from keyboards import main_menu
from services import registration, views
from texts import HELP_TEXT
from texts.messages import REGISTER_LOGIN_TEXT


def build_router(ctx: AppContext) -> Router:
    router = Router("menu")

    @router.message_callback(PayloadPrefix("menu:"))
    async def menu(event: MessageCallback) -> None:
        action = (event.callback.payload or "")[5:]
        user = event_user(event)

        if user is not None and action != "register":
            ctx.registration.pop(user.user_id, None)

        if action == "register":
            if user is not None:
                registration.begin(ctx, user.user_id)
                # Пустой список вложений очищает клавиатуру: attachments=None
                # в MAX означает «оставить вложения исходного сообщения»,
                # поэтому кнопка «Зарегистрироваться» оставалась висеть.
                await event.answer(
                    new_text=REGISTER_LOGIN_TEXT, attachments=[]
                )
            return
        if action == "add":
            if user is not None:
                # «Добавить» должно выходить из ИИ-чата, иначе следующий
                # текст снова уйдёт в модель вместо создания задания.
                ctx.ai_mode.discard(user.user_id)
            await respond(event, "Пришлите текст задания или голосовое сообщение.")
            return
        if action == "ai":
            if user is not None:
                ctx.ai_mode.add(user.user_id)
            await respond(event, "ИИ-чат включён. Напишите вопрос. /cancel — выход.")
            return
        if action == "help":
            await respond(
                event,
                HELP_TEXT,
                keyboard=main_menu(role_of(ctx, event)),
            )
            return

        token = await require_token(ctx, event)
        if token is None:
            return

        if action == "today":
            result = await views.safe_view(event, views.today(ctx.backend, token))
        elif action == "grades":
            result = await views.safe_view(event, views.grades(ctx.backend, token))
        elif action == "summary":
            result = await views.safe_view(
                event, views.summary(ctx.backend, token, role_of(ctx, event))
            )
        elif action == "schedule":
            result = await views.safe_view(
                event, views.schedule(ctx.backend, token)
            )
        elif action == "children":
            result = await views.safe_view(
                event, views.children(ctx.backend, token)
            )
        elif action == "classes":
            result = await views.safe_view(
                event, views.classes(ctx.backend, token)
            )
        else:
            await respond(event, "Неизвестная кнопка.")
            return

        if result is not None:
            await respond(event, result[0], result[1])

    return router
