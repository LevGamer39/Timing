"""HTTP-клиент к бэкенду VectorVK.

Бот не подключается к PostgreSQL и не знает паролей БД. Все операции идут
через существующее API бэкенда. Здесь собраны только те методы, которые
реально нужны боту, — чтобы пути и схемы не расползались по обработчикам.
"""

from __future__ import annotations

from typing import Any

import httpx

from config import settings
from security import sign_request


class BackendError(Exception):
    """Ошибка бэкенда с человекочитаемым ``detail``."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class Unauthorized(BackendError):
    """Токен истёк или отозван."""


def _detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200] or f"HTTP {response.status_code}"

    detail = payload.get("detail") if isinstance(payload, dict) else None
    if detail is None:
        return f"HTTP {response.status_code}"
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                parts.append(str(item.get("msg", item)))
            else:
                parts.append(str(item))
        return "; ".join(parts)
    return str(detail)


class Backend:
    """Тонкая обёртка над httpx с единой обработкой ошибок."""

    def __init__(self, base_url: str | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url or settings.backend_url,
            timeout=settings.backend_timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        **kwargs: Any,
    ) -> Any:
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"

        response = await self._client.request(method, path, headers=headers, **kwargs)

        if response.status_code == 401:
            raise Unauthorized(401, _detail(response))
        if response.status_code >= 400:
            raise BackendError(response.status_code, _detail(response))
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # ── Аутентификация ────────────────────────────────────────────────────

    async def max_link(self, hashed_user_id: str) -> dict[str, Any]:
        """Меняет хеш MAX user_id на JWT. Только для запросов от бота."""

        return await self._request(
            "POST",
            "/api/users/auth/max-link",
            json={"max_user_id_hashed": hashed_user_id},
            headers={"X-Bot-Signature": sign_request(hashed_user_id)},
        )

    async def link_max(self, token: str, hashed_user_id: str) -> None:
        """Привязывает MAX к аккаунту, в который уже выполнен вход."""

        await self._request(
            "POST",
            "/api/users/auth/link-max",
            token=token,
            params={"max_user_id_hashed": hashed_user_id},
        )

    async def me(self, token: str) -> dict[str, Any]:
        return await self._request("GET", "/api/users/me", token=token)

    # ── Задания ───────────────────────────────────────────────────────────

    async def list_tasks(
        self, token: str, status: str | None = None
    ) -> list[dict[str, Any]]:
        params = {"status": status} if status else None
        return await self._request(
            "GET", "/api/tasks", token=token, params=params
        )

    async def create_task(
        self, token: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/tasks", token=token, json=payload
        )

    async def complete_task(self, token: str, task_id: int) -> dict[str, Any]:
        return await self._request(
            "POST", f"/api/tasks/{task_id}/complete", token=token
        )

    async def delete_task(self, token: str, task_id: int) -> None:
        await self._request(
            "DELETE", f"/api/tasks/{task_id}", token=token
        )

    async def child_tasks(
        self, token: str, student_id: int
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET", f"/api/tasks/child/{student_id}", token=token
        )

    # ── Оценки ────────────────────────────────────────────────────────────

    async def my_grades(self, token: str) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/grades/my", token=token)

    async def my_averages(self, token: str) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/grades/my/averages", token=token)

    async def my_grade_summary(self, token: str) -> dict[str, Any]:
        return await self._request("GET", "/api/grades/my/summary", token=token)

    async def child_grades(
        self, token: str, student_id: int
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET", f"/api/grades/child/{student_id}", token=token
        )

    # ── Расписание ────────────────────────────────────────────────────────

    async def my_schedule(self, token: str) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/schedule/my", token=token)

    async def child_schedule(
        self, token: str, student_id: int
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET", f"/api/schedule/child/{student_id}", token=token
        )

    # ── ИИ и родительские связи ───────────────────────────────────────────

    async def ai_chat(self, token: str, message: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/ai/chat", token=token, json={"message": message}
        )

    async def children(self, token: str) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/users/me/children", token=token)

    async def my_classes(self, token: str) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/classes", token=token)


__all__ = [
    "Backend",
    "BackendError",
    "Unauthorized",
]
