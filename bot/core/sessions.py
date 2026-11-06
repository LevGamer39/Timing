"""Сессии пользователей: JWT в памяти и повторная привязка по хешу."""

from __future__ import annotations

import logging
from typing import Any

from core.security import hash_user_id, mask_token
from core.state import StateStore
from services.backend import Backend, BackendError

logger = logging.getLogger("timing_bot.sessions")


class SessionManager:
    """Кэш JWT по MAX user_id.

    Токен не пишется на диск. После перезапуска бот заново получает JWT
    у бэкенда по стабильному хешу MAX user_id.
    """

    def __init__(self, backend: Backend, store: StateStore) -> None:
        self._backend = backend
        self._store = store
        self._tokens: dict[int, str] = {}
        self._token_owners: dict[str, int] = {}

    async def token(
        self, max_user_id: int, *, force: bool = False
    ) -> str | None:
        if not force and max_user_id in self._tokens:
            return self._tokens[max_user_id]

        hashed = hash_user_id(max_user_id)
        try:
            payload = await self._backend.max_link(hashed)
        except BackendError as exc:
            if exc.status in {403, 404}:
                logger.info(
                    "MAX user %s не привязан к аккаунту (%s)",
                    max_user_id,
                    exc.detail,
                )
                return None
            logger.warning("Не удалось получить JWT: %s", exc)
            return None

        token = str(payload["access_token"])
        self.remember(max_user_id, token, payload)
        logger.info(
            "Сессия MAX user %s: role=%s token=%s",
            max_user_id,
            payload.get("role"),
            mask_token(token),
        )
        return token

    def remember(
        self, max_user_id: int, token: str, payload: dict[str, Any]
    ) -> None:
        """Сохраняет JWT и данные профиля из ответа бэкенда.

        Используется и при обычном входе по привязке, и сразу после
        регистрации из бота: второй запрос ``/auth/max-link`` не нужен.
        """
        previous = self._tokens.get(max_user_id)
        if previous:
            self._token_owners.pop(previous, None)
        self._tokens[max_user_id] = token
        self._token_owners[token] = max_user_id
        self._store.upsert_user(
            max_user_id,
            role=payload.get("role"),
            first_name=payload.get("first_name"),
            last_name=payload.get("last_name"),
            linked=True,
        )

    async def refresh(self, stale_token: str) -> str | None:
        """Меняет истёкший JWT и возвращает новый.

        Вызывается HTTP-клиентом при 401, чтобы бот не застревал с мёртвым
        токеном до перезапуска.
        """
        max_user_id = self._token_owners.pop(stale_token, None)
        if max_user_id is None:
            return None
        self._tokens.pop(max_user_id, None)
        return await self.token(max_user_id, force=True)

    def role(self, max_user_id: int) -> str | None:
        user = self._store.user(max_user_id)
        return user.get("role") if user else None
