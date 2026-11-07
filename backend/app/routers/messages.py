"""Внутренняя почта школы: входящие, отправленные, прочтение."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Assignment, MailFile, Message, User, UserRole

router = APIRouter(prefix="/api/messages", tags=["messages"])

MAX_SUBJECT_LENGTH = 255
MAX_BODY_LENGTH = 20000
MAX_FILES = 10
MAX_FILE_BYTES = 20 * 1024 * 1024
# Сколько живёт загруженный, но так и не приложенный файл. Меньше суток
# нельзя: человек может заполнять письмо долго.
ORPHAN_TTL = timedelta(hours=24)
UPLOAD_DIR = Path(__file__).resolve().parents[2] / "data" / "uploads" / "mail"


class PersonOut(BaseModel):
    id: int
    name: str
    role: UserRole


class FileOut(BaseModel):
    token: str
    name: str
    size: int = 0


class MessageOut(BaseModel):
    id: int
    sender: PersonOut
    recipient: PersonOut
    subject: str
    body: str
    files: list[FileOut]
    is_read: bool
    created_at: datetime


class MailboxResponse(BaseModel):
    inbox: list[MessageOut]
    sent: list[MessageOut]
    unread: int


class MessageCreate(BaseModel):
    recipient_id: int
    subject: str = ""
    body: str = ""
    files: list[str] = []

    @field_validator("subject")
    @classmethod
    def _subject(cls, value: str) -> str:
        value = value.strip()
        if len(value) > MAX_SUBJECT_LENGTH:
            raise ValueError(
                f"Subject must be <= {MAX_SUBJECT_LENGTH} characters"
            )
        return value or "(без темы)"

    @field_validator("body")
    @classmethod
    def _body(cls, value: str) -> str:
        if len(value) > MAX_BODY_LENGTH:
            raise ValueError(
                f"Body must be <= {MAX_BODY_LENGTH} characters"
            )
        return value

    @field_validator("files")
    @classmethod
    def _files(cls, value: list[str]) -> list[str]:
        if len(value) > MAX_FILES:
            raise ValueError(f"At most {MAX_FILES} files are allowed")
        return [str(name)[:255] for name in value if str(name).strip()]


def _person(user: User) -> PersonOut:
    name = f"{user.last_name} {user.first_name}".strip()
    return PersonOut(
        id=user.id,
        name=name or user.login or f"Пользователь {user.id}",
        role=user.role,
    )


def _files(value: str | None) -> list[FileOut]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    result = []
    for item in parsed:
        if isinstance(item, dict) and item.get("token"):
            result.append(
                FileOut(
                    token=str(item["token"]),
                    name=str(item.get("name") or "файл"),
                    size=int(item.get("size") or 0),
                )
            )
        elif isinstance(item, str) and item:
            # Старые письма хранили только имена без загрузки.
            result.append(FileOut(token="", name=item, size=0))
    return result


def _message(message: Message) -> MessageOut:
    return MessageOut(
        id=message.id,
        sender=_person(message.sender),
        recipient=_person(message.recipient),
        subject=message.subject,
        body=message.body,
        files=_files(message.files),
        is_read=message.is_read,
        created_at=message.created_at,
    )


@router.get("/people", response_model=list[PersonOut])
def list_people(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Пользователи школы — для выбора получателя.

    Учётка без подтверждённой почты (``is_active=False, is_verified=False``)
    остаётся человеком школы: админский список учителей её тоже показывает.
    Заблокированные админом (``is_verified=True``) скрыты.
    """
    users = (
        db.query(User)
        .filter(
            or_(User.is_active.is_(True), User.is_verified.is_(False)),
            User.id != current_user.id,
        )
        .order_by(User.role, User.last_name, User.first_name)
        .all()
    )
    return [_person(user) for user in users]


