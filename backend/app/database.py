"""Движок БД, фабрика сессий и первичное наполнение."""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import config
from app.models import Base, User, UserRole


def _engine_kwargs() -> dict:
    """Параметры движка: PostgreSQL получает пул и таймауты.

    Остальные диалекты (тесты) остаются без них.
    """
    url = config.build_database_url()
    if url.startswith("sqlite"):
        return {}
    return {"pool_pre_ping": True, "connect_args": {"connect_timeout": 10}}


engine = create_engine(config.build_database_url(), **_engine_kwargs())
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Зависимость FastAPI: сессия в рамках одного запроса.

    Откатывает транзакцию при ошибке явно: иначе роутер, упавший уже
    после ``db.add()``, оставит незафиксированные изменения, которые
    следующий flush в той же сессии может молча закоммитить.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _ensure_admin_user() -> None:
    """Гарантирует, что есть ровно одна рабочая учётная запись админа.

    Раньше админ создавался с ``is_active=False`` / ``is_verified=False``,
    а ветка «починки» присваивала ``False`` вместо ``True``, поэтому только
    что развёрнутый админ не мог войти в систему.
    """
    db = SessionLocal()
    try:
        admin = (
            db.query(User)
            .filter(User.role == UserRole.admin)
            .order_by(User.id.asc())
            .first()
        )
        if admin:
            changed = False
            # Выключенный админ не должен остаться заперт вне панели.
            if not admin.is_active:
                admin.is_active = True
                changed = True
            if not admin.is_verified:
                admin.is_verified = True
                changed = True
            if changed:
                db.commit()
                db.refresh(admin)
            return

        db.add(
            User(
                email=config.ADMIN_EMAIL,
                # Колонка login объявлена NOT NULL, а механизм логинов ещё
                # не готов: до него админ входит по email.
                login=config.ADMIN_EMAIL,
                password_hash=_bootstrap_password_hash(),
                first_name=config.ADMIN_FIRST_NAME,
                last_name=config.ADMIN_LAST_NAME,
                role=UserRole.admin,
                is_active=True,
                is_verified=True,
                notify_email=True,
                notify_browser=True,
                notify_digest_time="08:00",
            )
        )
        db.commit()
    finally:
        db.close()


def _bootstrap_password_hash() -> str:
    """Хеш пароля админа, создаваемого при развёртывании.

    Импорт ленивый: `app.routers.users` импортирует `app.database`,
    поэтому импорт на уровне модуля дал бы циклическую зависимость.
    """
    from app.routers.users import hash_password

    if not config.ADMIN_PASSWORD:
        raise RuntimeError(
            "ADMIN_PASSWORD is not set - cannot bootstrap the admin account"
        )
    return hash_password(config.ADMIN_PASSWORD)


def _apply_schema_updates() -> None:
    """Идемпотентно догоняет схему до текущих моделей.

    ``create_all`` создаёт только отсутствующие таблицы и не трогает уже
    существующие. Полноценных миграций в проекте нет, поэтому несколько
    безопасных DDL-изменений выполняются здесь при старте. Все операции
    идемпотентны, повторный запуск ничего не ломает.
    """
    if engine.dialect.name != "postgresql":
        return
    statements = (
        # Счётчик попыток доставки письма: без колонки backoff не рос.
        "ALTER TABLE notifications "
        "ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0",
        # Аватар приходит data URL на несколько килобайт, varchar(500) падал.
        "ALTER TABLE users ALTER COLUMN avatar_url TYPE TEXT",
        # Код привязки родителя стал восьмизначным.
        "ALTER TABLE parent_link_tokens "
        "ALTER COLUMN code TYPE VARCHAR(8)",
        # Логин появился ради регистрации из MAX-бота. На старой базе
        # колонки нет, а модель объявляет её NOT NULL: без бэкфилла любой
        # запрос к users падал бы с "column users.login does not exist".
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS login VARCHAR(255)",
        "UPDATE users SET login = COALESCE(email, 'user-' || id) "
        "WHERE login IS NULL",
        "ALTER TABLE users ALTER COLUMN login SET NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_login ON users (login)",
        # Аккаунт из бота создаётся без email и без пароля.
        "ALTER TABLE users ALTER COLUMN email DROP NOT NULL",
        "ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL",
        # Оценка за урок журнала: ссылка на journal_lessons. create_all
        # таблицу создаёт, но колонку в уже существующей grades — нет.
        "ALTER TABLE grades ADD COLUMN IF NOT EXISTS "
        "journal_lesson_id INTEGER REFERENCES journal_lessons(id) "
        "ON DELETE CASCADE",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_grades_student_lesson "
        "ON grades (student_id, journal_lesson_id) "
        "WHERE journal_lesson_id IS NOT NULL",
        # Несколько уроков одного предмета в день: дату дополняет slot.
        "ALTER TABLE journal_lessons "
        "ADD COLUMN IF NOT EXISTS slot INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE journal_lessons "
        "DROP CONSTRAINT IF EXISTS uq_journal_lesson_slot",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_journal_lesson_slot "
        "ON journal_lessons (class_id, subject, date, slot)",
        # «Н» привязан к конкретному уроку дня.
        "ALTER TABLE journal_absences "
        "ADD COLUMN IF NOT EXISTS slot INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE journal_absences "
        "DROP CONSTRAINT IF EXISTS uq_journal_absence_slot",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_journal_absence_slot "
        "ON journal_absences (student_id, subject, date, slot)",
    )
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        _migrate_teacher_subjects(connection)


def _migrate_teacher_subjects(connection) -> None:
    """Переносит единственный предмет учителя в таблицу ``teacher_subjects``.

    Раньше ``users.teacher_subject`` хранил одну строку. Значения переносятся,
    после чего колонка удаляется: два источника правды разошлись бы.
    """
    legacy = connection.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = 'users' "
            "AND column_name = 'teacher_subject'"
        )
    ).first()
    if not legacy:
        return
    connection.execute(
        text(
            "INSERT INTO teacher_subjects (user_id, subject) "
            "SELECT id, btrim(teacher_subject) FROM users "
            "WHERE teacher_subject IS NOT NULL "
            "AND btrim(teacher_subject) <> '' "
            "ON CONFLICT (user_id, subject) DO NOTHING"
        )
    )
    connection.execute(
        text("ALTER TABLE users DROP COLUMN teacher_subject")
    )


def _ensure_work_types() -> None:
    """Наполняет справочник видов работы значениями по умолчанию."""
    from app.models import JournalWorkType

    defaults = (
        ("lesson", "Работа на уроке", 1),
        ("home", "Домашняя работа", 1),
        ("self", "Самостоятельная", 2),
        ("test", "Тест", 2),
        ("kr", "Контрольная", 3),
    )
    db = SessionLocal()
    try:
        existing = {
            row[0] for row in db.query(JournalWorkType.code).all()
        }
        for code, name, weight in defaults:
            if code in existing:
                continue
            db.add(
                JournalWorkType(code=code, name=name, weight=weight)
            )
        db.commit()
    finally:
        db.close()


def create_tables() -> None:
    """Создаёт отсутствующие таблицы и гарантирует наличие админа.

    ``create_all`` не меняет существующие таблицы и колонки: правки схемы
    догоняются в :func:`_apply_schema_updates` или ручным DDL.
    """
    Base.metadata.create_all(bind=engine)
    _apply_schema_updates()
    _ensure_admin_user()
    _ensure_work_types()
