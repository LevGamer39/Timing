"""HTML-шаблоны писем, отправка по SMTP и планировщик уведомлений."""
import asyncio
import html
import logging
import re
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import config
from app.database import SessionLocal, get_db
from app.dependencies import get_current_user
from app.models import (
    Notification,
    NotificationChannel,
    Task,
    TaskStatus,
    User,
)

logger = logging.getLogger("vector.notifications")

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

# Создаётся заново для каждого цикла событий. Модульный синглтон после
# остановки продолжал держать ссылку на закрытый цикл, и следующий старт
# приложения (или следующий тест) падал с "Event loop is closed".
scheduler: AsyncIOScheduler | None = None

NOTIFICATION_PAGE_LIMIT = 100

# Политика повторов для джоба разбора очереди писем.
RETRY_WINDOW_HOURS = 24
# Пауза перед попыткой N+1, в минутах. Последнее значение повторяется.
RETRY_BACKOFF_MINUTES = (5, 15, 60, 240, 720)
BATCH_LIMIT = 200

# Незавершённые статусы, которые ещё можно пометить просроченными.
# Сами ``overdue`` и ``done`` сюда не входят.
OPEN_STATUSES = (TaskStatus.pending, TaskStatus.in_progress)


def _header(value: str) -> str:
    """Делает значение безопасным для заголовка письма.

    Заголовок задачи с CRLF заставил бы стандартную библиотеку бросить
    ``HeaderParseError`` для ASCII-тем (срабатывает по умолчанию, поэтому
    инъекции в заголовок не проходит), но письмо при этом всё равно
    теряется. Убираем переводы строк, чтобы ручные названия задач
    отправлялись как обычно.
    """
    return re.sub(r"[\r\n]+", " ", str(value)).strip()


def _escape(value: object) -> str:
    """Экранирует подставляемое содержимое для HTML.

    Названия задач и тексты уведомлений пишет пользователь, а раньше они
    попадали в письмо как есть — это позволяло вставить HTML и скрипты
    в тело письма.
    """
    return html.escape(str(value), quote=True)


# ---------------------------------------------------------------------------
# HTML-шаблоны
# ---------------------------------------------------------------------------


def _base_template(title: str, content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_escape(title)}</title>
</head>
<body style="margin:0;padding:0;background:#0f0e13;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f0e13;padding:40px 0;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">
          <tr>
            <td style="background:linear-gradient(135deg,#1a1825 0%,#2d2a3e 100%);border-radius:20px 20px 0 0;padding:32px 40px 24px;border-bottom:1px solid rgba(255,255,255,0.08);">
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td>
                    <div style="display:inline-block;background:linear-gradient(135deg,#8B79FF,#6b5ce7);border-radius:12px;padding:8px 14px;">
                      <span style="color:#fff;font-size:15px;font-weight:700;">Vector</span>
                    </div>
                  </td>
                  <td align="right">
                    <span style="color:rgba(255,255,255,0.3);font-size:12px;">Учебный помощник</span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="background:#18171c;padding:36px 40px;border-left:1px solid rgba(255,255,255,0.05);border-right:1px solid rgba(255,255,255,0.05);">
              {content}
            </td>
          </tr>
          <tr>
            <td style="background:#13121a;border-radius:0 0 20px 20px;padding:20px 40px;border:1px solid rgba(255,255,255,0.05);border-top:none;">
              <p style="margin:0;color:rgba(255,255,255,0.25);font-size:12px;line-height:1.5;">
                Это автоматическое письмо от Vector. Не отвечайте на него.<br>
                © {datetime.now(timezone.utc).year} Vector — учебный планировщик
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _email_verification_html(code: str) -> str:
    content = f"""
      <h1 style="margin:0 0 8px;color:#fff;font-size:24px;font-weight:700;">Подтверждение email</h1>
      <p style="margin:0 0 28px;color:rgba(255,255,255,0.5);font-size:15px;line-height:1.6;">
        Введите этот код на странице подтверждения. Он действителен <strong style="color:rgba(255,255,255,0.8);">15 минут</strong>.
      </p>
      <div style="background:linear-gradient(135deg,rgba(139,121,255,0.15),rgba(107,92,231,0.1));border:1px solid rgba(139,121,255,0.3);border-radius:16px;padding:28px;text-align:center;margin-bottom:28px;">
        <div style="letter-spacing:12px;font-size:40px;font-weight:800;color:#8B79FF;font-variant-numeric:tabular-nums;">{_escape(code)}</div>
      </div>
      <p style="margin:0;color:rgba(255,255,255,0.35);font-size:13px;line-height:1.6;">
        Если вы не регистрировались в Vector — просто проигнорируйте это письмо.
      </p>
    """
    return _base_template("Подтверждение email — Vector", content)


