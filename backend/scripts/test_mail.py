#!/usr/bin/env python
"""Отправляет реальное тестовое письмо тем же путём, что и приложение.

Отвечает на вопрос «почта вообще уходит?» без запуска сервера и без
обращения к базе.

    python test_mail.py someone@example.com
    python test_mail.py someone@example.com --template verify
    python test_mail.py --check          # только конфигурация, без отправки

Коды возврата: 0 — отправлено (или нечего отправлять при --check),
1 — ошибка.
"""
import argparse
import smtplib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.routers.notifications import (  # noqa: E402
    _notification_html,
    _overdue_task_html,
    _password_reset_html,
    _email_verification_html,
    send_email,
)

TEMPLATES = {
    "plain": (
        "Тестовое письмо — Vector",
        _notification_html(
            "Тестовое письмо",
            "Если вы читаете это, SMTP настроен верно.",
        ),
    ),
    "verify": (
        "Ваш код подтверждения — Vector",
        _email_verification_html("123456"),
    ),
    "reset": (
        "Сброс пароля — Vector",
        _password_reset_html(
            f"{config.APP_URL}/reset-password-new?token=test-token-123"
        ),
    ),
    "overdue": (
        "Просрочена задача — Vector",
        _overdue_task_html("Математика, параграф 12", "17.10.2026 23:59"),
    ),
}


def _looks_placeholder(value: str | None) -> bool:
    """Истина для незаполненных плейсхолдеров в .env вроде ``<ваш@яндекс.ру>``."""
    if not value:
        return True
    stripped = value.strip()
    if stripped.startswith(("<", "{")):
        return True
    return stripped.lower().startswith(("your", "changeme", "todo", "example"))


def describe_config() -> None:
    transport = (
        "SMTP_SSL (неявный TLS)"
        if config.SMTP_USE_SSL
        else ("SMTP + STARTTLS" if config.SMTP_USE_STARTTLS else "SMTP без TLS")
    )
    print("Конфигурация SMTP")
    print(f"  host        : {config.SMTP_HOST or '— НЕ ЗАДАН —'}")
    print(f"  port        : {config.SMTP_PORT}")
    print(f"  транспорт   : {transport}")

    # Показываем только пригодность — сам пароль в терминал не выводим.
    for label, value in (
        ("SMTP_USER", config.SMTP_USER),
        ("SMTP_PASSWORD", config.SMTP_PASSWORD),
        ("EMAIL_FROM", config.EMAIL_FROM),
    ):
        if value and not _looks_placeholder(value):
            state = "задан"
        elif not value:
            state = "— НЕ ЗАДАН —"
        else:
            state = "ЗАПОЛНЕН ПЛЕЙСХОЛДЕРОМ"
        print(f"  {label:<12} : {state}")

    print(f"  APP_URL     : {config.APP_URL}")

    problems: list[str] = []
    if not config.SMTP_HOST:
        problems.append("SMTP_HOST не задан")
    if config.SMTP_USE_SSL and config.SMTP_PORT != 465:
        problems.append("SMTP_USE_SSL при нестандартном порте")
    if _looks_placeholder(config.EMAIL_FROM):
        problems.append("EMAIL_FROM не заполнен реальным значением")

    # Логин и пароль нужны, только если сервер объявляет AUTH.
    needs_auth = server_requires_auth()
    if needs_auth is True:
        for label, value in (
            ("SMTP_USER", config.SMTP_USER),
            ("SMTP_PASSWORD", config.SMTP_PASSWORD),
        ):
            if _looks_placeholder(value):
                problems.append(
                    f"{label} обязателен: сервер требует авторизацию"
                )
    elif needs_auth is False:
        print("\n  Сервер не требует AUTH - логин и пароль не нужны.")

    if problems:
        print("\nПроблемы конфигурации:")
        for item in problems:
            print(f"  ! {item}")



def server_requires_auth() -> bool | None:
    """Спрашивает у сервера, объявляет ли он AUTH.

    Локальный relay принимает письма из docker-сети без учётных данных, так
    что пустой SMTP_USER там — это правильно, а не ошибка. Реально нужны
    они только серверу с объявленным AUTH. Возвращает None, если сервер
    недоступен.
    """
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as s:
            s.ehlo()
            return bool(s.esmtp_features.get("auth"))
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recipient", nargs="?", help="адрес получателя")
    parser.add_argument(
        "--template",
        choices=sorted(TEMPLATES),
        default="plain",
        help="какое письмо отправить (по умолчанию plain)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="только показать конфигурацию, ничего не отправлять",
    )
    args = parser.parse_args()

    describe_config()

    if args.check:
        usable = bool(config.SMTP_HOST) and not _looks_placeholder(
            config.EMAIL_FROM
        )
        return 0 if usable else 1

    if not args.recipient:
        parser.error("нужен адрес получателя (или флаг --check)")
    if not config.SMTP_HOST:
        print("\nSMTP_HOST не задан — проверь .env", file=sys.stderr)
        return 1

    subject, body = TEMPLATES[args.template]
    print(f"\nОтправляю шаблон '{args.template}' на {args.recipient} ...")

    try:
        send_email(args.recipient, subject, body)
    except Exception as exc:  # noqa: BLE001
        print(f"\nНЕ ОТПРАВЛЕНО: {exc}", file=sys.stderr)
        print(
            "Частые причины:\n"
            "  • нет сети до SMTP-сервера\n"
            "  • неверный пароль (для Яндекса нужен пароль приложения,\n"
            "    а не пароль от аккаунта: id.yandex.ru/security/apppasswords)\n"
            "  • SMTP-сервер требует STARTTLS на другом порту\n"
            "  • аккаунт не включён в .env (SMTP_USER / SMTP_PASSWORD)",
            file=sys.stderr,
        )
        return 1

    print("\nОтправлено. Проверь почту (и папку «Спам»).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())