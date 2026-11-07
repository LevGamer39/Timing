"""Сборка писем: безопасность заголовков, экранирование HTML, выбор транспорта.

Живой SMTP-сервер здесь не нужен: тесты проверяют MIME-сообщение, которое
собирает :func:`send_email`, перехватывая его на границе транспорта.
"""
import smtplib

import pytest

from app import config
from app.config import starttls_for_port
from app.routers import notifications
from app.routers.notifications import (
    _escape,
    _header,
    _notification_html,
    _overdue_task_html,
    _password_reset_html,
    send_email,
)


class _Captured:
    """Заглушка вместо smtplib.SMTP/SMTP_SSL, записывающая переданное ей."""

    instances: list["_Captured"] = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in = None
        self.sent: list = []
        _Captured.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, message):
        self.sent.append(message)


@pytest.fixture(autouse=True)
def valid_sender(monkeypatch):
    """Фиксирует настроенного отправителя для каждого теста в этом модуле.

    EMAIL_FROM приходит из .env и намеренно пуст, пока не заведён реальный
    почтовый ящик. Без этой фикстуры сьют проходил бы или падал в зависимости
    от локального файла разработчика.
    """
    monkeypatch.setattr(config, "EMAIL_FROM", "noreply@example.org")


@pytest.fixture()
def smtp(monkeypatch):
    """Перехватывает исходящую почту вместо её доставки."""
    _Captured.instances.clear()
    monkeypatch.setattr(notifications.smtplib, "SMTP", _Captured)
    monkeypatch.setattr(notifications.smtplib, "SMTP_SSL", _Captured)
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.org")
    monkeypatch.setattr(config, "EMAIL_FROM", "noreply@timing.myftp.org")
    monkeypatch.setattr(config, "SMTP_TIMEOUT", 15)
    return _Captured


# Выбор транспорта


@pytest.mark.parametrize(
    "port,use_ssl,use_starttls",
    [
        (465, True, False),     # неявный TLS
        (587, False, True),     # сабмишен + STARTTLS
        (2525, False, False),   # открытый
        (25, False, False),
    ],
)
def test_transport_follows_the_port(port, use_ssl, use_starttls):
    assert (port == 465) is use_ssl
    assert starttls_for_port(port) is use_starttls


def test_port_465_uses_implicit_tls(smtp, monkeypatch):
    """Регрессия: SMTP+STARTTLS на 465 подключиться не может."""
    monkeypatch.setattr(config, "SMTP_PORT", 465)
    monkeypatch.setattr(config, "SMTP_USE_SSL", True)
    monkeypatch.setattr(config, "SMTP_USE_STARTTLS", False)
    monkeypatch.setattr(config, "SMTP_USER", "")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "")

    send_email("a@example.org", "Subject", "<p>body</p>")

    assert len(smtp.instances) == 1
    session = smtp.instances[0]
    assert session.port == 465
    assert session.started_tls is False
    assert session.logged_in is None


def test_starttls_used_on_587(smtp, monkeypatch):
    monkeypatch.setattr(config, "SMTP_PORT", 587)
    monkeypatch.setattr(config, "SMTP_USE_SSL", False)
    monkeypatch.setattr(config, "SMTP_USE_STARTTLS", True)
    monkeypatch.setattr(config, "SMTP_USER", "u")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "p")

    send_email("a@example.org", "Subject", "<p>body</p>")

    session = smtp.instances[0]
    assert session.started_tls is True
    assert session.logged_in == ("u", "p")


def test_no_credentials_means_no_login(smtp, monkeypatch):
    monkeypatch.setattr(config, "SMTP_USE_SSL", False)
    monkeypatch.setattr(config, "SMTP_USE_STARTTLS", False)
    monkeypatch.setattr(config, "SMTP_USER", "")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "")

    send_email("a@example.org", "Subject", "<p>body</p>")
    assert smtp.instances[0].logged_in is None


def test_unconfigured_smtp_raises(monkeypatch):
    monkeypatch.setattr(config, "SMTP_HOST", "")
    with pytest.raises(RuntimeError, match="not configured"):
        send_email("a@example.org", "Subject", "<p>body</p>")


def test_missing_sender_raises(monkeypatch):
    """Пустой отправитель был бы отвергнут провайдером, поэтому падаем здесь сразу.

    Провайдеры требуют, чтобы From совпадал с аутентифицированным ящиком.
    Отправка с пустым или захардкоженным адресом падает намного позже и
    выглядит как непрозрачный отказ SMTP.
    """
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.org")
    monkeypatch.setattr(config, "EMAIL_FROM", "")
    with pytest.raises(RuntimeError, match="EMAIL_FROM"):
        send_email("a@example.org", "Subject", "<p>body</p>")


def test_smtp_failure_is_wrapped(monkeypatch):
    """Планировщик опирается на RuntimeError, чтобы отметить элемент батча неудачным."""

    class Boom:
        def __init__(self, *a, **k):
            raise OSError("connection refused")

    monkeypatch.setattr(notifications.smtplib, "SMTP", Boom)
    monkeypatch.setattr(notifications.smtplib, "SMTP_SSL", Boom)
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.org")
    with pytest.raises(RuntimeError, match="SMTP error"):
        send_email("a@example.org", "Subject", "<p>body</p>")


