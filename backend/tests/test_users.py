"""Аутентификационные сценарии и общие зависимости.

Идут через TestClient против настоящего PostgreSQL, поэтому проверяется полный
жизненный цикл запроса — включая lifespan, который вызывает ``create_tables()``,
и отправку письма с OTP.
"""
import pytest
from fastapi import HTTPException

from app.dependencies import get_current_user, require_role
from app.models import ClassMembership, User, UserRole
from app.routers.users import (
    MaxRegisterRequest,
    ProfileUpdateRequest,
    RegisterRequest,
    create_access_token,
    hash_password,
    invite_code_info,
    login_available,
    max_register,
    verify_password,
)


# ---------------------------------------------------------------------------
# Пароль и токены
# ---------------------------------------------------------------------------


def test_password_round_trip():
    hashed = hash_password("supersecret123")
    assert hashed != "supersecret123"
    assert verify_password("supersecret123", hashed)
    assert not verify_password("wrong", hashed)


def test_access_token_round_trip():
    """Регрессия: TTL раньше был строкой, поэтому каждый вход падал с 500."""
    from jose import jwt

    token = create_access_token(42)
    payload = jwt.decode(token, "test-secret-key", algorithms=["HS256"])
    assert payload["sub"] == "42"
    assert "exp" in payload


def test_token_expiry_is_numeric():
    """Нечисловой TTL снова сломал бы timedelta()."""
    from app import config

    assert isinstance(config.ACCESS_TOKEN_EXPIRE_MINUTES, int)


def test_require_role_allows_matching_role():
    dependency = require_role(UserRole.teacher)
    teacher = type("U", (), {"role": UserRole.teacher})()
    assert dependency(teacher) is teacher


def test_require_role_rejects_other_role():
    from fastapi import HTTPException

    dependency = require_role(UserRole.teacher)
    student = type("U", (), {"role": UserRole.student})()
    with pytest.raises(HTTPException) as exc:
        dependency(student)
    assert exc.value.status_code == 403


def test_single_get_current_user_implementation():
    """Зависимость раньше существовала в двух экземплярах, и копии разошлись."""
    from app import dependencies
    from app.routers import users as users_router

    assert users_router.get_current_user is dependencies.get_current_user
    assert users_router.require_role is dependencies.require_role


# ---------------------------------------------------------------------------
# Валидация схем
# ---------------------------------------------------------------------------


def test_grade_is_not_self_service():
    """Ученик раньше мог переписать собственный класс через PATCH /me."""
    assert "grade" not in ProfileUpdateRequest.model_fields


@pytest.mark.parametrize("bad", ["abc", "25:00", "9", "", "9:5"])
def test_digest_time_must_be_hhmm(bad):
    with pytest.raises(ValueError):
        ProfileUpdateRequest(notify_digest_time=bad)


def test_digest_time_accepts_hhmm():
    assert ProfileUpdateRequest(
        notify_digest_time="23:59"
    ).notify_digest_time == "23:59"


def test_registration_rejects_short_password():
    with pytest.raises(ValueError):
        RegisterRequest(
            email="a@b.ru", password="short",
            first_name="A", last_name="B", role=UserRole.student,
        )


def test_registration_rejects_blank_name():
    with pytest.raises(ValueError):
        RegisterRequest(
            email="a@b.ru", password="longenough",
            first_name="   ", last_name="B", role=UserRole.student,
        )


def test_registration_rejects_admin_role():
    """Админ создаётся бутстрапом, а не самозаписью."""
    with pytest.raises(ValueError):
        RegisterRequest(
            email="a@b.ru", password="longenough",
            first_name="A", last_name="B", role=UserRole.admin,
        )


# ---------------------------------------------------------------------------
# Проверка свободного логина (бот, шаг «Придумайте логин»)
# ---------------------------------------------------------------------------


def test_login_check_reports_taken_and_free(db, make_user):
    make_user("busy-login@test.local", UserRole.student, login="Busy")
    # Регистр не важен: иначе обходятся два аккаунта, различающихся им.
    assert login_available("busy", db) == {"available": False}
    assert login_available("BUSY", db) == {"available": False}
    assert login_available("free", db) == {"available": True}


def test_login_check_rejects_blank_login(db):
    with pytest.raises(HTTPException) as error:
        login_available("   ", db)
    assert error.value.status_code == 400


# ---------------------------------------------------------------------------
# Регистрация из MAX-бота: инвайт-код и создание аккаунта
# ---------------------------------------------------------------------------

HASH = "a" * 64


def _bot_signature(hashed: str) -> str:
    import hashlib
    import hmac

    from app import config

    return hmac.new(
        config.MAX_BOT_TOKEN.encode(),
        hashed.encode(),
        hashlib.sha256,
    ).hexdigest()


