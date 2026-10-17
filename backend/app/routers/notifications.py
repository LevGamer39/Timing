import smtplib
import asyncio
import os
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.database import get_db, SessionLocal
from app.models import Notification, NotificationChannel, Task, TaskStatus, User, UserRole
from app.routers.users import get_current_user, require_role
from app.dependencies import get_current_user
from dotenv import load_dotenv

load_dotenv()

APP_URL = os.environ.get("APP_URL", "https://vkcollege.ru")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.yandex.ru")
SMTP_PORT = os.environ.get("SMTP_PORT", "465")
SMTP_USER = os.environ.get("SMTP_USER", "your@yandex.ru")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "your-smtp-password")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "noreply@vkcollege.ru")

router = APIRouter(prefix="/api/notifications", tags=["notifications"])
scheduler = AsyncIOScheduler()

class NotificationResponse(BaseModel):
    id: int
    title: str
    body: str
    channel: NotificationChannel
    is_read: bool
    is_sent: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ─── HTML ШАБЛОНЫ ────────────────────────────────────────────────────────────

def _base_template(title: str, content: str) -> str:
    """Базовый HTML-шаблон письма."""
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#0f0e13;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f0e13;padding:40px 0;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">
          <!-- HEADER -->
          <tr>
            <td style="background:linear-gradient(135deg,#1a1825 0%,#2d2a3e 100%);border-radius:20px 20px 0 0;padding:32px 40px 24px;border-bottom:1px solid rgba(255,255,255,0.08);">
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td>
                    <div style="display:inline-block;background:linear-gradient(135deg,#8B79FF,#6b5ce7);border-radius:12px;padding:8px 14px;">
                      <span style="color:#fff;font-size:15px;font-weight:700;letter-spacing:-0.3px;">Vector</span>
                    </div>
                  </td>
                  <td align="right">
                    <span style="color:rgba(255,255,255,0.3);font-size:12px;">Учебный помощник</span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <!-- BODY -->
          <tr>
            <td style="background:#18171c;padding:36px 40px;border-left:1px solid rgba(255,255,255,0.05);border-right:1px solid rgba(255,255,255,0.05);">
              {content}
            </td>
          </tr>
          <!-- FOOTER -->
          <tr>
            <td style="background:#13121a;border-radius:0 0 20px 20px;padding:20px 40px;border:1px solid rgba(255,255,255,0.05);border-top:none;">
              <p style="margin:0;color:rgba(255,255,255,0.25);font-size:12px;line-height:1.5;">
                Это автоматическое письмо от Vector. Не отвечайте на него.<br>
                © {datetime.now().year} Vector — учебный планировщик
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
      <h1 style="margin:0 0 8px;color:#fff;font-size:24px;font-weight:700;letter-spacing:-0.5px;">Подтверждение email</h1>
      <p style="margin:0 0 28px;color:rgba(255,255,255,0.5);font-size:15px;line-height:1.6;">
        Введите этот код на странице подтверждения. Он действителен <strong style="color:rgba(255,255,255,0.8);">15 минут</strong>.
      </p>
      <div style="background:linear-gradient(135deg,rgba(139,121,255,0.15),rgba(107,92,231,0.1));border:1px solid rgba(139,121,255,0.3);border-radius:16px;padding:28px;text-align:center;margin-bottom:28px;">
        <div style="letter-spacing:12px;font-size:40px;font-weight:800;color:#8B79FF;font-variant-numeric:tabular-nums;">{code}</div>
      </div>
      <p style="margin:0;color:rgba(255,255,255,0.35);font-size:13px;line-height:1.6;">
        Если вы не регистрировались в Vector — просто проигнорируйте это письмо.
      </p>
    """
    return _base_template("Подтверждение email — Vector", content)


def _password_reset_html(reset_link: str) -> str:
    content = f"""
      <h1 style="margin:0 0 8px;color:#fff;font-size:24px;font-weight:700;letter-spacing:-0.5px;">Сброс пароля</h1>
      <p style="margin:0 0 28px;color:rgba(255,255,255,0.5);font-size:15px;line-height:1.6;">
        Вы запросили сброс пароля. Ссылка действительна <strong style="color:rgba(255,255,255,0.8);">1 час</strong>.
      </p>
      <div style="text-align:center;margin-bottom:28px;">
        <a href="{reset_link}" style="display:inline-block;background:linear-gradient(135deg,#8B79FF,#6b5ce7);color:#fff;font-size:15px;font-weight:600;text-decoration:none;padding:14px 32px;border-radius:12px;letter-spacing:-0.2px;">
          Сбросить пароль
        </a>
      </div>
      <p style="margin:0 0 8px;color:rgba(255,255,255,0.3);font-size:12px;">Или скопируйте ссылку в браузер:</p>
      <p style="margin:0;background:rgba(255,255,255,0.05);border-radius:8px;padding:10px 14px;font-size:12px;color:rgba(255,255,255,0.4);word-break:break-all;">{reset_link}</p>
      <p style="margin:20px 0 0;color:rgba(255,255,255,0.35);font-size:13px;">
        Если вы не запрашивали сброс — ничего не делайте, пароль останется прежним.
      </p>
    """
    return _base_template("Сброс пароля — Vector", content)


def _overdue_task_html(task_title: str, deadline_str: str) -> str:
    content = f"""
      <h1 style="margin:0 0 8px;color:#fff;font-size:24px;font-weight:700;letter-spacing:-0.5px;">Просроченная задача</h1>
      <p style="margin:0 0 24px;color:rgba(255,255,255,0.5);font-size:15px;line-height:1.6;">
        Время дедлайна вышло. Не затягивай — сдай как можно скорее.
      </p>
      <div style="background:rgba(255,60,60,0.08);border:1px solid rgba(255,60,60,0.25);border-radius:14px;padding:20px 24px;margin-bottom:24px;">
        <div style="display:flex;align-items:flex-start;gap:12px;">
          <div style="min-width:8px;height:8px;border-radius:50%;background:#ff4444;margin-top:6px;flex-shrink:0;"></div>
          <div>
            <div style="color:#fff;font-size:16px;font-weight:600;margin-bottom:4px;">{task_title}</div>
            <div style="color:rgba(255,80,80,0.8);font-size:13px;">Дедлайн: {deadline_str}</div>
          </div>
        </div>
      </div>
      <div style="text-align:center;">
        <a href="{APP_URL}/student/tasks" style="display:inline-block;background:rgba(255,255,255,0.08);color:rgba(255,255,255,0.7);font-size:14px;font-weight:500;text-decoration:none;padding:12px 28px;border-radius:10px;border:1px solid rgba(255,255,255,0.1);">
          Открыть задачи
        </a>
      </div>
    """
    return _base_template("Просроченная задача — Vector", content)


def _notification_html(title: str, body: str) -> str:
    content = f"""
      <h1 style="margin:0 0 8px;color:#fff;font-size:22px;font-weight:700;letter-spacing:-0.5px;">{title}</h1>
      <p style="margin:0 0 24px;color:rgba(255,255,255,0.6);font-size:15px;line-height:1.7;">{body}</p>
      <div style="text-align:center;">
        <a href="{APP_URL}/student/dashboard" style="display:inline-block;background:linear-gradient(135deg,#8B79FF,#6b5ce7);color:#fff;font-size:14px;font-weight:600;text-decoration:none;padding:12px 28px;border-radius:10px;">
          Открыть дашборд
        </a>
      </div>
    """
    return _base_template(f"{title} — Vector", content)


# ─── ОТПРАВКА ────────────────────────────────────────────────────────────────

def send_email(to: str, subject: str, body_html: str):
    if not SMTP_HOST:
        raise RuntimeError("SMTP is not configured")
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = to
        msg.attach(MIMEText(body_html, "html", "utf-8"))

        # Настройка SMTP как в test_mail.py
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            # TLS и Login только если они настроены (для 2525 обычно не нужны)
            if SMTP_USER and SMTP_PASSWORD:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
            
            server.send_message(msg)
            print(f"✅ Письмо успешно отправлено на {to}")
    except Exception as e:
        raise RuntimeError(f"SMTP error: {e}")


def send_verification_email(to: str, code: str):
    send_email(to, "Ваш код подтверждения — Vector", _email_verification_html(code))


def send_password_reset_email(to: str, reset_link: str):
    send_email(to, "Сброс пароля — Vector", _password_reset_html(reset_link))


# ─── СОЗДАНИЕ УВЕДОМЛЕНИЯ В БД ───────────────────────────────────────────────

def create_notification(
    db: Session,
    user_id: int,
    title: str,
    body: str,
    channel: NotificationChannel = NotificationChannel.browser,
    scheduled_at: datetime | None = None,
):
    n = Notification(
        user_id=user_id,
        title=title,
        body=body,
        channel=channel,
        scheduled_at=scheduled_at,
    )
    db.add(n)
    db.commit()
    return n


# ─── ПЛАНИРОВЩИК ─────────────────────────────────────────────────────────────

async def _job_send_pending_emails():
    """Каждую минуту отправляет накопившиеся email-уведомления из базы."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        pending = db.query(Notification).filter(
            Notification.channel == NotificationChannel.email,
            Notification.is_sent == False,
            (Notification.scheduled_at == None) | (Notification.scheduled_at <= now),
        ).all()

        for n in pending:
            user = db.query(User).filter(User.id == n.user_id).first()
            if user and user.notify_email:
                send_email(user.email, n.title, _notification_html(n.title, n.body))
            n.is_sent = True
            n.sent_at = now

        if pending:
            db.commit()
            print(f"[SCHEDULER] Отправлено {len(pending)} email(s)")

    except Exception as e:
        print(f"[SCHEDULER] Ошибка send_emails: {e}")
    finally:
        db.close()