def _password_reset_html(reset_link: str) -> str:
    link = _escape(reset_link)
    content = f"""
      <h1 style="margin:0 0 8px;color:#fff;font-size:24px;font-weight:700;">Сброс пароля</h1>
      <p style="margin:0 0 28px;color:rgba(255,255,255,0.5);font-size:15px;line-height:1.6;">
        Вы запросили сброс пароля. Ссылка действительна <strong style="color:rgba(255,255,255,0.8);">1 час</strong>.
      </p>
      <div style="text-align:center;margin-bottom:28px;">
        <a href="{link}" style="display:inline-block;background:linear-gradient(135deg,#8B79FF,#6b5ce7);color:#fff;font-size:15px;font-weight:600;text-decoration:none;padding:14px 32px;border-radius:12px;">
          Сбросить пароль
        </a>
      </div>
      <p style="margin:0 0 8px;color:rgba(255,255,255,0.3);font-size:12px;">Или скопируйте ссылку в браузер:</p>
      <p style="margin:0;background:rgba(255,255,255,0.05);border-radius:8px;padding:10px 14px;font-size:12px;color:rgba(255,255,255,0.4);word-break:break-all;">{link}</p>
    """
    return _base_template("Сброс пароля — Vector", content)


def _overdue_task_html(task_title: str, deadline_str: str) -> str:
    content = f"""
      <h1 style="margin:0 0 8px;color:#fff;font-size:24px;font-weight:700;">Просроченная задача</h1>
      <p style="margin:0 0 24px;color:rgba(255,255,255,0.5);font-size:15px;line-height:1.6;">
        Время дедлайна вышло. Не затягивай — сдай как можно скорее.
      </p>
      <div style="background:rgba(255,60,60,0.08);border:1px solid rgba(255,60,60,0.25);border-radius:14px;padding:20px 24px;margin-bottom:24px;">
        <div style="color:#fff;font-size:16px;font-weight:600;margin-bottom:4px;">{_escape(task_title)}</div>
        <div style="color:rgba(255,80,80,0.8);font-size:13px;">Дедлайн: {_escape(deadline_str)}</div>
      </div>
      <div style="text-align:center;">
        <a href="{_escape(config.APP_URL)}/student/tasks" style="display:inline-block;background:rgba(255,255,255,0.08);color:rgba(255,255,255,0.7);font-size:14px;text-decoration:none;padding:12px 28px;border-radius:10px;border:1px solid rgba(255,255,255,0.1);">
          Открыть задачи
        </a>
      </div>
    """
    return _base_template("Просроченная задача — Vector", content)


def _notification_html(title: str, body: str) -> str:
    content = f"""
      <h1 style="margin:0 0 8px;color:#fff;font-size:22px;font-weight:700;">{_escape(title)}</h1>
      <p style="margin:0 0 24px;color:rgba(255,255,255,0.6);font-size:15px;line-height:1.7;">{_escape(body)}</p>
      <div style="text-align:center;">
        <a href="{_escape(config.APP_URL)}/dashboard" style="display:inline-block;background:linear-gradient(135deg,#8B79FF,#6b5ce7);color:#fff;font-size:14px;font-weight:600;text-decoration:none;padding:12px 28px;border-radius:10px;">
          Открыть дашборд
        </a>
      </div>
    """
    return _base_template(f"{title} — Vector", content)


# ---------------------------------------------------------------------------
# Отправка
# ---------------------------------------------------------------------------


