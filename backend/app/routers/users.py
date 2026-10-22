"""Регистрация, аутентификация, профиль и привязка аккаунтов."""
import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from random import randint
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models import (
    EmailChangeToken,
    EmailVerification,
    InviteCode,
    ParentLinkToken,
    ParentStudent,
    PasswordResetToken,
    TokenBlacklist,
    User,
    UserRole,
)

logger = logging.getLogger("vector.users")

router = APIRouter(prefix="/api/users", tags=["users"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/users/login")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------------------------------------------------------------------------
# Простой rate limiter (in-memory, для одного процесса uvicorn)
# ---------------------------------------------------------------------------
import time as _time

_RATE_LIMIT_WINDOW = 60  # секунд
_RATE_LIMIT_MAX_ATTEMPTS = 10
_rate_limit_store: dict[str, list[float]] = {}


def _is_rate_limited(key: str) -> bool:
    now = _time.time()
    window_start = now - _RATE_LIMIT_WINDOW
    attempts = _rate_limit_store.get(key, [])
    # Удаляем старые записи
    _rate_limit_store[key] = [t for t in attempts if t > window_start]
    return len(_rate_limit_store[key]) >= _RATE_LIMIT_MAX_ATTEMPTS


def _record_attempt(key: str) -> None:
    now = _time.time()
    _rate_limit_store.setdefault(key, []).append(now)


MIN_PASSWORD_LENGTH = 8
EMAIL_VERIFICATION_TTL = timedelta(minutes=15)
RESET_TOKEN_TTL = timedelta(hours=1)
PARENT_LINK_TTL = timedelta(hours=24)


def _hash_token(token: str) -> str:
    """Хеширует однократный токен для безопасного хранения в БД."""
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest()


def _find_token_by_hash(
    db: Session, model: Any, token: str, user_id: int | None = None
) -> Any | None:
    """Ищет токен по хешу (с fallback на открытый токен для обратной совместимости)."""
    token_hash = _hash_token(token)
    query = db.query(model).filter(model.token_hash == token_hash)
    if user_id is not None:
        query = query.filter(model.user_id == user_id)
    record = query.filter(model.is_used.is_(False)).first()
    if record:
        return record
    # Fallback: поиск по открытому токену (для баз, созданных до внедрения хеширования)
    query = db.query(model).filter(model.token == token)
    if user_id is not None:
        query = query.filter(model.user_id == user_id)
    return query.filter(model.is_used.is_(False)).first()

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


# ---------------------------------------------------------------------------
# Схемы
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    role: UserRole
    invite_code: str | None = None

    @field_validator("password")
    @classmethod
    def _password_length(cls, value: str) -> str:
        if len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
            )
        return value

    @field_validator("first_name", "last_name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name must not be blank")
        return value


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: UserRole
    first_name: str
    last_name: str


class VerifyEmailRequest(BaseModel):
    code: str


class ChildSummary(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str
    grade: str | None

    class Config:
        from_attributes = True


class ProfileResponse(BaseModel):
    id: int
    email: str
    first_name: str
    last_name: str
    role: UserRole
    grade: str | None
    teacher_subject: str | None
    avatar_url: str | None
    yandex_id: str | None
    notify_email: bool
    notify_browser: bool
    notify_telegram: bool
    notify_digest_time: str

    class Config:
        from_attributes = True


class ProfileUpdateRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    teacher_subject: str | None = None
    avatar_url: str | None = None
    notify_email: bool | None = None
    notify_browser: bool | None = None
    notify_digest_time: str | None = None

    @field_validator("notify_digest_time")
    @classmethod
    def _valid_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _HHMM_RE.match(value):
            raise ValueError("notify_digest_time must be in HH:MM format")
        return value

    @field_validator("first_name", "last_name")
    @classmethod
    def _name_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Name must not be blank")
        return value


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _password_length(cls, value: str) -> str:
        if len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
            )
        return value


class EmailChangeRequest(BaseModel):
    email: EmailStr


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _password_length(cls, value: str) -> str:
        if len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
            )
        return value


