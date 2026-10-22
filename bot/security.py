"""Криптографические помощники бота.

Модуль намеренно маленький: он решает три задачи, которые нельзя размазывать
по обработчикам.

1. ``hash_user_id`` — MAX user_id никогда не уходит в бэкенд открытым. Для
   одного и того же пользователя хеш стабилен, поэтому по нему работает
   привязка аккаунта.
2. ``sign_request`` — доказывает бэкенду, что запрос пришёл именно от бота,
   а не от любого, кто узнал хеш. Подпись считается на том же
   ``MAX_BOT_TOKEN``, который уже есть у обоих сервисов.
3. ``mask_token`` — ни один токен не попадает в лог целиком.
"""

from __future__ import annotations

import hashlib
import hmac

from config import settings

_WEAK_SALTS = {"", "changeme", "vectorvk-default-salt"}


def _salt() -> bytes:
    """Возвращает соль для хеширования MAX user_id.

    Если в окружении задана нормальная соль — используется она. Иначе соль
    выводится из токена бота: так привязки не ломаются от перезапуска и не
    зависят от угадываемой константы из исходников.
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

    return hmac.new(
        _salt(),
        str(max_user_id).encode(),
        hashlib.sha256,
    ).hexdigest()


def sign_request(payload: str) -> str:
    """Подпись запроса к бэкенду (HMAC-SHA256 на токене бота)."""

    return hmac.new(
        settings.max_bot_token.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()


def mask_token(token: str | None, visible: int = 4) -> str:
    """Маскирует токен для логов: ``abcd****``."""

    if not token:
        return ""
    if len(token) <= visible:
        return "*" * len(token)
    return token[:visible] + "*" * (len(token) - visible)