def send_email(to: str, subject: str, body_html: str) -> None:
    """Отправляет одно HTML-письмо.

    Транспорт берётся из ``config``: неявный TLS для порта 465, STARTTLS
    для 587, обычный SMTP в остальных случаях. Раньше здесь всегда был
    ``SMTP`` + ``starttls()``, а это не работает на 465 и вдобавок падает
    на локальном перехватчике, который не объявляет STARTTLS.
    """
    if not config.SMTP_HOST:
        raise RuntimeError("SMTP_HOST is not configured")
    if not config.EMAIL_FROM:
        raise RuntimeError(
            "EMAIL_FROM is not configured - set it in .env to the same "
            "address as SMTP_USER, otherwise the provider rejects the mail"
        )

    message = MIMEMultipart("alternative")
    message["Subject"] = _header(subject)
    message["From"] = _header(config.EMAIL_FROM)
    message["To"] = _header(to)
    message.attach(MIMEText(body_html, "html", "utf-8"))

    smtp_class = smtplib.SMTP_SSL if config.SMTP_USE_SSL else smtplib.SMTP
    try:
        with smtp_class(
            config.SMTP_HOST,
            config.SMTP_PORT,
            timeout=config.SMTP_TIMEOUT,
        ) as server:
            if config.SMTP_USE_STARTTLS:
                server.starttls()
            if config.SMTP_USER and config.SMTP_PASSWORD:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(message)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"SMTP error: {exc}") from exc


def send_verification_email(to: str, code: str) -> None:
    send_email(to, "Ваш код подтверждения — Vector", _email_verification_html(code))


def send_password_reset_email(to: str, reset_link: str) -> None:
    send_email(to, "Сброс пароля — Vector", _password_reset_html(reset_link))


# ---------------------------------------------------------------------------
# Работа с отложенными отправками
# ---------------------------------------------------------------------------


def _next_attempt_delay(attempts: int) -> int:
    """Сколько минут ждать до следующей попытки; растёт с каждой неудачей."""
    if attempts < len(RETRY_BACKOFF_MINUTES):
        return RETRY_BACKOFF_MINUTES[attempts]
    return RETRY_BACKOFF_MINUTES[-1]


def _attempts_made(notification: Notification, now: datetime) -> int:
    """Сколько раз письмо уже пытались отправить."""
    attempts = getattr(notification, "attempts", None)
    if isinstance(attempts, int):
        return max(0, attempts)
    return 0


def create_notification(
    db: Session,
    user_id: int,
    title: str,
    body: str,
    channel: NotificationChannel = NotificationChannel.browser,
    scheduled_at: datetime | None = None,
    commit: bool = True,
) -> Notification:
    """Ставит уведомление в очередь.

    ``commit=False`` позволяет объединить несколько записей в одну
    транзакцию; раньше сигнатура всегда коммитила, и вызывающий код
    коммитил дважды.
    """
    notification = Notification(
        user_id=user_id,
        title=title,
        body=body,
        channel=channel,
        scheduled_at=scheduled_at,
    )
    db.add(notification)
    if commit:
        db.commit()
    return notification


# ---------------------------------------------------------------------------
# Планировщик
# ---------------------------------------------------------------------------


