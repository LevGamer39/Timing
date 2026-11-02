"""Общий контекст приложения, который получают все роутеры."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from maxapi import Bot

from core.sessions import SessionManager
from core.state import StateStore
from services.backend import Backend
from services.homework import HomeworkParser


@dataclass(slots=True)
class AppContext:
    """Зависимости, общие для всех обработчиков."""

    bot: Bot
    backend: Backend
    store: StateStore
    sessions: SessionManager
    parser: HomeworkParser
    ai_mode: set[int] = field(default_factory=set)
    notified_unlinked: set[int] = field(default_factory=set)
    registration: dict[int, dict[str, Any]] = field(default_factory=dict)
    link: dict[int, dict[str, Any]] = field(default_factory=dict)