# ---------------------------------------------------------------------------
# Вспомогательные функции авторизации
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int, role: UserRole | None = None) -> str:
    """Выпускает JWT сclaims: sub, exp, jti, role.

    ``ACCESS_TOKEN_EXPIRE_MINUTES`` раньше читался через ``os.environ.get``
    и уходил в ``timedelta`` строкой, поэтому любой вход падал с
    ``TypeError`` (HTTP 500). Теперь значение парсится в int в
    ``app.config``.
    """
    import uuid

    expire = datetime.now(timezone.utc) + timedelta(
        minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "jti": str(uuid.uuid4()),
    }
    if role is not None:
        payload["role"] = role.value
    return jwt.encode(payload, config.SECRET_KEY, algorithm=config.ALGORITHM)


def revoke_token(jti: str, user_id: int, expires_at: datetime, db: Session) -> None:
    """Добавляет JWT в черный список (отзыв токена)."""
    db.add(
        TokenBlacklist(
            jti=jti,
            user_id=user_id,
            expires_at=expires_at,
        )
    )
    db.commit()


def _normalize_profile_defaults(user: User, db: Session | None = None) -> User:
    """Заполняет настройки уведомлений у строк, созданных до их появления."""
    changed = False
    if user.notify_email is None:
        user.notify_email = True
        changed = True
    if user.notify_browser is None:
        user.notify_browser = True
        changed = True
    if user.notify_telegram is None:
        user.notify_telegram = False
        changed = True
    if not user.notify_digest_time:
        user.notify_digest_time = "08:00"
        changed = True
    if changed and db is not None:
        db.commit()
        db.refresh(user)
    return user


def _delete_account_rows(user_id: int, db: Session) -> None:
    """Удаляет все строки пользователя, чтобы повторная регистрация не
    нарушила внешние ключи.

    Каскады ORM покрывают почти всё, но у таблиц токенов нет связи с
    ``User``, поэтому их чистим вручную.
    """
    for model in (
        EmailVerification,
        PasswordResetToken,
        EmailChangeToken,
        ParentLinkToken,
    ):
        db.query(model).filter(model.user_id == user_id).delete(
            synchronize_session=False
        )
    db.query(InviteCode).filter(InviteCode.used_by_id == user_id).update(
        {"is_used": False, "used_by_id": None, "used_at": None},
        synchronize_session=False,
    )


def _create_verification(user_id: int, db: Session) -> str:
    code = str(randint(100000, 999999))
    db.add(
        EmailVerification(
            user_id=user_id,
            code=code,
            expires_at=datetime.now(timezone.utc) + EMAIL_VERIFICATION_TTL,
        )
    )
    db.commit()
    return code


def _build_email_change_html(confirm_link: str, new_email: str) -> str:
    from app.routers.notifications import _base_template, _escape

    content = f"""
      <h1 style="margin:0 0 8px;color:#fff;font-size:24px;font-weight:700;">Смена email</h1>
      <p style="margin:0 0 28px;color:rgba(255,255,255,0.5);font-size:15px;line-height:1.6;">
        Подтвердите новый адрес <strong style="color:rgba(255,255,255,0.86);">{_escape(new_email)}</strong>.
        Ссылка действительна <strong style="color:rgba(255,255,255,0.8);">1 час</strong>.
      </p>
      <div style="text-align:center;margin-bottom:28px;">
        <a href="{_escape(confirm_link)}" style="display:inline-block;background:linear-gradient(135deg,#8B79FF,#6b5ce7);color:#fff;font-size:15px;font-weight:600;text-decoration:none;padding:14px 32px;border-radius:12px;">
          Подтвердить смену email
        </a>
      </div>
      <p style="margin:0 0 8px;color:rgba(255,255,255,0.3);font-size:12px;">Или скопируйте ссылку в браузер:</p>
      <p style="margin:0;background:rgba(255,255,255,0.05);border-radius:8px;padding:10px 14px;font-size:12px;color:rgba(255,255,255,0.4);word-break:break-all;">{_escape(confirm_link)}</p>
    """
    return _base_template("Смена email — Vector", content)


# ---------------------------------------------------------------------------
# Роутеры
# ---------------------------------------------------------------------------


