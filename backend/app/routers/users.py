"""Регистрация, аутентификация, профиль и привязка аккаунтов."""
import hashlib
import hmac
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.enrollment import (
    add_missing_assignment_tasks,
    clean_subject,
    refresh_student_grade,
    set_teacher_subjects,
)
from app.models import (
    Class,
    ClassMembership,
    EmailChangeToken,
    EmailVerification,
    InviteCode,
    MaxLoginCode,
    ParentLinkToken,
    ParentStudent,
    PasswordResetToken,
    User,
    UserRole,
)

logger = logging.getLogger("timing.users")

router = APIRouter(prefix="/api/users", tags=["users"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Ограничение попыток входа и регистрации (in-memory, один процесс uvicorn)
_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX_ATTEMPTS = 10
_RATE_LIMIT_MAX_KEYS = 10_000
# Самый длинный интервал, для которого вызывается лимитер. Записи старше
# него не нужны и удаляются, чтобы словарь не рос бесконечно.
_RATE_LIMIT_MAX_WINDOW = 3600
_rate_limit_store: dict[str, list[float]] = {}


def _prune_rate_limit(now: float) -> None:
    """Удаляет устаревшие записи и не даёт словарю расти бесконечно."""
    window_start = now - _RATE_LIMIT_MAX_WINDOW
    for key in list(_rate_limit_store):
        fresh = [
            moment
            for moment in _rate_limit_store[key]
            if moment > window_start
        ]
        if fresh:
            _rate_limit_store[key] = fresh
        else:
            del _rate_limit_store[key]

    if len(_rate_limit_store) > _RATE_LIMIT_MAX_KEYS:
        oldest = sorted(
            _rate_limit_store,
            key=lambda name: _rate_limit_store[name][0],
        )
        for key in oldest[: len(_rate_limit_store) - _RATE_LIMIT_MAX_KEYS]:
            del _rate_limit_store[key]


def _is_rate_limited(
    key: str,
    limit: int = _RATE_LIMIT_MAX_ATTEMPTS,
    window: int = _RATE_LIMIT_WINDOW,
) -> bool:
    now = time.time()
    _prune_rate_limit(now)
    window_start = now - window
    attempts = [
        moment
        for moment in _rate_limit_store.get(key, [])
        if moment > window_start
    ]
    return len(attempts) >= limit


def _record_attempt(key: str) -> None:
    _rate_limit_store.setdefault(key, []).append(time.time())


def _reset_attempts(key: str) -> None:
    _rate_limit_store.pop(key, None)


MIN_PASSWORD_LENGTH = 8
MAX_NAME_LENGTH = 100
MAX_AVATAR_LENGTH = 2_000_000
EMAIL_VERIFICATION_TTL = timedelta(minutes=15)
RESET_TOKEN_TTL = timedelta(hours=1)
PARENT_LINK_TTL = timedelta(hours=1)
PARENT_LINK_ATTEMPTS = 5
PARENT_LINK_WINDOW = 600
PARENT_CODE_DIGITS = 8

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
# Хеш MAX user_id — HMAC-SHA256 в hex, ровно 64 символа.
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


# Схемы


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
        if len(value) > MAX_NAME_LENGTH:
            raise ValueError(f"Name must be <= {MAX_NAME_LENGTH} characters")
        return value

    @field_validator("role")
    @classmethod
    def _no_self_service_admin(cls, value: UserRole) -> UserRole:
        """Администратор создаётся только при развёртывании."""
        if value == UserRole.admin:
            raise ValueError("Administrator accounts cannot be registered")
        return value


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    id: int
    login: str
    role: UserRole
    first_name: str
    last_name: str


class VerifyEmailRequest(BaseModel):
    code: str


class ChildSummary(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str | None
    grade: str | None

    class Config:
        from_attributes = True


class ProfileResponse(BaseModel):
    id: int
    login: str
    email: str | None
    first_name: str
    last_name: str
    role: UserRole
    grade: str | None
    teacher_subjects: list[str]
    avatar_url: str | None
    yandex_id: str | None
    notify_email: bool
    notify_browser: bool
    notify_max: bool
    notify_digest_time: str

    class Config:
        from_attributes = True


class ProfileUpdateRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    teacher_subjects: list[str] | None = None
    avatar_url: str | None = None
    notify_email: bool | None = None
    notify_browser: bool | None = None
    notify_max: bool | None = None
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
        if len(value) > MAX_NAME_LENGTH:
            raise ValueError(f"Name must be <= {MAX_NAME_LENGTH} characters")
        return value

    @field_validator("teacher_subjects")
    @classmethod
    def _subject_length(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned: list[str] = []
        for raw in value:
            subject = clean_subject(raw)
            if subject is None:
                continue
            if subject.lower() not in {item.lower() for item in cleaned}:
                cleaned.append(subject)
        return cleaned

    @field_validator("avatar_url")
    @classmethod
    def _avatar_length(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) > MAX_AVATAR_LENGTH:
            raise ValueError("Avatar is too large")
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


# Вспомогательные функции авторизации


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str | None) -> bool:
    """Проверка пароля. ``None`` — у аккаунта из MAX пароля нет."""

    if not hashed:
        return False
    try:
        return pwd_context.verify(plain, hashed)
    except ValueError:
        # Битый хеш в БД не должен превращаться в 500 на входе.
        return False


def create_access_token(user_id: int) -> str:
    """Выпускает JWT с полями sub и exp.

    ``ACCESS_TOKEN_EXPIRE_MINUTES`` раньше читался через ``os.environ.get``
    и уходил в ``timedelta`` строкой, поэтому любой вход падал с
    ``TypeError`` (HTTP 500). Теперь значение парсится в int в
    ``app.config``.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, config.SECRET_KEY, algorithm=config.ALGORITHM)


def _normalize_profile_defaults(user: User, db: Session | None = None) -> User:
    """Заполняет настройки уведомлений у строк, созданных до их появления."""
    changed = False
    if user.notify_email is None:
        user.notify_email = True
        changed = True
    if user.notify_browser is None:
        user.notify_browser = True
        changed = True
    if user.notify_max is None:
        user.notify_max = True
        changed = True
    if not user.notify_digest_time:
        user.notify_digest_time = "08:00"
        changed = True
    if changed and db is not None:
        db.commit()
        db.refresh(user)
    return user


def _random_code(digits: int) -> str:
    """Криптостойкий числовой код заданной длины.

    ``random.randint`` предсказуем: зная несколько выданных кодов, можно
    восстановить состояние генератора и подобрать следующий.
    """
    return "".join(str(secrets.randbelow(10)) for _ in range(digits))


def _active_verification_codes(db: Session) -> set[str]:
    now = datetime.now(timezone.utc)
    rows = (
        db.query(EmailVerification.code)
        .filter(
            EmailVerification.is_used.is_(False),
            EmailVerification.expires_at > now,
        )
        .all()
    )
    return {row[0] for row in rows}


def _create_verification(user_id: int, db: Session) -> str:
    """Выпускает код, который однозначно указывает на одного пользователя.

    ``/verify-email`` ищет запись только по коду. Если два активных кода
    совпадут, подтверждение активирует чужой аккаунт, поэтому код
    проверяется на уникальность среди действующих.
    """
    active = _active_verification_codes(db)
    code = ""
    for _ in range(20):
        candidate = _random_code(6)
        if candidate not in active:
            code = candidate
            break
    if not code:
        raise HTTPException(
            503, "Не удалось выпустить код подтверждения. Попробуйте позже."
        )

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
    return _base_template("Смена email — Timing", content)


# Роутеры


@router.post("/register", status_code=201)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    key = f"register:{body.email.lower()}"
    if _is_rate_limited(key):
        raise HTTPException(429, "Too many registration attempts. Try later.")
    _record_attempt(key)
    email = body.email.lower()

    existing = (
        db.query(User)
        .filter((User.email == email) | (User.login == email))
        .first()
    )
    if existing:
        # Неподтверждённый аккаунт нельзя перезаписывать: иначе атакующий
        # подменял бы пароль чужой регистрации и забирал её после
        # подтверждения владельцем почты. Для потерянного кода есть
        # /resend-verification.
        raise HTTPException(400, "Email already registered")

    invite = None
    if body.role == UserRole.admin:
        raise HTTPException(400, "Administrator accounts cannot be registered")
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
        # Заглушка до механизма логинов: колонка NOT NULL, а формат логина
        # при обычной регистрации ещё не определён.
        login=email,
        password_hash=hash_password(body.password),
        first_name=body.first_name,
        last_name=body.last_name,
        role=body.role,
        is_active=False,
        is_verified=False,
        notify_email=True,
        notify_browser=True,
        notify_max=True,
        notify_digest_time="08:00",
        teacher_invite_code=body.invite_code.upper() if invite else None,
    )
    db.add(user)
    db.commit()
    _reset_attempts(key)

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


class InviteCodeInfo(BaseModel):
    """Ответ на проверку инвайт-кода: существует ли и для какой роли."""

    valid: bool
    role: UserRole | None = None
    class_name: str | None = None


@router.get("/invite-code", response_model=InviteCodeInfo)
def invite_code_info(code: str, db: Session = Depends(get_db)) -> InviteCodeInfo:
    """Проверяет инвайт-код и сообщает роль, для которой он выдан.

    Бот спрашивает это до регистрации: код класса открывает регистрацию
    ученика (и сразу зачисляет его в класс), одноразовый код из
    ``invite_codes`` — регистрацию учителя.
    """
    normalized = code.strip().upper()
    if not normalized or len(normalized) > 16:
        raise HTTPException(400, "Invite code must be 1-16 characters")

    key = f"invite-check:{normalized}"
    if _is_rate_limited(key):
        raise HTTPException(429, "Too many attempts. Try later.")
    _record_attempt(key)

    class_ = db.query(Class).filter(Class.invite_code == normalized).first()
    if class_ is not None:
        return InviteCodeInfo(
            valid=True, role=UserRole.student, class_name=class_.name
        )

    invite = (
        db.query(InviteCode)
        .filter(
            InviteCode.code == normalized,
            InviteCode.is_used.is_(False),
        )
        .first()
    )
    if invite is not None:
        return InviteCodeInfo(valid=True, role=UserRole.teacher)

    return InviteCodeInfo(valid=False)


@router.get("/login-available")
def login_available(login: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    """Свободен ли логин. Нужен боту на шаге придумывания логина.

    Сравнение регистронезависимое: ``func.lower`` не даёт завести два
    аккаунта, различающихся только регистром символов.
    """
    login = login.strip().lower()
    if not login or len(login) > 255:
        raise HTTPException(400, "Login must be 1-255 characters")

    key = f"login-check:{login}"
    if _is_rate_limited(key):
        raise HTTPException(429, "Too many attempts. Try later.")
    _record_attempt(key)

    exists = (
        db.query(User.id).filter(func.lower(User.login) == login).first()
        is not None
    )
    return {"available": not exists}


def _client_host(request: Request) -> str:
    """IP клиента для лимитов.

    nginx перезаписывает ``X-Real-IP`` значением ``$remote_addr``, поэтому
    заголовку можно доверять; напрямую backend наружу не опубликован.
    """
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


@router.post("/verify-email")
def verify_email(
    body: VerifyEmailRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Подтверждает email по одноразовому коду.

    Лимит попыток привязан к адресу клиента: без него шестизначный код
    можно перебрать за время жизни (15 минут).
    """
    key = f"verify-email:{_client_host(request)}"
    if _is_rate_limited(key):
        raise HTTPException(429, "Too many attempts. Try later.")

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
        _record_attempt(key)
        raise HTTPException(400, "Invalid code")

    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        _record_attempt(key)
        raise HTTPException(400, "Code expired")

    user = db.query(User).filter(User.id == record.user_id).first()
    if not user:
        _record_attempt(key)
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
    _reset_attempts(key)
    return {"message": "Email verified. You can now log in."}


@router.post("/resend-verification")
def resend_verification(email: EmailStr, db: Session = Depends(get_db)):
    """Повторно отправляет код подтверждения.

    Ответ не зависит от того, существует ли адрес: иначе эндпоинт
    превращается в проверку зарегистрированных email. Есть лимит на
    количество писем, чтобы не спамить чужой ящик.
    """
    response = {
        "message": (
            "Если такой адрес зарегистрирован и ещё не подтверждён, "
            "письмо с кодом отправлено."
        )
    }
    key = f"resend-verification:{email.lower()}"
    if _is_rate_limited(key, limit=3, window=3600):
        return response
    _record_attempt(key)

    user = (
        db.query(User)
        .filter(User.email == email.lower(), User.is_verified.is_(False))
        .first()
    )
    if not user:
        return response

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
        logger.warning("Failed to resend verification email: %s", exc)
    return response


@router.post("/login", response_model=LoginResponse)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    key = f"login:{form.username.lower()}"
    if _is_rate_limited(key):
        raise HTTPException(429, "Too many login attempts. Try later.")
    ident = form.username.strip().lower()
    user = (
        db.query(User)
        .filter((User.login == ident) | (func.lower(User.email) == ident))
        .first()
    )
    if not user or not verify_password(form.password, user.password_hash):
        _record_attempt(key)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Email not verified")

    _reset_attempts(key)
    _normalize_profile_defaults(user, db)
    return LoginResponse(
        access_token=create_access_token(user.id),
        id=user.id,
        login=user.login,
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
    subjects = updates.pop("teacher_subjects", None)
    if current_user.role != UserRole.teacher:
        subjects = None

    for field, value in updates.items():
        setattr(current_user, field, value)

    if subjects is not None:
        set_teacher_subjects(current_user, subjects, db)

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
    if current_user.email and new_email == current_user.email.lower():
        raise HTTPException(400, "Новый email совпадает с текущим")

    existing = (
        db.query(User)
        .filter(
            (User.email == new_email) | (User.login == new_email),
            User.id != current_user.id,
        )
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
            expires_at=datetime.now(timezone.utc) + RESET_TOKEN_TTL,
        )
    )
    db.commit()

    from app.routers.notifications import send_email

    confirm_link = f"{config.APP_URL}/settings/change-email/confirm?token={token}"
    send_email(
        new_email,
        "Подтверждение смены email — Timing",
        _build_email_change_html(confirm_link, new_email),
    )
    return {"message": "Письмо с подтверждением отправлено на новую почту."}


@router.post("/me/change-email/confirm")
def confirm_email_change(token: str, db: Session = Depends(get_db)):
    record = (
        db.query(EmailChangeToken)
        .filter(
            EmailChangeToken.token == token,
            EmailChangeToken.is_used.is_(False),
            EmailChangeToken.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
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
    """Ученик показывает код родителю, тот вводит его в своём аккаунте."""
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

    now = datetime.now(timezone.utc)
    active_codes = {
        row[0]
        for row in db.query(ParentLinkToken.code)
        .filter(
            ParentLinkToken.is_used.is_(False),
            ParentLinkToken.expires_at > now,
        )
        .all()
    }
    code = ""
    for _ in range(20):
        candidate = _random_code(PARENT_CODE_DIGITS)
        if candidate not in active_codes:
            code = candidate
            break
    if not code:
        raise HTTPException(503, "Не удалось выпустить код. Попробуйте позже.")

    token = ParentLinkToken(
        student_id=current_user.id,
        code=code,
        expires_at=now + PARENT_LINK_TTL,
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
    key = f"parent-link:{current_user.id}"
    if _is_rate_limited(
        key, limit=PARENT_LINK_ATTEMPTS, window=PARENT_LINK_WINDOW
    ):
        raise HTTPException(429, "Too many attempts. Try later.")

    code = code.strip()
    if len(code) != PARENT_CODE_DIGITS or not code.isdigit():
        _record_attempt(key)
        raise HTTPException(400, "Invalid or expired code")

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
        _record_attempt(key)
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
        _record_attempt(key)
        raise HTTPException(400, "Invalid or expired code")

    db.add(ParentStudent(parent_id=current_user.id, student_id=student.id))
    token.is_used = True
    db.commit()
    _reset_attempts(key)
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


# MAX Bridge auth — привязка аккаунта MAX к внутреннему профилю


class MaxLinkRequest(BaseModel):
    max_user_id_hashed: str

    @field_validator("max_user_id_hashed")
    @classmethod
    def _valid_hash(cls, value: str) -> str:
        value = value.strip().lower()
        if not _HASH_RE.match(value):
            raise ValueError("MAX user hash must be 64 hex characters")
        return value


class MaxLinkResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    id: int
    login: str
    role: UserRole
    first_name: str
    last_name: str


class MaxRegisterRequest(BaseModel):
    """Регистрация из MAX-бота: логин, инвайт-код и имя из профиля MAX."""

    max_user_id_hashed: str
    login: str
    password: str
    invite_code: str
    first_name: str
    last_name: str

    @field_validator("max_user_id_hashed")
    @classmethod
    def _valid_hash(cls, value: str) -> str:
        value = value.strip().lower()
        if not _HASH_RE.match(value):
            raise ValueError("MAX user hash must be 64 hex characters")
        return value

    @field_validator("login")
    @classmethod
    def _valid_login(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Login must not be blank")
        if len(value) > 255:
            raise ValueError("Login must be <= 255 characters")
        if any(char.isspace() for char in value):
            raise ValueError("Login must not contain spaces")
        # Проверка свободного логина регистронезависимая, поэтому и храним
        # в нижнем регистре: иначе база пропустила бы "Ivan" и "ivan".
        return value.lower()

    @field_validator("invite_code")
    @classmethod
    def _invite_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Invite code must not be blank")
        if len(value) > 16:
            raise ValueError("Invite code must be <= 16 characters")
        return value

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
        if len(value) > MAX_NAME_LENGTH:
            raise ValueError(f"Name must be <= {MAX_NAME_LENGTH} characters")
        return value


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
    if not _HASH_RE.match(hashed_user_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="MAX user hash must be 64 hex characters",
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
        access_token=create_access_token(user.id),
        id=user.id,
        login=user.login,
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
    hashed = max_user_id_hashed.strip().lower()
    if not _HASH_RE.match(hashed):
        raise HTTPException(422, "MAX user hash must be 64 hex characters")
    existing = (
        db.query(User).filter(User.max_user_id == hashed).first()
    )
    if existing and existing.id != current_user.id:
        raise HTTPException(400, "MAX user already linked to another account")
    current_user.max_user_id = hashed
    db.commit()


@router.post(
    "/auth/max-register",
    response_model=MaxLinkResponse,
    status_code=201,
)
def max_register(
    body: MaxRegisterRequest,
    x_bot_signature: str | None = Header(default=None, alias="X-Bot-Signature"),
    db: Session = Depends(get_db),
):
    """Регистрирует аккаунт из MAX-бота: логин + инвайт-код.

    Email и пароль не запрашиваются (их у такого аккаунта нет). Роль
    определяет сам код: код класса зачисляет ученика в этот класс,
    одноразовый код из ``invite_codes`` делает пользователя учителем и
    гасится. MAX user_id сохраняется сразу, поэтому следующий
    ``/auth/max-link`` отдаёт JWT без повторной регистрации.
    """
    _verify_bot_signature(body.max_user_id_hashed, x_bot_signature)

    already = (
        db.query(User)
        .filter(User.max_user_id == body.max_user_id_hashed)
        .first()
    )
    if already:
        # Повтор после потерянного ответа: аккаунт уже создан, отдаём JWT,
        # иначе пользователь навсегда застрял бы на «попробуйте ещё раз».
        if not already.is_active:
            raise HTTPException(403, "Account is not active")
        return MaxLinkResponse(
            access_token=create_access_token(already.id),
            id=already.id,
            login=already.login,
            role=already.role,
            first_name=already.first_name,
            last_name=already.last_name,
        )
    if (
        db.query(User.id)
        .filter(func.lower(User.login) == body.login)
        .first()
    ):
        raise HTTPException(400, "Login already taken")

    code = body.invite_code.upper()
    class_ = db.query(Class).filter(Class.invite_code == code).first()
    invite = None
    if class_ is None:
        invite = (
            db.query(InviteCode)
            .filter(
                InviteCode.code == code,
                InviteCode.is_used.is_(False),
            )
            .first()
        )
    if class_ is None and invite is None:
        raise HTTPException(400, "Invalid or already used invite code")

    user = User(
        login=body.login,
        email=None,
        password_hash=hash_password(body.password),
        first_name=body.first_name,
        last_name=body.last_name,
        role=UserRole.student if class_ is not None else UserRole.teacher,
        is_active=True,
        is_verified=True,
        max_user_id=body.max_user_id_hashed,
        # Почты нет, поэтому и письма отправлять некуда.
        notify_email=False,
        notify_browser=True,
        notify_max=True,
        notify_digest_time="08:00",
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        # Гонка двух регистраций с одним логином или MAX user_id: проверки
        # выше не атомарны, окончательное слово за уникальными индексами.
        db.rollback()
        raise HTTPException(409, "Login or MAX account is already registered")

    if class_ is not None:
        db.add(ClassMembership(student_id=user.id, class_id=class_.id))
        db.flush()
        add_missing_assignment_tasks(user.id, class_.id, db)
        refresh_student_grade(user.id, db)
    else:
        invite.is_used = True
        invite.used_by_id = user.id
        invite.used_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(user)
    return MaxLinkResponse(
        access_token=create_access_token(user.id),
        id=user.id,
        login=user.login,
        role=user.role,
        first_name=user.first_name,
        last_name=user.last_name,
    )


MAX_CODE_TTL = timedelta(minutes=10)


class MaxCodeIssueRequest(BaseModel):
    max_user_id_hashed: str

    @field_validator("max_user_id_hashed")
    @classmethod
    def _valid_hash(cls, value: str) -> str:
        value = value.strip().lower()
        if not _HASH_RE.match(value):
            raise ValueError("MAX user hash must be 64 hex characters")
        return value


class MaxCodeLoginRequest(BaseModel):
    code: str


@router.post("/auth/max-code/issue")
def issue_max_code(
    body: MaxCodeIssueRequest,
    x_bot_signature: str | None = Header(default=None, alias="X-Bot-Signature"),
    db: Session = Depends(get_db),
):
    """Бот выдаёт одноразовый код для входа в SPA."""
    _verify_bot_signature(body.max_user_id_hashed, x_bot_signature)
    user = (
        db.query(User)
        .filter(User.max_user_id == body.max_user_id_hashed)
        .first()
    )
    if not user:
        raise HTTPException(404, "MAX account is not registered")
    if not user.is_active:
        raise HTTPException(403, "Account is not active")

    db.query(MaxLoginCode).filter(
        MaxLoginCode.user_id == user.id,
        MaxLoginCode.is_used.is_(False),
    ).update({"is_used": True}, synchronize_session=False)
    db.commit()

    code = ""
    for _ in range(10):
        candidate = "".join(str(secrets.randbelow(10)) for _ in range(6))
        taken = (
            db.query(MaxLoginCode.id)
            .filter(
                MaxLoginCode.code == candidate,
                MaxLoginCode.is_used.is_(False),
            )
            .first()
        )
        if not taken:
            code = candidate
            break
    if not code:
        raise HTTPException(503, "Could not create a login code")

    db.add(
        MaxLoginCode(
            code=code,
            user_id=user.id,
            expires_at=datetime.now(timezone.utc) + MAX_CODE_TTL,
        )
    )
    db.commit()
    return {"code": code, "expires_in": int(MAX_CODE_TTL.total_seconds())}


@router.post("/auth/max-code", response_model=MaxLinkResponse)
def max_code_login(
    body: MaxCodeLoginRequest,
    db: Session = Depends(get_db),
):
    """Меняет код из бота на JWT. Код одноразовый и живёт 10 минут."""
    code = body.code.strip()
    if not code or len(code) > 8:
        raise HTTPException(400, "Invalid code")

    key = f"max-code:{code}"
    if _is_rate_limited(key, limit=5, window=600):
        raise HTTPException(429, "Too many attempts. Try later.")

    record = (
        db.query(MaxLoginCode)
        .filter(
            MaxLoginCode.code == code,
            MaxLoginCode.is_used.is_(False),
        )
        .order_by(MaxLoginCode.created_at.desc())
        .first()
    )
    expires_at = record.expires_at if record else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if not record or expires_at < datetime.now(timezone.utc):
        _record_attempt(key)
        raise HTTPException(400, "Code expired or invalid")

    user = db.query(User).filter(User.id == record.user_id).first()
    if not user or not user.is_active:
        _record_attempt(key)
        raise HTTPException(403, "Account is not active")

    record.is_used = True
    db.commit()
    _reset_attempts(key)
    return MaxLinkResponse(
        access_token=create_access_token(user.id),
        id=user.id,
        login=user.login,
        role=user.role,
        first_name=user.first_name,
        last_name=user.last_name,
    )


# Автовход из мини-приложения MAX (Web App)

_WEAK_SALTS = {"", "changeme", "timingvk-default-salt"}


def _max_user_hash(raw_id: int | str) -> str:
    """Тот же хеш MAX user_id, что считает бот (bot/core/security.py).

    Соль и строка ``timingvk:user-id-salt:v1`` обязаны совпадать с ботом,
    иначе привязки не найдутся. Это внутренняя строка, а не бренд.
    """
    raw_salt = config.BOT_USER_ID_SALT.strip()
    if raw_salt not in _WEAK_SALTS and len(raw_salt) >= 16:
        salt = raw_salt.encode()
    else:
        salt = hmac.new(
            config.MAX_BOT_TOKEN.encode(),
            b"timingvk:user-id-salt:v1",
            hashlib.sha256,
        ).digest()
    normalized = str(int(str(raw_id).strip()))
    return hmac.new(salt, f"max:{normalized}".encode(), hashlib.sha256).hexdigest()


def _verify_webapp_init_data(init_data: str) -> int:
    """Проверяет подпись initData мини-приложения MAX и достаёт user_id.

    Схема как у Telegram Mini Apps: поля ``key=value`` через ``&``, строка
    для проверки — ключи по алфавиту без ``hash``, секрет — HMAC от токена
    бота с ключом ``WebAppData``. Сверить с документацией MAX при
    расхождении: меняется только эта функция.
    """
    import json
    from urllib.parse import parse_qsl, unquote

    secret = config.MAX_BOT_TOKEN
    if not secret:
        raise HTTPException(503, "MAX bot integration is not configured")
    raw_pairs = parse_qsl(init_data, keep_blank_values=True)
    if len({k for k, _ in raw_pairs}) != len(raw_pairs):
        raise HTTPException(401, "Duplicate initData parameter")
    pairs = dict(raw_pairs)
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "Invalid initData")
    data_check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    signing_key = hmac.new(
        b"WebAppData", secret.encode(), hashlib.sha256
    ).digest()
    expected = hmac.new(
        signing_key, data_check.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise HTTPException(401, "Invalid initData signature")
    auth_date = pairs.get("auth_date")
    if auth_date and auth_date.isdigit():
        if time.time() - int(auth_date) > 24 * 3600:
            raise HTTPException(401, "initData expired")
    user_raw = pairs.get("user") or pairs.get("user_id") or pairs.get("id")
    if not user_raw:
        raise HTTPException(401, "No user in initData")
    try:
        return int(json.loads(unquote(user_raw))["id"])
    except (ValueError, KeyError, TypeError):
        try:
            return int(user_raw.strip())
        except ValueError:
            raise HTTPException(401, "No user id in initData") from None


class MaxWebAppRequest(BaseModel):
    init_data: str


@router.post("/auth/max-webapp", response_model=MaxLinkResponse)
def max_webapp_login(body: MaxWebAppRequest, db: Session = Depends(get_db)):
    """Автовход из мини-приложения MAX: initData → JWT без кода и пароля."""
    max_id = _verify_webapp_init_data(body.init_data)
    hashed = _max_user_hash(max_id)
    user = db.query(User).filter(User.max_user_id == hashed).first()
    if not user:
        raise HTTPException(
            404, "MAX user not linked. Register in the bot first."
        )
    if not user.is_active:
        raise HTTPException(403, "Account is not active")
    _normalize_profile_defaults(user, db)
    return MaxLinkResponse(
        access_token=create_access_token(user.id),
        id=user.id,
        login=user.login,
        role=user.role,
        first_name=user.first_name,
        last_name=user.last_name,
    )


# Сброс пароля
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
    record = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.token == body.token,
            PasswordResetToken.is_used.is_(False),
            PasswordResetToken.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
    if not record:
        raise HTTPException(400, "Invalid or expired reset link")

    user = db.query(User).filter(User.id == record.user_id).first()
    if not user:
        raise HTTPException(400, "Invalid or expired reset link")

    user.password_hash = hash_password(body.new_password)
    record.is_used = True
    db.commit()
    return {"message": "Password changed successfully"}
