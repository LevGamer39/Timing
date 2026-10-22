"""Сессии пользователей: JWT в памяти и повторная привязка по хешу."""

from __future__ import annotations

import logging

from api import Backend, BackendError, Unauthorized
from security import hash_user_id, mask_token
from state import StateStore

logger = logging.getLogger("vector_bot.sessions")


class SessionManager:
    """Кэш JWT по MAX user_id.

    Токен не пишется на диск. Если он истёк или бот перезапустился, JWT
    заново выдаётся бэкендом по стабильному хешу MAX user_id.
    """

    def __init__(self, backend: Backend, store: StateStore) -> None:
        self._backend = backend
        self._store = store
        self._tokens: dict[int, str] = {}

    def drop(self, max_user_id: int) -> None:
        self._tokens.pop(max_user_id, None)

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
        self._tokens[max_user_id] = token
        self._store.upsert_user(
            max_user_id,
            role=payload.get("role"),
            first_name=payload.get("first_name"),
            last_name=payload.get("last_name"),
            linked=True,
        )
        logger.info(
            "Сессия MAX user %s: role=%s token=%s",
            max_user_id,
            payload.get("role"),
            mask_token(token),
        )
        return token

    async def authorized(
        self, max_user_id: int, call
    ) -> tuple[object | None, str | None]:
        """Выполняет ``call(token)``, один раз перевыпуская JWT при 401."""

        token = await self.token(max_user_id)
        if token is None:
            return None, None

        try:
            return await call(token), None
        except Unauthorized:
            self.drop(max_user_id)
            token = await self.token(max_user_id, force=True)
            if token is None:
                return None, None
            try:
                return await call(token), None
            except BackendError as exc:
                return None, exc.detail
        except BackendError as exc:
            return None, exc.detail

    def role(self, max_user_id: int) -> str | None:
        user = self._store.user(max_user_id)
        return user.get("role") if user else None
