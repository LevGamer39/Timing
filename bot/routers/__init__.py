"""Сборка роутеров в порядке обработки."""

from __future__ import annotations

from maxapi import Dispatcher

from core.context import AppContext
from routers import (
    ai_chat,
    common,
    grades,
    homework,
    menu,
    messages,
    schedule,
    tasks,
)


def include_routers(dp: Dispatcher, ctx: AppContext) -> None:
    """Подключает роутеры: команды раньше свободного текста."""
    dp.include_routers(
        common.build_router(ctx),
        tasks.build_router(ctx),
        grades.build_router(ctx),
        schedule.build_router(ctx),
        ai_chat.build_router(ctx),
        homework.build_router(ctx),
        menu.build_router(ctx),
        messages.build_router(ctx),
    )
