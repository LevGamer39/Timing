"""HTTP-клиент к бэкенду VectorVK."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import httpx

from config import settings
from core.security import sign_request

UnauthorizedHandler = Callable[[str], Awaitable[str | None]]


class BackendError(Exception):
    """Ошибка бэкенда с человекочитаемым ``detail``."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class Unauthorized(BackendError):
    """Токен истёк или больше не принимается."""


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
        self._unauthorized_handler: UnauthorizedHandler | None = None

    def set_unauthorized_handler(
        self, handler: UnauthorizedHandler
    ) -> None:
        """Регистрирует обновление JWT по истёкшему токену."""
        self._unauthorized_handler = handler

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        _retry: bool = True,
        **kwargs: Any,
    ) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        extra_headers = dict(headers)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            response = await self._client.request(
                method, path, headers=headers, **kwargs
            )
        except httpx.RequestError as exc:
            # Сетевые сбои раньше вылетали мимо BackendError и обрывали
            # обработчик без ответа пользователю.
            raise BackendError(
                503, f"Бэкенд недоступен: {exc.__class__.__name__}"
            ) from exc

        if response.status_code == 401:
            if token and _retry and self._unauthorized_handler is not None:
                fresh = await self._unauthorized_handler(token)
                if fresh and fresh != token:
                    return await self._request(
                        method,
                        path,
                        token=fresh,
                        _retry=False,
                        headers=extra_headers,
                        **kwargs,
                    )
            raise Unauthorized(401, _detail(response))
        if response.status_code >= 400:
            raise BackendError(response.status_code, _detail(response))
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise BackendError(502, "Бэкенд вернул некорректный ответ") from exc

    async def max_link(self, hashed_user_id: str) -> dict[str, Any]:
        """Меняет подписанный хеш MAX user_id на JWT."""

        return await self._request(
            "POST",
            "/api/users/auth/max-link",
            json={"max_user_id_hashed": hashed_user_id},
            headers={"X-Bot-Signature": sign_request(hashed_user_id)},
        )

    async def link_web_account(
        self, hashed_user_id: str, login: str, password: str
    ) -> dict[str, Any]:
        """Привязывает MAX к готовому веб-аккаунту по логину и паролю."""
        payload = await self._request(
            "POST",
            "/api/users/login",
            data={"username": login, "password": password},
        )
        token = str(payload["access_token"])
        await self._request(
            "POST",
            "/api/users/auth/link-max",
            token=token,
            params={"max_user_id_hashed": hashed_user_id},
        )
        return payload

    async def login_available(self, login: str) -> bool:
        """Свободен ли логин при регистрации (регистр не важен)."""

        payload = await self._request(
            "GET", "/api/users/login-available", params={"login": login}
        )
        return bool(payload.get("available"))

    async def invite_code_info(self, code: str) -> dict[str, Any]:
        """Существует ли инвайт-код и для какой роли он выдан."""

        return await self._request(
            "GET", "/api/users/invite-code", params={"code": code}
        )

    async def max_register(
        self,
        hashed_user_id: str,
        *,
        login: str,
        password: str,
        invite_code: str,
        first_name: str,
        last_name: str,
    ) -> dict[str, Any]:
        """Регистрирует аккаунт из MAX и сразу выдаёт JWT."""

        return await self._request(
            "POST",
            "/api/users/auth/max-register",
            json={
                "max_user_id_hashed": hashed_user_id,
                "login": login,
                "password": password,
                "invite_code": invite_code,
                "first_name": first_name,
                "last_name": last_name,
            },
            headers={"X-Bot-Signature": sign_request(hashed_user_id)},
        )

    async def issue_max_code(self, hashed_user_id: str) -> dict[str, Any]:
        """Одноразовый код для входа в SPA (10 минут)."""

        return await self._request(
            "POST",
            "/api/users/auth/max-code/issue",
            json={"max_user_id_hashed": hashed_user_id},
            headers={"X-Bot-Signature": sign_request(hashed_user_id)},
        )

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
        await self._request("DELETE", f"/api/tasks/{task_id}", token=token)

    async def child_tasks(
        self, token: str, student_id: int
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET", f"/api/tasks/child/{student_id}", token=token
        )

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

    async def my_schedule(self, token: str) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/schedule/my", token=token)

    async def ai_chat(self, token: str, message: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/ai/chat", token=token, json={"message": message}
        )

    async def children(self, token: str) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/users/me/children", token=token)

    async def my_classes(self, token: str) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/classes", token=token)

    async def pending_notifications(self, token: str) -> list[dict[str, Any]]:
        """Очередь уведомлений для MAX (события из API)."""
        return await self._request(
            "GET", "/api/notifications/max/pending", token=token
        )

    async def mark_delivered(self, token: str, ids: list[int]) -> None:
        await self._request(
            "POST",
            "/api/notifications/max/delivered",
            token=token,
            json={"ids": ids},
        )

    async def update_profile(
        self, token: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "PATCH", "/api/users/me", token=token, json=payload
        )

    async def profile(self, token: str) -> dict[str, Any]:
        return await self._request("GET", "/api/users/me", token=token)

    async def weekly_stats(self, token: str) -> dict[str, Any]:
        return await self._request("GET", "/api/stats/weekly/my", token=token)

    async def child_weekly_stats(
        self, token: str, student_id: int
    ) -> dict[str, Any]:
        return await self._request(
            "GET", f"/api/stats/weekly/child/{student_id}", token=token
        )


__all__ = ["Backend", "BackendError", "Unauthorized"]