async def _job_check_overdue():
    """Каждые 10 минут помечает просроченные задачи и создаёт уведомления."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        overdue = db.query(Task).filter(
            Task.deadline < now,
            Task.status == TaskStatus.pending,
        ).all()

        for task in overdue:
            task.status = TaskStatus.overdue
            deadline_str = task.deadline.strftime("%d.%m.%Y %H:%M")

            # Browser-уведомление
            create_notification(
                db=db,
                user_id=task.student_id,
                title="Просроченная задача",
                body=f'«{task.title}» просрочена. Дедлайн был {deadline_str}.',
                channel=NotificationChannel.browser,
            )

            # Email-уведомление с красивым шаблоном
            user = db.query(User).filter(User.id == task.student_id).first()
            if user and user.notify_email:
                send_email(
                    user.email,
                    f"Просрочена задача: {task.title} — Vector",
                    _overdue_task_html(task.title, deadline_str),
                )

        if overdue:
            db.commit()
            print(f"[SCHEDULER] Помечено просроченных: {len(overdue)}")

    except Exception as e:
        print(f"[SCHEDULER] Ошибка check_overdue: {e}")
    finally:
        db.close()


def start_scheduler():
    scheduler.add_job(_job_send_pending_emails, "interval", minutes=1,  id="send_emails")
    scheduler.add_job(_job_check_overdue,        "interval", minutes=10, id="check_overdue")
    scheduler.start()


# ─── API РОУТЫ ───────────────────────────────────────────────────────────────

@router.get("", response_model=list[NotificationResponse])
def list_notifications(
    unread_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Notification).filter(Notification.user_id == current_user.id)
    if unread_only:
        query = query.filter(Notification.is_read == False)
    return query.order_by(Notification.created_at.desc()).limit(100).all()


@router.post("/{notification_id}/read", status_code=204)
def mark_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    n = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == current_user.id,
    ).first()
    if not n:
        raise HTTPException(404, "Not found")
    n.is_read = True
    db.commit()


@router.post("/read-all", status_code=204)
def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.is_read == False,
    ).update({"is_read": True})
    db.commit()
