from datetime import datetime, timedelta, timezone
from random import randint
from dotenv import load_dotenv
import secrets
import os

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.database import get_db
from app.models import User, UserRole, InviteCode, EmailVerification, ParentStudent, ParentLinkToken, PasswordResetToken, EmailChangeToken
from app.dependencies import get_current_user

load_dotenv()

SECRET_KEY = os.environ.get("SECRET_KEY")
ALGORITHM = os.environ.get("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "10080")
APP_URL = os.environ.get("APP_URL", "https://vkcollege.ru")

router = APIRouter(prefix="/api/users", tags=["users"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/users/login")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    role: UserRole
    invite_code: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: UserRole
    first_name: str
    last_name: str


class VerifyEmailRequest(BaseModel):
    code: str


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
    grade: str | None = None
    teacher_subject: str | None = None
    avatar_url: str | None = None
    notify_email: bool | None = None
    notify_browser: bool | None = None
    notify_digest_time: str | None = None


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class EmailChangeRequest(BaseModel):
    email: EmailStr


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": str(user_id), "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    exc = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise exc
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise exc
    return user


def require_role(*roles: UserRole):
    def dependency(current_user: User = Depends(get_current_user)):
        if current_user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return current_user
    return dependency


def _normalize_profile_defaults(user: User, db: Session | None = None) -> User:
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




def _create_verification(user_id: int, db: Session) -> str:
    code = str(randint(100000, 999999))
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    db.add(EmailVerification(user_id=user_id, code=code, expires_at=expires))
    db.commit()
    return code


def _build_email_change_html(confirm_link: str, new_email: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Смена email — Vector</title>
</head>
<body style="margin:0;padding:0;background:#0f0e13;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f0e13;padding:40px 0;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">
          <tr>
            <td style="background:linear-gradient(135deg,#1a1825 0%,#2d2a3e 100%);border-radius:20px 20px 0 0;padding:32px 40px 24px;border-bottom:1px solid rgba(255,255,255,0.08);">
              <div style="display:inline-block;background:linear-gradient(135deg,#8B79FF,#6b5ce7);border-radius:12px;padding:8px 14px;color:#fff;font-size:15px;font-weight:700;">Vector</div>
            </td>
          </tr>
          <tr>
            <td style="background:#18171c;padding:36px 40px;border-left:1px solid rgba(255,255,255,0.05);border-right:1px solid rgba(255,255,255,0.05);">
              <h1 style="margin:0 0 8px;color:#fff;font-size:24px;font-weight:700;letter-spacing:-0.5px;">Смена email</h1>
              <p style="margin:0 0 28px;color:rgba(255,255,255,0.5);font-size:15px;line-height:1.6;">
                Подтвердите новый адрес <strong style="color:rgba(255,255,255,0.86);">{new_email}</strong>. Ссылка действительна <strong style="color:rgba(255,255,255,0.8);">1 час</strong>.
              </p>
              <div style="text-align:center;margin-bottom:28px;">
                <a href="{confirm_link}" style="display:inline-block;background:linear-gradient(135deg,#8B79FF,#6b5ce7);color:#fff;font-size:15px;font-weight:600;text-decoration:none;padding:14px 32px;border-radius:12px;letter-spacing:-0.2px;">
                  Подтвердить смену email
                </a>
              </div>
              <p style="margin:0 0 8px;color:rgba(255,255,255,0.3);font-size:12px;">Или скопируйте ссылку в браузер:</p>
              <p style="margin:0;background:rgba(255,255,255,0.05);border-radius:8px;padding:10px 14px;font-size:12px;color:rgba(255,255,255,0.4);word-break:break-all;">{confirm_link}</p>
            </td>
          </tr>
          <tr>
            <td style="background:#13121a;border-radius:0 0 20px 20px;padding:20px 40px;border:1px solid rgba(255,255,255,0.05);border-top:none;">
              <p style="margin:0;color:rgba(255,255,255,0.25);font-size:12px;line-height:1.5;">
                Это автоматическое письмо от Vector. Не отвечайте на него.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/register", status_code=201)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.email == body.email).first()
    
    if existing_user:
        if existing_user.is_active:
            raise HTTPException(400, "Email already registered")
        else:
            db.query(EmailVerification).filter(EmailVerification.user_id == existing_user.id).delete()
            db.query(PasswordResetToken).filter(PasswordResetToken.user_id == existing_user.id).delete()
            db.delete(existing_user)
            db.flush()

    if body.role == UserRole.teacher:
        if not body.invite_code:
            raise HTTPException(400, "Invite code required for teacher registration")
        invite = db.query(InviteCode).filter(
            InviteCode.code == body.invite_code,
            InviteCode.is_used == False
        ).first()
        if not invite:
            raise HTTPException(400, "Invalid or already used invite code")

    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    user = User(
        email=body.email,
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
        teacher_invite_code=body.invite_code if body.role == UserRole.teacher else None,
    )
    db.add(user)
    db.flush()  # получаем user.id до commit

    code = _create_verification(user.id, db)
    from app.routers.notifications import send_verification_email
    try:
        send_verification_email(user.email, code)
    except Exception as e:
        raise HTTPException(503, f"Не удалось отправить письмо с кодом подтверждения: {e}")
    db.commit()
    return {"message": "Registration successful. Check your email for verification code."}


@router.post("/verify-email")
def verify_email(body: VerifyEmailRequest, db: Session = Depends(get_db)):
    record = db.query(EmailVerification).filter(
        EmailVerification.code == body.code,
        EmailVerification.is_used == False,
    ).order_by(EmailVerification.created_at.desc()).first()

    if not record:
        raise HTTPException(400, "Invalid code")
    if record.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(400, "Code expired")

    record.is_used = True
    user = db.query(User).filter(User.id == record.user_id).first()
    user.is_active = True
    user.is_verified = True
    if user.role == UserRole.teacher and user.teacher_invite_code:
        invite = db.query(InviteCode).filter(
            InviteCode.code == user.teacher_invite_code,
            InviteCode.is_used == False,
        ).first()
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
    user = db.query(User).filter(User.email == email, User.is_verified == False).first()
    if not user:
        raise HTTPException(400, "User not found or already verified")
    code = _create_verification(user.id, db)
    from app.routers.notifications import send_verification_email
    try:
        send_verification_email(user.email, code)
    except Exception as e:
        raise HTTPException(503, f"Не удалось отправить письмо с кодом подтверждения: {e}")
    return {"message": "Code sent"}


@router.post("/login", response_model=LoginResponse)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Email not verified")
    return LoginResponse(
        access_token=create_access_token(user.id),
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
    updates = body.model_dump(exclude_none=True)
    if current_user.role != UserRole.teacher:
        updates.pop("teacher_subject", None)
    if current_user.role != UserRole.student:
        updates.pop("grade", None)

    for field, value in updates.items():
        setattr(current_user, field, value)
    db.commit()
    db.refresh(current_user)
    return _normalize_profile_defaults(current_user)


@router.post("/me/change-password")
def change_password(
    body: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(400, "Wrong current password")
    if len(body.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    current_user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"message": "Password changed"}


@router.post("/me/change-email")
def request_email_change(
    body: EmailChangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    new_email = body.email.strip().lower()
    if new_email == current_user.email.lower():
        raise HTTPException(400, "Новый email совпадает с текущим")

    existing = db.query(User).filter(User.email == new_email, User.id != current_user.id).first()
    if existing:
        raise HTTPException(400, "Этот email уже занят")

    db.query(EmailChangeToken).filter(
        EmailChangeToken.user_id == current_user.id,
        EmailChangeToken.is_used == False,
    ).update({"is_used": True})

    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    db.add(EmailChangeToken(user_id=current_user.id, new_email=new_email, token=token, expires_at=expires))
    db.commit()

    from app.routers.notifications import send_email

    confirm_link = f"{APP_URL}/settings/change-email/confirm?token={token}"
    send_email(
        new_email,
        "Подтверждение смены email — Vector",
        _build_email_change_html(confirm_link, new_email),
    )
    return {"message": "Письмо с подтверждением отправлено на новую почту."}


@router.post("/me/change-email/confirm")
def confirm_email_change(
    token: str,
    db: Session = Depends(get_db),
):
    record = db.query(EmailChangeToken).filter(
        EmailChangeToken.token == token,
        EmailChangeToken.is_used == False,
        EmailChangeToken.expires_at > datetime.now(timezone.utc),
    ).first()

    if not record:
        raise HTTPException(400, "Недействительная или просроченная ссылка")

    user = db.query(User).filter(User.id == record.user_id).first()
    if not user:
        raise HTTPException(404, "Пользователь не найден")

    existing = db.query(User).filter(User.email == record.new_email, User.id != user.id).first()
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
    """Ученик генерирует 6-значный код. Родитель вводит его у себя в кабинете."""
    existing = db.query(ParentLinkToken).filter(
        ParentLinkToken.student_id == current_user.id,
        ParentLinkToken.is_used == False,
        ParentLinkToken.expires_at > datetime.now(timezone.utc),
    ).first()
    if existing:
        return {"code": existing.code, "expires_at": existing.expires_at}

    code = str(randint(100000, 999999))
    expires = datetime.now(timezone.utc) + timedelta(hours=24)
    token = ParentLinkToken(student_id=current_user.id, code=code, expires_at=expires)
    db.add(token)
    db.commit()
    return {"code": code, "expires_at": expires}


@router.post("/me/link-parent")
def link_parent(
    code: str,
    current_user: User = Depends(require_role(UserRole.parent)),
    db: Session = Depends(get_db),
):
    """Родитель вводит код из кабинета ученика и привязывается к нему."""
    token = db.query(ParentLinkToken).filter(
        ParentLinkToken.code == code,
        ParentLinkToken.is_used == False,
        ParentLinkToken.expires_at > datetime.now(timezone.utc),
    ).first()
    if not token:
        raise HTTPException(400, "Invalid or expired code")

    already = db.query(ParentStudent).filter(
        ParentStudent.parent_id == current_user.id,
        ParentStudent.student_id == token.student_id,
    ).first()
    if already:
        raise HTTPException(400, "Already linked")

    db.add(ParentStudent(parent_id=current_user.id, student_id=token.student_id))
    token.is_used = True
    db.commit()

    student = db.query(User).filter(User.id == token.student_id).first()
    return {"message": "Linked successfully", "student_name": f"{student.first_name} {student.last_name}"}


@router.get("/me/children")
def get_children(
    current_user: User = Depends(require_role(UserRole.parent)),
    db: Session = Depends(get_db),
):
    links = db.query(ParentStudent).filter(ParentStudent.parent_id == current_user.id).all()
    result = []
    for link in links:
        s = db.query(User).filter(User.id == link.student_id).first()
        if s:
            result.append({"id": s.id, "first_name": s.first_name, "last_name": s.last_name, "email": s.email, "grade": s.grade})
    return result


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str


@router.post("/password-reset/request")
def request_password_reset(body: PasswordResetRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user:
        return {"message": "Если такой email зарегистрирован, письмо отправлено."}

    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.is_used == False,
    ).update({"is_used": True})

    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    db.add(PasswordResetToken(user_id=user.id, token=token, expires_at=expires))
    db.commit()
    from app.routers.notifications import send_password_reset_email
    reset_link = f"{APP_URL}/reset-password-new?token={token}"
    try:
        send_password_reset_email(user.email, reset_link)
    except Exception as e:
        print(f"[MAIL] Ошибка отправки письма сброса пароля: {e}")

    return {"message": "Если такой email зарегистрирован, письмо отправлено."}


@router.post("/password-reset/confirm")
def confirm_password_reset(body: PasswordResetConfirm, db: Session = Depends(get_db)):
    if len(body.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    record = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == body.token,
        PasswordResetToken.is_used == False,
        PasswordResetToken.expires_at > datetime.now(timezone.utc),
    ).first()

    if not record:
        raise HTTPException(400, "Invalid or expired reset link")

    user = db.query(User).filter(User.id == record.user_id).first()
    user.password_hash = hash_password(body.new_password)
    record.is_used = True
    db.commit()

    return {"message": "Password changed successfully"}