@router.post("/register", status_code=201)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    key = f"register:{body.email.lower()}"
    if _is_rate_limited(key):
        raise HTTPException(429, "Too many registration attempts. Try later.")
    _record_attempt(key)
    email = body.email.lower()

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        if existing.is_active:
            raise HTTPException(400, "Email already registered")
        # Неподтверждённый остаток можно заменить, но только после удаления
        # зависимых строк - иначе удаление упадёт по внешнему ключу.
        _delete_account_rows(existing.id, db)
        db.delete(existing)
        db.commit()

    invite = None
    if body.role == UserRole.teacher:
        if not body.invite_code:
            raise HTTPException(400, "Invite code required for teacher registration")
        invite = (
            db.query(InviteCode)
            .filter(
                InviteCode.code == body.invite_code.upper(),
                InviteCode.is_used.is_(False),
            )
            .first()
        )
        if not invite:
            raise HTTPException(400, "Invalid or already used invite code")

    user = User(
        email=email,
        password_hash=hash_password(body.password),
        first_name=body.first_name,
        last_name=body.last_name,
        role=body.role,
        is_active=False,
        is_verified=False,
        notify_email=True,
        notify_browser=True,
        notify_telegram=False,
        notify_digest_time="08:00",
        teacher_invite_code=body.invite_code.upper() if invite else None,
    )
    db.add(user)
    db.commit()

    # Письмо отправляем после коммита строки пользователя, чтобы сбой
    # SMTP не оставил полузаписанный аккаунт.
    code = _create_verification(user.id, db)
    from app.routers.notifications import send_verification_email

    try:
        send_verification_email(user.email, code)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            503, f"Не удалось отправить письмо с кодом подтверждения: {exc}"
        )

    return {
        "message": "Registration successful. Check your email for verification code."
    }


@router.post("/verify-email")
def verify_email(body: VerifyEmailRequest, db: Session = Depends(get_db)):
    record = (
        db.query(EmailVerification)
        .filter(
            EmailVerification.code == body.code,
            EmailVerification.is_used.is_(False),
        )
        .order_by(EmailVerification.created_at.desc())
        .first()
    )
    if not record:
        raise HTTPException(400, "Invalid code")

    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(400, "Code expired")

    user = db.query(User).filter(User.id == record.user_id).first()
    if not user:
        raise HTTPException(400, "Invalid code")

    record.is_used = True
    user.is_active = True
    user.is_verified = True

    # Инвайт учителя забираем только сейчас, когда аккаунт уже рабочий.
    if user.role == UserRole.teacher and user.teacher_invite_code:
        invite = (
            db.query(InviteCode)
            .filter(
                InviteCode.code == user.teacher_invite_code,
                InviteCode.is_used.is_(False),
            )
            .first()
        )
        if not invite:
            raise HTTPException(400, "Teacher invite code is no longer available")
        invite.is_used = True
        invite.used_by_id = user.id
        invite.used_at = datetime.now(timezone.utc)
        user.teacher_invite_code = None

    db.commit()
    return {"message": "Email verified. You can now log in."}


@router.post("/resend-verification")
def resend_verification(email: EmailStr, db: Session = Depends(get_db)):
    user = (
        db.query(User)
        .filter(User.email == email.lower(), User.is_verified.is_(False))
        .first()
    )
    if not user:
        raise HTTPException(400, "User not found or already verified")

    # Гасим прежние коды, чтобы работал только самый свежий.
    db.query(EmailVerification).filter(
        EmailVerification.user_id == user.id,
        EmailVerification.is_used.is_(False),
    ).delete(synchronize_session=False)
    db.commit()

    code = _create_verification(user.id, db)
    from app.routers.notifications import send_verification_email

    try:
        send_verification_email(user.email, code)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            503, f"Не удалось отправить письмо с кодом подтверждения: {exc}"
        )
    return {"message": "Code sent"}


@router.post("/login", response_model=LoginResponse)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    key = f"login:{form.username.lower()}"
    if _is_rate_limited(key):
        raise HTTPException(429, "Too many login attempts. Try later.")
    _record_attempt(key)
    user = db.query(User).filter(User.email == form.username.lower()).first()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Email not verified")

    _normalize_profile_defaults(user, db)
    return LoginResponse(
        access_token=create_access_token(user.id, role=user.role),
        role=user.role,
        first_name=user.first_name,
        last_name=user.last_name,
    )


