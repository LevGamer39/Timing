"""Движок БД, фабрика сессий и первичное наполнение."""
from sqlalchemy import create_engine
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
                password_hash=_bootstrap_password_hash(),
                first_name=config.ADMIN_FIRST_NAME,
                last_name=config.ADMIN_LAST_NAME,
                role=UserRole.admin,
                is_active=True,
                is_verified=True,
                notify_email=True,
                notify_browser=True,
                notify_telegram=False,
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


def create_tables() -> None:
    """Создаёт отсутствующие таблицы и гарантирует наличие админа.

    ``create_all`` не меняет существующие таблицы и колонки: правки схемы
    по-прежнему требуют ручного DDL — миграций в проекте нет.
    """
    Base.metadata.create_all(bind=engine)
    _ensure_admin_user()