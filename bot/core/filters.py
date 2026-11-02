"""Фильтры событий MAX."""

from __future__ import annotations

from typing import Any

from maxapi.filters.filter import BaseFilter


class PayloadPrefix(BaseFilter):
    """Пропускает callback-кнопки с нужным префиксом payload."""

    def __init__(self, *prefixes: str) -> None:
        self._prefixes = prefixes

    async def __call__(self, event: Any) -> bool:
        callback = getattr(event, "callback", None)
        payload = getattr(callback, "payload", None) or ""
        return payload.startswith(self._prefixes)