@router.get("/me", response_model=ProfileResponse)
def get_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _normalize_profile_defaults(current_user, db)


@router.patch("/me", response_model=ProfileResponse)
def update_profile(
    body: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Поля профиля, которые пользователь меняет сам.

    ``grade`` намеренно отсутствует: им управляет членство в классе и админ.
    Раньше его принимали здесь, и любой ученик мог переписать свой класс.
    """
    updates = body.model_dump(exclude_none=True)
    if current_user.role != UserRole.teacher:
        updates.pop("teacher_subject", None)

    for field, value in updates.items():
        setattr(current_user, field, value)

    # Держим денормализованный grade в синхроне с членством в классе.
    if current_user.role == UserRole.student:
        current_user.grade = _current_class_name(current_user.id, db)

    db.commit()
    db.refresh(current_user)
    return _normalize_profile_defaults(current_user)


def _current_class_name(student_id: int, db: Session) -> str | None:
    from app.models import Class, ClassMembership

    membership = (
        db.query(ClassMembership)
        .filter(ClassMembership.student_id == student_id)
        .order_by(ClassMembership.joined_at.desc())
        .first()
    )
    if not membership:
        return None
    class_ = db.query(Class).filter(Class.id == membership.class_id).first()
    return class_.name if class_ else None


@router.post("/me/change-password")
def change_password(
    body: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(400, "Wrong current password")

    current_user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"message": "Password changed"}


@router.post("/me/change-email")
def request_email_change(
    body: EmailChangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    new_email = body.email.lower()
    if new_email == current_user.email.lower():
        raise HTTPException(400, "Новый email совпадает с текущим")

    existing = (
        db.query(User)
        .filter(User.email == new_email, User.id != current_user.id)
        .first()
    )
    if existing:
        raise HTTPException(400, "Этот email уже занят")

    db.query(EmailChangeToken).filter(
        EmailChangeToken.user_id == current_user.id,
        EmailChangeToken.is_used.is_(False),
    ).update({"is_used": True}, synchronize_session=False)

    token = secrets.token_urlsafe(32)
    db.add(
        EmailChangeToken(
            user_id=current_user.id,
            new_email=new_email,
            token=token,
            token_hash=_hash_token(token),
            expires_at=datetime.now(timezone.utc) + RESET_TOKEN_TTL,
        )
    )
    db.commit()

    from app.routers.notifications import send_email

    confirm_link = f"{config.APP_URL}/settings/change-email/confirm?token={token}"
    send_email(
        new_email,
        "Подтверждение смены email — Vector",
        _build_email_change_html(confirm_link, new_email),
    )
    return {"message": "Письмо с подтверждением отправлено на новую почту."}


@router.post("/me/change-email/confirm")
def confirm_email_change(token: str, db: Session = Depends(get_db)):
    record = _find_token_by_hash(
        db, EmailChangeToken, token, user_id=None
    )
    if not record or record.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(400, "Недействительная или просроченная ссылка")
    if not record:
        raise HTTPException(400, "Недействительная или просроченная ссылка")

    user = db.query(User).filter(User.id == record.user_id).first()
    if not user:
        raise HTTPException(404, "Пользователь не найден")

    existing = (
        db.query(User)
        .filter(User.email == record.new_email, User.id != user.id)
        .first()
    )
    if existing:
        raise HTTPException(400, "Этот email уже занят")

    user.email = record.new_email
    user.is_verified = True
    record.is_used = True
    db.commit()
    return {"message": "Email успешно изменён", "email": user.email}


@router.post("/me/parent-link-token")
def generate_parent_link_token(
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    """Ученик показывает шестизначный код, родитель вводит его в своём
    аккаунте."""
    existing = (
        db.query(ParentLinkToken)
        .filter(
            ParentLinkToken.student_id == current_user.id,
            ParentLinkToken.is_used.is_(False),
            ParentLinkToken.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
    if existing:
        return {"code": existing.code, "expires_at": existing.expires_at}

    code = str(randint(100000, 999999))
    token = ParentLinkToken(
        student_id=current_user.id,
        code=code,
        expires_at=datetime.now(timezone.utc) + PARENT_LINK_TTL,
    )
    db.add(token)
    db.commit()
    return {"code": code, "expires_at": token.expires_at}


@router.post("/me/link-parent")
def link_parent(
    code: str,
    current_user: User = Depends(require_role(UserRole.parent)),
    db: Session = Depends(get_db),
):
    """Родитель отправляет код ученика, чтобы связать аккаунты."""
    token = (
        db.query(ParentLinkToken)
        .filter(
            ParentLinkToken.code == code,
            ParentLinkToken.is_used.is_(False),
            ParentLinkToken.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
    if not token:
        raise HTTPException(400, "Invalid or expired code")

    already = (
        db.query(ParentStudent)
        .filter(
            ParentStudent.parent_id == current_user.id,
            ParentStudent.student_id == token.student_id,
        )
        .first()
    )
    if already:
        raise HTTPException(400, "Already linked")

    student = db.query(User).filter(User.id == token.student_id).first()
    if not student or student.role != UserRole.student:
        raise HTTPException(400, "Invalid or expired code")

    db.add(ParentStudent(parent_id=current_user.id, student_id=student.id))
    token.is_used = True
    db.commit()
    return {
        "message": "Linked successfully",
        "student_name": f"{student.first_name} {student.last_name}",
    }


@router.get("/me/children", response_model=list[ChildSummary])
def get_children(
    current_user: User = Depends(require_role(UserRole.parent)),
    db: Session = Depends(get_db),
):
    children = (
        db.query(User)
        .join(ParentStudent, ParentStudent.student_id == User.id)
        .filter(ParentStudent.parent_id == current_user.id)
        .order_by(User.last_name.asc())
        .all()
    )
    return [
        ChildSummary(
            id=child.id,
            first_name=child.first_name,
            last_name=child.last_name,
            email=child.email,
            grade=child.grade,
        )
        for child in children
    ]


# ---------------------------------------------------------------------------
# MAX Bridge auth — привязка аккаунта MAX к внутреннему профилю
# ---------------------------------------------------------------------------


class MaxLinkRequest(BaseModel):
    max_user_id_hashed: str


class MaxLinkResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: UserRole
    first_name: str
    last_name: str


def _verify_bot_signature(hashed_user_id: str, signature: str | None) -> None:
    """Проверяет, что /auth/max-link вызвал именно MAX-бот.

    Хеш MAX user_id — это фактически credential: по нему выдаётся JWT.
    Поэтому одного хеша недостаточно, запрос должен быть подписан
    HMAC-SHA256 на общем MAX_BOT_TOKEN.
    """

    secret = config.MAX_BOT_TOKEN
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MAX bot integration is not configured",
        )
    expected = hmac.new(
        secret.encode(),
        hashed_user_id.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid bot signature",
        )


@router.post("/auth/max-link", response_model=MaxLinkResponse)
def max_link(
    body: MaxLinkRequest,
    x_bot_signature: str | None = Header(default=None, alias="X-Bot-Signature"),
    db: Session = Depends(get_db),
):
    """
    Привязывает MAX user_id к существующему аккаунту и выдаёт JWT.

    Используется ботом MAX: пользователь уже аутентифицирован в MAX,
    бот передаёт хеш max_user_id (SHA-256 с солью), бэкенд ищет
    пользователя с таким max_user_id в таблице users.

    В упрощённом варианте (первый вход через бота) бот передаёт хеш,
    бэкенд ищет пользователя по max_user_id (хранит хеш).
    """
    _verify_bot_signature(body.max_user_id_hashed, x_bot_signature)

    user = (
        db.query(User)
        .filter(User.max_user_id == body.max_user_id_hashed)
        .first()
    )
    if not user:
        raise HTTPException(
            status_code=404,
            detail="MAX user not linked. Please log in via web first.",
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is not active")

    _normalize_profile_defaults(user, db)
    return MaxLinkResponse(
        access_token=create_access_token(user.id, role=user.role),
        role=user.role,
        first_name=user.first_name,
        last_name=user.last_name,
    )


@router.post("/auth/link-max", status_code=204)
def link_max_user(
    max_user_id_hashed: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Привязать текущий аккаунт к MAX user_id (хеш, вызов после логина через веб)."""
    existing = (
        db.query(User).filter(User.max_user_id == max_user_id_hashed).first()
    )
    if existing and existing.id != current_user.id:
        raise HTTPException(400, "MAX user already linked to another account")
    current_user.max_user_id = max_user_id_hashed
    db.commit()


# ---------------------------------------------------------------------------
# Refresh — обмен старого JWT на новый (и отзыв старого)
# ---------------------------------------------------------------------------


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: UserRole
    first_name: str
    last_name: str


@router.post("/auth/refresh", response_model=RefreshResponse)
def refresh(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
) -> RefreshResponse:
    """Обменивает текущий JWT на новый. Старый добавляется в черный список."""
    from jose import jwt as _jwt

    try:
        payload = _jwt.decode(
            token, config.SECRET_KEY, algorithms=[config.ALGORITHM]
        )
        jti = payload.get("jti")
        exp = payload.get("exp")
        if jti and exp:
            revoke_token(
                jti=jti,
                user_id=current_user.id,
                expires_at=datetime.fromtimestamp(exp, tz=timezone.utc),
                db=db,
            )
    except JWTError:
        pass

    return RefreshResponse(
        access_token=create_access_token(current_user.id, role=current_user.role),
        role=current_user.role,
        first_name=current_user.first_name,
        last_name=current_user.last_name,
    )


# ---------------------------------------------------------------------------
# Logout — отзыв текущего JWT
# ---------------------------------------------------------------------------


class LogoutResponse(BaseModel):
    message: str


@router.post("/logout", response_model=LogoutResponse)
def logout(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
) -> LogoutResponse:
    """Добавляет текущий JWT в черный список."""
    from jose import jwt as _jwt

    try:
        payload = _jwt.decode(
            token, config.SECRET_KEY, algorithms=[config.ALGORITHM]
        )
        jti = payload.get("jti")
        exp = payload.get("exp")
        if jti and exp:
            revoke_token(
                jti=jti,
                user_id=current_user.id,
                expires_at=datetime.fromtimestamp(exp, tz=timezone.utc),
                db=db,
            )
    except JWTError:
        pass  # токен уже недействителен — считаем, что отозван
    return LogoutResponse(message="Logged out")


# ---------------------------------------------------------------------------
# Сброс пароля
# ---------------------------------------------------------------------------

_RESET_MESSAGE = "Если такой email зарегистрирован, письмо отправлено."


@router.post("/password-reset/request")
def request_password_reset(
    body: PasswordResetRequest,
    db: Session = Depends(get_db),
):
    """Отвечает всегда одинаково, чтобы нельзя было перебором узнать,
    какие email зарегистрированы."""
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if not user:
        return {"message": _RESET_MESSAGE}

    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.is_used.is_(False),
    ).update({"is_used": True}, synchronize_session=False)

    token = secrets.token_urlsafe(32)
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token=token,
            token_hash=_hash_token(token),
            expires_at=datetime.now(timezone.utc) + RESET_TOKEN_TTL,
        )
    )
    db.commit()

    from app.routers.notifications import send_password_reset_email

    reset_link = f"{config.APP_URL}/reset-password-new?token={token}"
    try:
        send_password_reset_email(user.email, reset_link)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to send password reset email: %s", exc)

    return {"message": _RESET_MESSAGE}


@router.post("/password-reset/confirm")
def confirm_password_reset(
    body: PasswordResetConfirm,
    db: Session = Depends(get_db),
):
    record = _find_token_by_hash(
        db, PasswordResetToken, body.token, user_id=None
    )
    if not record or record.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(400, "Invalid or expired reset link")

    user = db.query(User).filter(User.id == record.user_id).first()
    if not user:
        raise HTTPException(400, "Invalid or expired reset link")

    user.password_hash = hash_password(body.new_password)
    record.is_used = True
    db.commit()
    return {"message": "Password changed successfully"}