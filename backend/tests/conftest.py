"""Общие фикстуры pytest.

Сьют работает против **настоящего PostgreSQL** — именно он используется в
продакшене. SQLite намеренно не поддерживается: под ним не проверяются
нативные enum-типы, реальное поведение внешних ключей и типы возврата
агрегатов, на которые опирается этот код, поэтому тесты могли бы проходить
здесь и падать в проде.

Переменная ``TEST_DATABASE_URL`` должна указывать на одноразовую базу,
например созданную так::

    docker compose up -d db
    docker exec vector_db psql -U vector -d vectordb \
        -c "CREATE DATABASE vectordb_test;"
    $env:TEST_DATABASE_URL="postgresql://vector:pw@127.0.0.1:5432/vectordb_test"

Схема один раз за сессию пересоздаётся, а каждый тест выполняется внутри
транзакции, которая затем откатывается.
"""
import os

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

if not TEST_DATABASE_URL:
    raise RuntimeError(
        "TEST_DATABASE_URL is not set. These tests require PostgreSQL.\n"
        "  docker compose up -d db\n"
        '  docker exec vector_db psql -U vector -d vectordb '
        '-c "CREATE DATABASE vectordb_test;"\n'
        "  cd backend\n"
        "  python run_tests.py -v\n"
        "\n"
        "Or set it manually (only for the current shell):\n"
        '  $env:TEST_DATABASE_URL="postgresql://vector:<DB_PASS>'
        '@127.0.0.1:5432/vectordb_test"\n'
        "  pytest -v\n"
        "\n"
        "Use 127.0.0.1, not localhost: Docker listens on IPv4."
    )

if not TEST_DATABASE_URL.startswith("postgresql"):
    raise RuntimeError(
        "TEST_DATABASE_URL must be a postgresql:// URL; this project does not "
        f"support other engines (got: {TEST_DATABASE_URL.split(':', 1)[0]}://)"
    )

# На Windows подключение к «localhost» иногда уходит в IPv6 (::1) и висит:
# Docker Desktop слушает IPv4. Таймаут превращает зависание в понятную
# ошибку, а не в молчаливый простой сьюта.
if "connect_timeout" not in TEST_DATABASE_URL:
    separator = "&" if "?" in TEST_DATABASE_URL else "?"
    TEST_DATABASE_URL += separator + "connect_timeout=5"

# Приложение читает конфигурацию в момент импорта, поэтому тестовый URL
# должен быть выставлен до того, как app.config импортируется где-либо.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "10080")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.local")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("SMTP_HOST", "")
os.environ.setdefault("OLLAMA_URL", "http://localhost:11434")
os.environ.setdefault("OLLAMA_MODEL", "qwen2.5:7b")
os.environ.setdefault("APP_URL", "http://localhost:8000")
# Подпись запросов бота (X-Bot-Signature) для /api/users/auth/max-*.
os.environ.setdefault("MAX_BOT_TOKEN", "test-bot-token")
os.environ.setdefault("DEBUG", "true")
# Фоновые задачи срабатывали бы по таймеру во время прогона сьюта.
os.environ.setdefault("ENABLE_SCHEDULER", "false")

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.database import get_db  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models import (  # noqa: E402
    AIMessage,
    Assignment,
    Base,
    Class,
    ClassMembership,
    EmailChangeToken,
    EmailVerification,
    Grade,
    InviteCode,
    Notification,
    ParentLinkToken,
    ParentStudent,
    PasswordResetToken,
    Schedule,
    Task,
    User,
    UserRole,
)

NOW = datetime.now(timezone.utc)


def _truncate_all(eng) -> None:
    """Очищает все таблицы, не трогая enum-типы.

    Используется между HTTP-тестами. ``DROP SCHEMA`` инвалидировал бы
    подготовленные выражения у любого соединения с открытой транзакцией,
    и это всплывало бы как ``cache lookup failed for type <oid>``.
    """
    table_names = ",".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
    if not table_names:
        return
    with eng.begin() as conn:
        conn.execute(
            text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE")
        )


def _reset_schema(eng) -> None:
    """Удаляет и заново создаёт схему public.

    ``Base.metadata.drop_all`` оставляет после себя enum-типы PostgreSQL, а
    соединения в пуле удерживают подготовленные выражения со ссылками на них —
    это всплывает позже как ``cache lookup failed for type <oid>``. Удаление
    схемы убирает и типы.
    """
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    eng.dispose()
    Base.metadata.create_all(eng)


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    _reset_schema(eng)
    yield eng
    _reset_schema(eng)
    eng.dispose()


