"""Единственный источник конфигурации, читаемой из переменных окружения.

Раньше каждый модуль сам вызывал ``load_dotenv()`` и заново читал
``os.environ``. Из-за этого ``ACCESS_TOKEN_EXPIRE_MINUTES`` доезжал до
``timedelta()`` строкой, и любой запрос ``/api/users/login`` падал с
TypeError. Теперь значения парсятся и валидируются ровно один раз — здесь.
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()


def _str(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value is None:
        return None
    value = value.strip()
    return value or default


def _int(name: str, default: int) -> int:
    raw = _str(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(
            f"{name} must be an integer, got {raw!r}"
        ) from None


def _bool(name: str, default: bool = False) -> bool:
    raw = _str(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


# ─── База данных ──────────────────────────────────────────────────────────────
# Полный URL важнее отдельных частей: DATABASE_URL перекрывает их все разом,
# поэтому сменить БД можно одной переменной, не трогая остальной код.
DATABASE_URL = _str("DATABASE_URL")

DB_USER = _str("DB_USER", "user")
DB_PASS = _str("DB_PASS", "")
DB_HOST = _str("DB_HOST", "localhost")
DB_PORT = _int("DB_PORT", 5432)
DB_NAME = _str("DB_NAME", "vectordb")


def build_database_url() -> str:
    if DATABASE_URL:
        return DATABASE_URL
    return (
        f"postgresql://{DB_USER}:{DB_PASS}"
        f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )

# ─── Админ при первом запуске ──────────────────────────────────────────────────
ADMIN_EMAIL = _str("ADMIN_EMAIL", "admin@vector.local")
ADMIN_PASSWORD = _str("ADMIN_PASSWORD")
ADMIN_FIRST_NAME = _str("ADMIN_FIRST_NAME", "Admin")
ADMIN_LAST_NAME = _str("ADMIN_LAST_NAME", "Vector")

# ─── Авторизация ──────────────────────────────────────────────────────────────
SECRET_KEY = _str("SECRET_KEY")
ALGORITHM = _str("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = _int("ACCESS_TOKEN_EXPIRE_MINUTES", 10080)

def starttls_for_port(port: int) -> bool:
    """Нужен ли STARTTLS на этом порту по умолчанию.

    465 — неявный TLS (SMTP_SSL, STARTTLS не участвует). 587 — порт
    отправки, там ожидается STARTTLS. Все остальные порты считаются
    обычным SMTP без шифрования.
    """
    return port == 587


# ─── Почта ────────────────────────────────────────────────────────────────────
APP_URL = _str("APP_URL", "http://localhost:8000")
SMTP_HOST = _str("SMTP_HOST", "smtp.yandex.ru")
SMTP_PORT = _int("SMTP_PORT", 587)
SMTP_USER = _str("SMTP_USER", "")
SMTP_PASSWORD = _str("SMTP_PASSWORD", "")
# Пустое значение намеренно остаётся пустым: _str() подставил бы дефолт,
# а любой отправитель, отличный от авторизованного ящика, отвергается
# почтовым провайдером.
EMAIL_FROM = _str("EMAIL_FROM")

# Режим TLS определяется портом и переопределяется через SMTP_STARTTLS:
#   465 -> неявный TLS, smtplib.SMTP_SSL
#   587 -> STARTTLS
#   любой другой порт -> обычный SMTP без TLS
# Переопределение нужно для локальных перехватчиков: они не объявляют
# STARTTLS, и вызов starttls() на них падает.
SMTP_USE_SSL = SMTP_PORT == 465
SMTP_USE_STARTTLS = _bool("SMTP_STARTTLS", starttls_for_port(SMTP_PORT))
SMTP_TIMEOUT = _int("SMTP_TIMEOUT", 15)

# ─── ИИ ───────────────────────────────────────────────────────────────────────
OLLAMA_URL = _str("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = _str("OLLAMA_MODEL", "qwen2.5:7b")

# ─── OAuth Яндекса ────────────────────────────────────────────────────────────
YANDEX_CLIENT_ID = _str("YANDEX_CLIENT_ID", "")
YANDEX_CLIENT_SECRET = _str("YANDEX_CLIENT_SECRET", "")
YANDEX_LOGIN_CALLBACK = _str(
    "YANDEX_LOGIN_CALLBACK", f"{APP_URL}/api/auth/yandex/callback"
)
YANDEX_LINK_CALLBACK = _str(
    "YANDEX_LINK_CALLBACK", f"{APP_URL}/api/auth/yandex/confirm-link"
)

# ─── Прочее ───────────────────────────────────────────────────────────────────
DEBUG = _bool("DEBUG", False)

# ─── MAX Bot ──────────────────────────────────────────────────────────────────
# Токен нужен только для проверки подписи запросов от бота к /auth/max-link.
# Это тот же MAX_BOT_TOKEN, который использует сервис bot/.
MAX_BOT_TOKEN = _str("MAX_BOT_TOKEN", "")

# Фоновые джобы: очередь писем и проверка просрочек. Выключаются в тестах и
# при разовых командах управления. С несколькими воркерами uvicorn каждый
# поднял бы свои джобы, поэтому держи включённым только на одном процессе —
# именно так запускается docker-compose.
ENABLE_SCHEDULER = _bool("ENABLE_SCHEDULER", True)

_REQUIRED = [
    ("SECRET_KEY", SECRET_KEY),
    ("ADMIN_PASSWORD", ADMIN_PASSWORD),
]
_missing = [name for name, value in _REQUIRED if not value]
if _missing and not DEBUG:
    print(
        "[config] WARNING: missing required env vars: "
        + ", ".join(_missing)
        + " (set them, or DEBUG=true to silence)",
        file=sys.stderr,
    )

# SECRET_KEY должен быть достаточно длинным для безопасного HS256.
if SECRET_KEY and len(SECRET_KEY) < 32 and not DEBUG:
    print(
        "[config] WARNING: SECRET_KEY shorter than 32 characters "
        "(HS256 requires a strong secret)",
        file=sys.stderr,
    )