def _max_body(**overrides) -> MaxRegisterRequest:
    payload = {
        "max_user_id_hashed": HASH,
        "login": "NewStudent",
        "password": "supersecret123",
        "invite_code": "AAAA111111",
        "first_name": "Иван",
        "last_name": "Иванов",
    }
    payload.update(overrides)
    return MaxRegisterRequest(**payload)


def test_invite_code_knows_its_role(db, klass, invite_code):
    """Бот должен понимать по коду, ученика или учителя он регистрирует."""
    info = invite_code_info(klass.invite_code.lower(), db)
    assert info.valid is True
    assert info.role == UserRole.student
    assert info.class_name == klass.name

    teacher_info = invite_code_info(invite_code.code.lower(), db)
    assert teacher_info.valid is True
    assert teacher_info.role == UserRole.teacher
    assert teacher_info.class_name is None

    assert invite_code_info("NOPE", db).valid is False


def test_max_register_creates_student_in_class(db, klass):
    body = _max_body()
    response = max_register(body, _bot_signature(HASH), db)

    user = db.query(User).filter(User.login == "newstudent").first()
    assert user is not None
    assert user.role == UserRole.student
    assert user.email is None
    assert user.password_hash is not None
    assert verify_password("supersecret123", user.password_hash)
    assert user.max_user_id == HASH
    assert user.is_active and user.is_verified
    assert user.grade == klass.name

    membership = (
        db.query(ClassMembership)
        .filter(ClassMembership.student_id == user.id)
        .first()
    )
    assert membership is not None
    assert membership.class_id == klass.id
    assert response.role == UserRole.student


def test_max_register_teacher_consumes_invite(db, invite_code):
    body = _max_body(invite_code=invite_code.code, login="teacher-max")
    response = max_register(body, _bot_signature(HASH), db)

    user = db.query(User).filter(User.login == "teacher-max").first()
    assert user is not None
    assert user.role == UserRole.teacher
    assert response.role == UserRole.teacher

    db.refresh(invite_code)
    assert invite_code.is_used is True
    assert invite_code.used_by_id == user.id


def test_max_register_is_idempotent(db, klass):
    """Повторная отправка после потерянного ответа не должна застревать."""
    body = _max_body()
    signature = _bot_signature(HASH)
    first = max_register(body, signature, db)
    second = max_register(body, signature, db)

    assert first.access_token
    assert second.access_token
    assert db.query(User).filter(User.login == "newstudent").count() == 1


def test_max_register_rejects_busy_login(db, klass, make_user):
    make_user("busy@test.local", UserRole.student, login="busy")
    with pytest.raises(HTTPException) as error:
        max_register(_max_body(login="BUSY"), _bot_signature(HASH), db)
    assert error.value.status_code == 400


def test_max_register_rejects_unknown_invite(db):
    with pytest.raises(HTTPException) as error:
        max_register(_max_body(invite_code="NOPE"), _bot_signature(HASH), db)
    assert error.value.status_code == 400


def test_max_register_requires_bot_signature(db):
    with pytest.raises(HTTPException) as error:
        max_register(_max_body(), "not-a-signature", db)
    assert error.value.status_code == 403


def test_max_register_normalizes_login():
    body = _max_body(login="  NewStudent  ")
    assert body.login == "newstudent"


def test_max_register_rejects_login_with_spaces():
    with pytest.raises(ValueError):
        _max_body(login="Ivan Petrov")


def test_passwordless_account_cannot_log_in_by_password():
    """У аккаунта из MAX нет хеша: вход по паролю должен быть отказом, не 500."""
    assert verify_password("anything", None) is False


# ---------------------------------------------------------------------------
# Полный HTTP-сценарий
# ---------------------------------------------------------------------------


def _register(client, email, password="supersecret123", role="student",
              invite=None):
    payload = {
        "email": email,
        "password": password,
        "first_name": "Test",
        "last_name": "User",
        "role": role,
    }
    if invite:
        payload["invite_code"] = invite
    return client.post("/api/users/register", json=payload)