@router.get("", response_model=MailboxResponse)
def mailbox(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    inbox_rows = (
        db.query(Message)
        .filter(
            Message.recipient_id == current_user.id,
            Message.deleted_by_recipient.is_(False),
        )
        .order_by(Message.created_at.desc())
        .limit(500)
        .all()
    )
    sent_rows = (
        db.query(Message)
        .filter(
            Message.sender_id == current_user.id,
            Message.deleted_by_sender.is_(False),
        )
        .order_by(Message.created_at.desc())
        .limit(500)
        .all()
    )
    unread = sum(1 for row in inbox_rows if not row.is_read)
    return MailboxResponse(
        inbox=[_message(row) for row in inbox_rows],
        sent=[_message(row) for row in sent_rows],
        unread=unread,
    )


@router.post("/attachments", response_model=FileOut, status_code=201)
def upload_attachment(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Загрузка файла до отправки письма. Возвращает токен для вложения."""
    raw = file.file.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(413, "File is too large")
    name = (file.filename or "файл").replace("\\", "/").split("/")[-1][:255]
    if not name:
        name = "файл"
    token = secrets.token_hex(24)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = UPLOAD_DIR / token
    target.write_bytes(raw)
    row = MailFile(
        token=token,
        name=name,
        path=str(target),
        size=len(raw),
        uploader_id=current_user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return FileOut(token=row.token, name=row.name, size=row.size)


def _tokens_from_json(raw: str | None) -> set[str]:
    """Токены вложений из колонки ``files`` (JSON-список объектов)."""
    if not raw:
        return set()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return set()
    if not isinstance(data, list):
        return set()
    return {
        item["token"]
        for item in data
        if isinstance(item, dict) and item.get("token")
    }


def _referenced_tokens(db: Session) -> set[str]:
    """Токены, которые уже приложены к письму или заданию."""
    tokens: set[str] = set()
    for (raw,) in db.query(Message.files).all():
        tokens |= _tokens_from_json(raw)
    for (raw,) in db.query(Assignment.files).all():
        tokens |= _tokens_from_json(raw)
    return tokens


@router.delete("/attachments/{token}", status_code=204)
def delete_attachment(
    token: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Отмена добавления: удаляет загруженный файл, пока он не приложен.

    Токен уже в письме или задании — 409: иначе получатель потеряет
    вложение, а строка письма останется битой.
    """
    row = db.query(MailFile).filter(MailFile.token == token).first()
    if not row or row.uploader_id != current_user.id:
        raise HTTPException(404, "File not found")
    if token in _referenced_tokens(db):
        raise HTTPException(409, "File is already attached")
    path = Path(row.path)
    db.delete(row)
    db.commit()
    path.unlink(missing_ok=True)


def cleanup_orphan_attachments(
    db: Session, older_than: timedelta = ORPHAN_TTL
) -> int:
    """Удаляет загруженные, но не приложенные файлы старше ``older_than``.

    Фронтенд удаляет чип сразу через DELETE; эта уборка ловит то, что
    осталось от закрытой вкладки и упавшей загрузки.
    """
    cutoff = datetime.now(timezone.utc) - older_than
    used = _referenced_tokens(db)
    removed = 0
    for row in db.query(MailFile).filter(MailFile.created_at < cutoff).all():
        if row.token in used:
            continue
        Path(row.path).unlink(missing_ok=True)
        db.delete(row)
        removed += 1
    db.commit()
    return removed


@router.get("/attachments/{token}")
def download_attachment(
    token: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Скачивание: автор или получатель письма с этим вложением."""
    row = db.query(MailFile).filter(MailFile.token == token).first()
    if not row:
        raise HTTPException(404, "File not found")
    allowed = row.uploader_id == current_user.id
    if not allowed and current_user.role == UserRole.admin:
        allowed = True
    if not allowed:
        messages = (
            db.query(Message)
            .filter(
                Message.recipient_id == current_user.id,
                Message.deleted_by_recipient.is_(False),
            )
            .all()
        )
        allowed = any(
            token in (message.files or "") for message in messages
        )
    if not allowed:
        # Файл задания: учитель класса или ученик классаAssignment
        from app.models import Assignment, ClassMembership
        import json

        for assignment in db.query(Assignment).all():
            try:
                data = json.loads(assignment.files or "[]")
            except Exception:
                continue
            tokens = {x.get("token") for x in data if isinstance(x, dict)}
            if token not in tokens:
                continue
            if assignment.teacher_id == current_user.id:
                allowed = True
                break
            if db.query(ClassMembership).filter(
                ClassMembership.class_id == assignment.class_id,
                ClassMembership.student_id == current_user.id,
            ).first():
                allowed = True
                break
    if not allowed:
        raise HTTPException(404, "File not found")
    path = Path(row.path)
    if not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=row.name)


@router.get("/attachments/{token}/render")
def render_attachment_page(
    token: str,
    page: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """PNG первой (или указанной) страницы PDF — для нашего полноэкранного просмотра.

    Проверка доступа та же, что у скачивания, поэтому студент/учитель видят
    только «свой» документ. Пропустил PDF → 415.
    """
    from fastapi.responses import Response as _Resp

    row = db.query(MailFile).filter(MailFile.token == token).first()
    if not row:
        raise HTTPException(404, "File not found")

    allowed = row.uploader_id == current_user.id
    if not allowed and current_user.role == UserRole.admin:
        allowed = True
    if not allowed:
        allowed = any(
            token in (message.files or "")
            for message in db.query(Message)
            .filter(
                Message.recipient_id == current_user.id,
                Message.deleted_by_recipient.is_(False),
            )
            .all()
        )
    if not allowed:
        from app.models import Assignment, ClassMembership
        import json

        for assignment in db.query(Assignment).all():
            try:
                data = json.loads(assignment.files or "[]")
            except Exception:
                continue
            tokens = {x.get("token") for x in data if isinstance(x, dict)}
            if token not in tokens:
                continue
            if assignment.teacher_id == current_user.id:
                allowed = True
                break
            if db.query(ClassMembership).filter(
                ClassMembership.class_id == assignment.class_id,
                ClassMembership.student_id == current_user.id,
            ).first():
                allowed = True
                break
    if not allowed:
        raise HTTPException(404, "File not found")

    path = Path(row.path)
    if not path.is_file():
        raise HTTPException(404, "File not found")

    try:
        import pymupdf
    except Exception:
        raise HTTPException(415, "PDF preview is not available")

    try:
        doc = pymupdf.open(str(path))
        if page < 0 or page >= len(doc):
            raise HTTPException(404, "Page not found")
        pix = doc.load_page(page).get_pixmap(dpi=200)
        data = pix.tobytes("png")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(415, "Not a valid PDF")

    return _Resp(content=data, media_type="image/png")


@router.post("", response_model=MessageOut, status_code=201)
def send_message(
    body: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    recipient = (
        db.query(User)
        .filter(
            User.id == body.recipient_id,
            or_(User.is_active.is_(True), User.is_verified.is_(False)),
        )
        .first()
    )
    if not recipient:
        raise HTTPException(404, "Recipient not found")
    if recipient.id == current_user.id:
        raise HTTPException(400, "Cannot send a message to yourself")
    resolved = []
    if body.files:
        rows = (
            db.query(MailFile)
            .filter(
                MailFile.token.in_(body.files),
                MailFile.uploader_id == current_user.id,
            )
            .all()
        )
        by_token = {row.token: row for row in rows}
        for token in body.files:
            row = by_token.get(token)
            if row:
                resolved.append(
                    {"token": row.token, "name": row.name, "size": row.size}
                )
    message = Message(
        sender_id=current_user.id,
        recipient_id=recipient.id,
        subject=body.subject,
        body=body.body,
        files=json.dumps(resolved, ensure_ascii=False) if resolved else None,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return _message(message)


@router.post("/{message_id}/read", response_model=MessageOut)
def mark_read(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    message = (
        db.query(Message)
        .filter(
            Message.id == message_id,
            or_(
                Message.recipient_id == current_user.id,
                Message.sender_id == current_user.id,
            ),
        )
        .first()
    )
    if not message:
        raise HTTPException(404, "Message not found")
    if message.recipient_id == current_user.id:
        message.is_read = True
        db.commit()
        db.refresh(message)
    return _message(message)


@router.delete("/{message_id}", status_code=204)
def delete_message(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    message = (
        db.query(Message)
        .filter(
            Message.id == message_id,
            or_(
                Message.recipient_id == current_user.id,
                Message.sender_id == current_user.id,
            ),
        )
        .first()
    )
    if not message:
        raise HTTPException(404, "Message not found")
    if message.recipient_id == current_user.id:
        message.deleted_by_recipient = True
        message.is_read = True
    if message.sender_id == current_user.id:
        message.deleted_by_sender = True
    db.commit()