async def _job_send_pending_emails() -> None:
    """Разбирает очередь писем раз в минуту.

    Неудачи повторяются с растущей паузой и списываются через
    RETRY_WINDOW_HOURS, чтобы недоставляемый адрес не крутился вечно.
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        expiry_cutoff = now - timedelta(hours=RETRY_WINDOW_HOURS)

        pending = (
            db.query(Notification)
            .filter(
                Notification.channel == NotificationChannel.email,
                Notification.is_sent.is_(False),
                (
                    Notification.scheduled_at.is_(None)
                    | (Notification.scheduled_at <= now)
                ),
            )
            .limit(BATCH_LIMIT)
            .all()
        )

        sent = failed = abandoned = 0
        for notification in pending:
            created = notification.created_at
            if created is not None:
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created < expiry_cutoff:
                    # Слишком старое, чтобы быть полезным; больше не пробуем.
                    notification.is_sent = True
                    notification.sent_at = now
                    abandoned += 1
                    continue

            user = (
                db.query(User).filter(User.id == notification.user_id).first()
            )
            if not user or not user.notify_email:
                # Отправлять некуда — закрываем, чтобы очередь не копилась.
                notification.is_sent = True
                notification.sent_at = now
                continue

            try:
                # SMTP синхронный и может висеть до таймаута; в общем цикле
                # событий это останавливает все остальные запросы.
                await asyncio.to_thread(
                    send_email,
                    user.email,
                    notification.title,
                    _notification_html(notification.title, notification.body),
                )
            except Exception as exc:  # noqa: BLE001
                # Один битый адрес не должен обрывать всю пачку.
                failed += 1
                attempts = _attempts_made(notification, now)
                delay = _next_attempt_delay(attempts)
                notification.attempts = attempts + 1
                notification.scheduled_at = now + timedelta(minutes=delay)
                logger.warning(
                    "email to user %s failed (attempt %d, retry in %dm): %s",
                    notification.user_id,
                    attempts + 1,
                    delay,
                    exc,
                )
                continue

            notification.is_sent = True
            notification.sent_at = now
            notification.scheduled_at = None
            sent += 1

        if pending:
            db.commit()
            logger.info(
                "emails sent=%d failed=%d abandoned=%d",
                sent,
                failed,
                abandoned,
            )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("send_emails error: %s", exc)
    finally:
        db.close()


async def _job_check_overdue() -> None:
    """Помечает просроченные задачи и уведомляет владельцев раз в 10 минут."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        overdue = (
            db.query(Task)
            .filter(
                Task.deadline.isnot(None),
                Task.deadline < now,
                Task.status.in_(OPEN_STATUSES),
                Task.parent_task_id.is_(None),
            )
            .limit(500)
            .all()
        )
        if not overdue:
            return

        student_ids = {task.student_id for task in overdue}
        users = {
            user.id: user
            for user in db.query(User).filter(User.id.in_(student_ids)).all()
        }

        mailed = 0
        for task in overdue:
            task.status = TaskStatus.overdue
            deadline_str = task.deadline.astimezone(timezone.utc).strftime(
                "%d.%m.%Y %H:%M"
            )
            create_notification(
                db=db,
                user_id=task.student_id,
                title="Просроченная задача",
                body=(
                    f"«{task.title}» просрочена. "
                    f"Дедлайн был {deadline_str}."
                ),
                channel=NotificationChannel.browser,
                commit=False,
            )

            user = users.get(task.student_id)
            if user and user.notify_email:
                try:
                    await asyncio.to_thread(
                        send_email,
                        user.email,
                        f"Просрочена задача: {task.title} — Vector",
                        _overdue_task_html(task.title, deadline_str),
                    )
                    mailed += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "overdue mail to user %d failed: %s", user.id, exc
                    )

        db.commit()
        logger.info(
            "marked overdue=%d mailed=%d", len(overdue), mailed
        )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("check_overdue error: %s", exc)
    finally:
        db.close()


def start_scheduler() -> None:
    """Регистрирует и запускает фоновые джобы для текущего цикла событий."""
    global scheduler

    if not config.ENABLE_SCHEDULER:
        return
    if scheduler is not None and scheduler.running:
        return

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _job_send_pending_emails,
        "interval",
        minutes=1,
        id="send_emails",
        replace_existing=True,
    )
    scheduler.add_job(
        _job_check_overdue,
        "interval",
        minutes=10,
        id="check_overdue",
        replace_existing=True,
    )
    scheduler.start()


def stop_scheduler() -> None:
    global scheduler

    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=False)
    scheduler = None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class NotificationResponse(BaseModel):
    id: int
    title: str
    body: str
    channel: NotificationChannel
    is_read: bool
    is_sent: bool
    scheduled_at: datetime | None
    sent_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("", response_model=list[NotificationResponse])
def list_notifications(
    unread_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Notification).filter(Notification.user_id == current_user.id)
    if unread_only:
        query = query.filter(Notification.is_read.is_(False))
    return (
        query.order_by(Notification.created_at.desc())
        .limit(NOTIFICATION_PAGE_LIMIT)
        .all()
    )


@router.post("/read-all", status_code=204)
def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.is_read.is_(False),
    ).update({"is_read": True}, synchronize_session=False)
    db.commit()


@router.post("/{notification_id}/read", status_code=204)
def mark_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notification = (
        db.query(Notification)
        .filter(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
        .first()
    )
    if not notification:
        raise HTTPException(404, "Notification not found")
    notification.is_read = True
    db.commit()