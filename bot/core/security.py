"""Криптографические помощники бота."""

from __future__ import annotations

import hashlib
import hmac

from config import settings

_WEAK_SALTS = {"", "changeme", "vectorvk-default-salt"}


def _salt() -> bytes:
    """Возвращает соль для хеширования MAX user_id.

    Если в окружении задана нормальная соль — используется она. Иначе соль
    выводится из токена бота, чтобы привязки не ломались после перезапуска.
    """

    raw = settings.user_id_salt.strip()
    if raw not in _WEAK_SALTS and len(raw) >= 16:
        return raw.encode()
    return hmac.new(
        settings.max_bot_token.encode(),
        b"vectorvk:user-id-salt:v1",
        hashlib.sha256,
    ).digest()


def hash_user_id(max_user_id: int | str) -> str:
    """Стабильный необратимый идентификатор MAX-пользователя."""

    normalized = str(int(str(max_user_id).strip())) # " 001 " -> "1"
    return hmac.new(
        _salt(),
        f"max:{normalized}".encode(), # если в будущем будет интеграция не только с MAX
        hashlib.sha256,
    ).hexdigest()


def sign_request(payload: str) -> str:
    """Подпись запроса к бэкенду на общем токене бота."""

    return hmac.new(
        settings.max_bot_token.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()


def mask_token(token: str | None, visible: int = 4) -> str:
    """Маскирует токен для логов."""

    if not token:
        return ""
    if len(token) <= visible:
        return "*" * len(token)
    return token[:visible] + "*" * (len(token) - visible)