def test_registration_verification_and_login(client):
    """Сквозной сценарий: регистрация -> подтверждение -> вход -> профиль."""
    response = _register(client, "flow@example.com")
    assert response.status_code == 201
    assert client.sent_emails, "verification email was not attempted"

    # OTP лежит в теле письма; достаём его так же, как это делает интерфейс.
    body = client.sent_emails[0][2]
    code = _extract_code(body)

    verify = client.post("/api/users/verify-email", json={"code": code})
    assert verify.status_code == 200

    login = client.post(
        "/api/users/login",
        data={"username": "flow@example.com", "password": "supersecret123"},
    )
    assert login.status_code == 200, login.text
    payload = login.json()
    assert payload["role"] == "student"
    assert payload["access_token"]

    token = payload["access_token"]
    me = client.get(
        "/api/users/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == "flow@example.com"


def test_login_rejects_wrong_password(client):
    _register(client, "wrong@example.com")
    code = _extract_code(client.sent_emails[0][2])
    client.post("/api/users/verify-email", json={"code": code})

    login = client.post(
        "/api/users/login",
        data={"username": "wrong@example.com", "password": "nope"},
    )
    assert login.status_code == 401


def test_login_blocked_before_verification(client):
    _register(client, "unverified@example.com")
    login = client.post(
        "/api/users/login",
        data={"username": "unverified@example.com", "password": "supersecret123"},
    )
    assert login.status_code == 403


def test_protected_route_requires_token(client):
    assert client.get("/api/users/me").status_code == 401


def test_protected_route_rejects_garbage_token(client):
    response = client.get(
        "/api/users/me", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert response.status_code == 401


def test_deactivated_user_loses_access(client):
    """``get_current_user`` должен отвергать неактивные учётные записи."""
    _register(client, "deact@example.com")
    code = _extract_code(client.sent_emails[0][2])
    client.post("/api/users/verify-email", json={"code": code})

    login = client.post(
        "/api/users/login",
        data={"username": "deact@example.com", "password": "supersecret123"},
    )
    token = login.json()["access_token"]

    from app.database import SessionLocal
    from app.models import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "deact@example.com").one()
        user.is_active = False
        db.commit()
    finally:
        db.close()

    response = client.get(
        "/api/users/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403


def test_teacher_registration_requires_invite(client):
    response = _register(client, "t@example.com", role="teacher")
    assert response.status_code == 400


def test_admin_registration_is_rejected_over_http(client):
    response = _register(client, "evil@example.com", role="admin")
    assert response.status_code == 422


def test_reregistration_cannot_overwrite_pending_account(client):
    """Атакующий не должен подменять пароль чужой неподтверждённой заявки."""
    assert _register(client, "pending@example.com").status_code == 201
    second = _register(
        client, "pending@example.com", password="attackerpass1"
    )
    assert second.status_code == 400

    code = _extract_code(client.sent_emails[0][2])
    assert client.post(
        "/api/users/verify-email", json={"code": code}
    ).status_code == 200

    # Старый пароль по-прежнему рабочий, пароль атакующего — нет.
    assert client.post(
        "/api/users/login",
        data={
            "username": "pending@example.com",
            "password": "supersecret123",
        },
    ).status_code == 200
    assert client.post(
        "/api/users/login",
        data={
            "username": "pending@example.com",
            "password": "attackerpass1",
        },
    ).status_code == 401


def test_teacher_registration_with_valid_invite(client, real_db):
    """Инвайт должен существовать, чтобы его увидела собственная сессия клиента."""
    from app.models import InviteCode, User, UserRole

    admin = (
        real_db.query(User)
        .filter(User.role == UserRole.admin)
        .order_by(User.id.asc())
        .first()
    )
    assert admin is not None, "lifespan should have bootstrapped an admin"
    real_db.add(
        InviteCode(code="TEACHERCODE1234", created_by_id=admin.id)
    )
    real_db.commit()

    response = _register(
        client, "teacherflow@example.com", role="teacher",
        invite="TEACHERCODE1234",
    )
    assert response.status_code == 201, response.text


def test_invite_is_consumed_on_verification(client, real_db):
    """Инвайт учителя не должен быть переиспользуемым."""
    from app.models import EmailVerification, InviteCode, User, UserRole

    admin = (
        real_db.query(User)
        .filter(User.role == UserRole.admin)
        .order_by(User.id.asc())
        .first()
    )
    real_db.add(InviteCode(code="ONESHOTCODE1234", created_by_id=admin.id))
    real_db.commit()

    assert _register(
        client, "oneshot@example.com", role="teacher",
        invite="ONESHOTCODE1234",
    ).status_code == 201

    code = _extract_code(client.sent_emails[0][2])
    assert client.post(
        "/api/users/verify-email", json={"code": code}
    ).status_code == 200

    invite = real_db.query(InviteCode).filter(
        InviteCode.code == "ONESHOTCODE1234"
    ).one()
    assert invite.is_used is True

    # Второй учитель уже не может использовать тот же код.
    assert _register(
        client, "second@example.com", role="teacher",
        invite="ONESHOTCODE1234",
    ).status_code == 400


def test_missing_page_returns_shell_not_500(client):
    """Старые адреса отдают оболочку SPA: разбирать их больше не нужно."""
    for path in ("/parent/dashboard", "/student/profile"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "<title>Вектор</title>" in response.text, path


def test_existing_page_is_served(client):
    assert client.get("/login").status_code == 200


def test_health_endpoint(client):
    assert client.get("/health").json() == {"status": "ok"}


def _extract_code(html_body: str) -> str:
    """Достаёт 6-значный OTP из тела письма с подтверждением."""
    import re

    match = re.search(r"letter-spacing:12px[^>]*>(\d{6})<", html_body)
    assert match, "verification code not found in email body"
    return match.group(1)