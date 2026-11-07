"""Конфигурация MAX-бота.

Единственный источник настроек: значения читаются из окружения (в Docker —
через ``env_file``) и из ``.env`` при локальном запуске. Ни один другой
модуль не обращается к ``os.environ`` напрямую.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _str(name: str, default: str = "") -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw or default


def _int(name: str, default: int) -> int:
    raw = _str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from None


def _float(name: str, default: float) -> float:
    raw = _str(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from None


def _bool(name: str, default: bool = False) -> bool:
    raw = _str(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    """Все настройки бота, разобранные один раз при старте."""

    # MAX Bot API
    max_bot_token: str

    # Внутренний бэкенд Timing
    backend_url: str
    backend_timeout: float

    # Режим получения обновлений
    update_mode: str
    webhook_url: str
    webhook_secret: str
    webhook_host: str
    webhook_port: int
    webhook_path: str

    # Ollama / Qwen
    ollama_url: str
    ollama_model: str
    ollama_timeout: float

    # Парсер домашних заданий
    parser_enabled: bool
    parser_batch_interval_minutes: int
    parser_keyword_filter: bool
    parser_max_batch: int
    parser_voice_enabled: bool

    # Голосовые сообщения (faster-whisper)
    voice_enabled: bool
    whisper_model: str
    whisper_model_dir: str
    whisper_device: str
    whisper_compute_type: str
    whisper_language: str
    audio_temp_dir: str
    voice_download_timeout: float

    # Напоминания и недельная сводка
    reminders_enabled: bool
    reminder_interval_minutes: int
    weekly_summary_enabled: bool
    weekly_summary_weekday: int
    weekly_summary_hour: int

    # Прочее
    state_path: str
    user_id_salt: str
    log_level: str


def load_settings() -> Settings:
    """Собирает настройки из окружения с проверкой типов."""

    update_mode = _str("BOT_UPDATE_MODE", "long_polling").lower()
    if update_mode not in {"long_polling", "webhook"}:
        raise RuntimeError(
            "BOT_UPDATE_MODE must be 'long_polling' or 'webhook', "
            f"got {update_mode!r}"
        )

    weekday = _int("WEEKLY_SUMMARY_WEEKDAY", 6)
    if not 0 <= weekday <= 6:
        raise RuntimeError("WEEKLY_SUMMARY_WEEKDAY must be between 0 and 6")

    hour = _int("WEEKLY_SUMMARY_HOUR", 18)
    if not 0 <= hour <= 23:
        raise RuntimeError("WEEKLY_SUMMARY_HOUR must be between 0 and 23")

    return Settings(
        max_bot_token=_str("MAX_BOT_TOKEN"),
        backend_url=_str("MAX_BOT_URL", "http://backend:8000").rstrip("/"),
        backend_timeout=_float("BOT_BACKEND_TIMEOUT", 30.0),
        update_mode=update_mode,
        webhook_url=_str("WEBHOOK_URL"),
        webhook_secret=_str("WEBHOOK_SECRET"),
        webhook_host=_str("WEBHOOK_HOST", "0.0.0.0"),
        webhook_port=_int("WEBHOOK_PORT", 8080),
        webhook_path=_str("WEBHOOK_PATH", "/max/webhook"),
        ollama_url=_str("OLLAMA_URL", "http://ollama:11434").rstrip("/"),
        ollama_model=_str("OLLAMA_MODEL", "qwen2.5:7b"),
        ollama_timeout=_float("OLLAMA_TIMEOUT", 90.0),
        parser_enabled=_bool("PARSER_ENABLED", True),
        parser_batch_interval_minutes=_int("PARSER_BATCH_INTERVAL_MINUTES", 15),
        parser_keyword_filter=_bool("PARSER_KEYWORD_FILTER", True),
        parser_max_batch=_int("PARSER_MAX_BATCH", 20),
        parser_voice_enabled=_bool("PARSER_VOICE_ENABLED", True),
        voice_enabled=_bool("VOICE_ENABLED", False),
        whisper_model=_str("WHISPER_MODEL", "small"),
        whisper_model_dir=_str("WHISPER_MODEL_DIR", "models"),
        whisper_device=_str("WHISPER_DEVICE", "cpu"),
        whisper_compute_type=_str("WHISPER_COMPUTE_TYPE", "int8"),
        whisper_language=_str("WHISPER_LANGUAGE", "ru"),
        audio_temp_dir=_str("AUDIO_TEMP_DIR", "data/tmp"),
        voice_download_timeout=_float("VOICE_DOWNLOAD_TIMEOUT", 60.0),
        reminders_enabled=_bool("REMINDERS_ENABLED", True),
        reminder_interval_minutes=_int("REMINDER_INTERVAL_MINUTES", 30),
        weekly_summary_enabled=_bool("WEEKLY_SUMMARY_ENABLED", True),
        weekly_summary_weekday=weekday,
        weekly_summary_hour=hour,
        state_path=_str("BOT_STATE_PATH", "data/bot_state.json"),
        user_id_salt=_str("BOT_USER_ID_SALT"),
        log_level=_str("LOG_LEVEL", "INFO").upper(),
    )


settings = load_settings()


def validate() -> None:
    """Падает на старте, если не хватает обязательных настроек."""

    if not settings.max_bot_token:
        raise RuntimeError(
            "MAX_BOT_TOKEN is not set. Put it in the project .env file."
        )
    if settings.update_mode == "webhook" and not settings.webhook_url:
        raise RuntimeError(
            "WEBHOOK_URL must be set when BOT_UPDATE_MODE=webhook"
        )