# Безопасность заголовков


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Просто тема", "Просто тема"),
        ("ДЗ\r\nBcc: attacker@evil.com", "ДЗ Bcc: attacker@evil.com"),
        ("ДЗ\nX-Injected: yes", "ДЗ X-Injected: yes"),
        ("ДЗ\r\n\r\nтело", "ДЗ тело"),
        ("  края  ", "края"),
    ],
)
def test_header_strips_line_breaks(raw, expected):
    assert _header(raw) == expected


def test_multiline_title_still_produces_a_mail(smtp, monkeypatch):
    """CRLF в названии задачи раньше приводил к HeaderParseError и потере письма."""
    monkeypatch.setattr(config, "SMTP_USE_SSL", False)
    monkeypatch.setattr(config, "SMTP_USE_STARTTLS", False)

    send_email(
        "a@example.org",
        "Просрочена задача: многострочное\r\nимя",
        "<p>body</p>",
    )

    message = smtp.instances[0].sent[0]
    raw = message.as_bytes().decode("utf-8", "replace")
    head = raw.split("\r\n\r\n")[0]
    injected = [
        line for line in head.splitlines()
        if line.lower().startswith(("bcc:", "x-injected:"))
    ]
    assert injected == [], injected


# Экранирование HTML


def test_escape_handles_quotes_and_tags():
    assert "<" not in _escape("<b>")
    assert '"' not in _escape('" onload=')


def test_overdue_template_escapes_the_title():
    """Тег должен быть обезврежен, а не просто отсутствовать.

    Экранирование превращает ``<`` в ``&lt;``, поэтому буквальный текст
    ``onerror=`` законно сохраняется — важно лишь, что это больше не тег.
    """
    html = _overdue_task_html('<img src=x onerror="alert(1)">', "15.10.2026")
    assert "<img" not in html
    assert "&lt;img" in html
    assert 'onerror=&quot;' in html


def test_notification_template_escapes_title_and_body():
    html = _notification_html("<script>alert(1)</script>", "<b>hi</b>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<b>hi</b>" not in html
    assert "&lt;b&gt;hi&lt;/b&gt;" in html


def test_reset_template_escapes_the_link():
    html = _password_reset_html('https://x/"><script>alert(1)</script>')
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_escaped_payload_is_still_readable():
    html = _overdue_task_html("Физика & математика", "15.10.2026")
    assert "Физика &amp; математика" in html


# Очередь MAX и недельная сводка


def test_notification_dedupes_by_key(db, student):
    from app.models import Notification, NotificationChannel

    first = notifications.create_notification(
        db,
        student.id,
        "Ждут проверки",
        "есть работы",
        channel=NotificationChannel.max,
        dedupe_key="waiting:1:2026-11-05-10",
    )
    second = notifications.create_notification(
        db,
        student.id,
        "Ждут проверки",
        "есть работы",
        channel=NotificationChannel.max,
        dedupe_key="waiting:1:2026-11-05-10",
    )

    assert first is not None
    assert second is None
    assert db.query(Notification).count() == 1


def test_notify_max_off_skips_event(db, student):
    from app.models import Notification, NotificationChannel

    student.notify_max = False
    db.commit()

    created = notifications.create_notification(
        db,
        student.id,
        "Работа принята",
        "«§12» — Math.",
        channel=NotificationChannel.max,
    )
    assert created is None
    assert db.query(Notification).count() == 0


def test_pending_returns_only_own_max_queue(db, student, student2):
    from app.models import NotificationChannel

    mine = notifications.create_notification(
        db, student.id, "Принято", "задание", channel=NotificationChannel.max
    )
    notifications.create_notification(
        db, student.id, "Письмо", "текст", channel=NotificationChannel.email
    )
    notifications.create_notification(
        db, student2.id, "Принято", "задание", channel=NotificationChannel.max
    )

    pending = notifications.max_pending(student, db)
    assert [row.id for row in pending] == [mine.id]


def test_delivered_marks_rows_sent(db, student):
    from app.models import NotificationChannel

    row = notifications.create_notification(
        db, student.id, "Принято", "задание", channel=NotificationChannel.max
    )
    notifications.max_delivered(
        notifications.DeliveredBody(ids=[row.id]), student, db
    )
    db.refresh(row)
    assert row.is_sent is True
    assert row.sent_at is not None


def test_weekly_email_text_uses_week_numbers(
    db, student, make_assignment, make_task
):
    from datetime import datetime, timedelta, timezone

    assignment = make_assignment()
    task = make_task(
        student.id,
        assignment_id=assignment.id,
        is_personal=False,
        deadline=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    task.completed_at = task.deadline
    db.commit()

    text = notifications.weekly_email_text(db, student)
    assert "заданий 1" in text
    assert "в срок 1" in text


def test_weekly_email_text_is_empty_without_tasks(db, student):
    assert notifications.weekly_email_text(db, student) is None