@pytest.fixture()
def db(engine):
    """Сессия, привязанная к соединению с откатываемой транзакцией."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)

    # Делаем внешнюю транзакцию видимой для сессии, чтобы всё записанное
    # через ``db`` входило в неё и исчезало при откате.
    session.begin_nested()

    try:
        yield session
    finally:
        session.close()
        # Тест, проверявший IntegrityError, мог уже выполнить откат, который
        # деассоциирует транзакцию; защищаемся от двойного отката.
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture()
def client(engine):
    """TestClient, подключённый к одноразовой базе.

    В отличие от фикстуры ``db`` здесь всё коммитится по-настоящему, поэтому
    проверяется полный жизненный цикл запроса (включая lifespan, который
    вызывает ``create_tables()``). После теста таблицы пересоздаются.
    """
    from app.routers.notifications import send_email

    sent: list[tuple[str, str, str]] = []

    def fake_send_email(to: str, subject: str, body_html: str) -> None:
        sent.append((to, subject, body_html))

    original_send = send_email
    import app.routers.notifications as notifications_module

    notifications_module.send_email = fake_send_email

    _truncate_all(engine)

    testing = sessionmaker(bind=engine)()
    fastapi_app.dependency_overrides[get_db] = lambda: testing

    try:
        with TestClient(fastapi_app) as test_client:
            test_client.sent_emails = sent
            yield test_client
    finally:
        fastapi_app.dependency_overrides.clear()
        notifications_module.send_email = original_send
        testing.close()
        _truncate_all(engine)


@pytest.fixture()
def real_db(engine):
    """Сессия, которая коммитит по-настоящему.

    Нужна тестам, работающим с ``client``: их запросы выполняются в собственной
    сессии, поэтому всё, созданное через откатываемую фикстуру ``db``, было бы
    для них невидимо.
    """
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Фабрики данных
# ---------------------------------------------------------------------------


@pytest.fixture()
def make_user(db):
    def _make(email: str, role: UserRole, **kwargs) -> User:
        user = User(
            email=email,
            # Колонка users.login объявлена NOT NULL; фабрика подставляет
            # email, чтобы старые тесты не падали на ограничении.
            login=kwargs.pop("login", email),
            password_hash=kwargs.pop("password_hash", "x"),
            first_name=kwargs.pop("first_name", "Ivan"),
            last_name=kwargs.pop("last_name", "Ivanov"),
            role=role,
            is_active=kwargs.pop("is_active", True),
            is_verified=kwargs.pop("is_verified", True),
            **kwargs,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    return _make


@pytest.fixture()
def admin(make_user):
    # Намеренно не ADMIN_EMAIL: приложение создаёт своего админа в lifespan,
    # а фикстура client коммитит по-настоящему.
    return make_user("panel-admin@test.local", UserRole.admin)


@pytest.fixture()
def teacher(make_user):
    return make_user(
        "teacher@test.local", UserRole.teacher, teacher_subject="Math"
    )


@pytest.fixture()
def other_teacher(make_user):
    return make_user(
        "other@test.local", UserRole.teacher, teacher_subject="Physics"
    )


@pytest.fixture()
def student(make_user):
    return make_user("student@test.local", UserRole.student, grade="10A")


@pytest.fixture()
def student2(make_user):
    return make_user("student2@test.local", UserRole.student, grade="10A")


@pytest.fixture()
def parent(make_user):
    return make_user("parent@test.local", UserRole.parent)


@pytest.fixture()
def link_parent_child(db, parent, student):
    """Связывает фикстуру ``parent`` с фикстурой ``student``."""
    from app.models import ParentStudent

    def _link(parent_user=None, student_user=None):
        link = ParentStudent(
            parent_id=(parent_user or parent).id,
            student_id=(student_user or student).id,
        )
        db.add(link)
        db.commit()
        return link

    return _link


@pytest.fixture()
def klass(db, teacher):
    class_ = Class(name="10A", teacher_id=teacher.id, invite_code="AAAA111111")
    db.add(class_)
    db.commit()
    db.refresh(class_)
    return class_


@pytest.fixture()
def populated_class(klass, db, student, student2, parent):
    db.add(ClassMembership(student_id=student.id, class_id=klass.id))
    db.add(ClassMembership(student_id=student2.id, class_id=klass.id))
    db.add(ParentStudent(parent_id=parent.id, student_id=student.id))
    db.commit()
    return klass


@pytest.fixture()
def make_assignment(db, teacher, klass):
    def _make(**kwargs) -> Assignment:
        assignment = Assignment(
            title=kwargs.pop("title", "Homework"),
            subject=kwargs.pop("subject", "Math"),
            description=kwargs.pop("description", None),
            deadline=kwargs.pop("deadline", NOW + timedelta(days=2)),
            class_id=kwargs.pop("class_id", klass.id),
            teacher_id=kwargs.pop("teacher_id", teacher.id),
            priority=kwargs.pop("priority", None),
        )
        if assignment.priority is None:
            from app.models import TaskPriority

            assignment.priority = TaskPriority.medium
        db.add(assignment)
        db.commit()
        db.refresh(assignment)
        return assignment

    return _make


@pytest.fixture()
def make_task(db):
    def _make(student_id: int, **kwargs) -> Task:
        from app.models import TaskPriority, TaskStatus

        task = Task(
            student_id=student_id,
            title=kwargs.pop("title", "Task"),
            subject=kwargs.pop("subject", None),
            description=kwargs.pop("description", None),
            deadline=kwargs.pop("deadline", None),
            status=kwargs.pop("status", TaskStatus.pending),
            priority=kwargs.pop("priority", TaskPriority.medium),
            is_personal=kwargs.pop("is_personal", True),
            assignment_id=kwargs.pop("assignment_id", None),
            parent_task_id=kwargs.pop("parent_task_id", None),
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    return _make


@pytest.fixture()
def invite_code(db, admin):
    code = InviteCode(code="INVITE1234567890", created_by_id=admin.id)
    db.add(code)
    db.commit()
    db.refresh(code)
    return code