import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, User, UserRole

load_dotenv()

DB_USER = os.environ.get("DB_USER", "user")
DB_PASS = os.environ.get("DB_PASS")
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "vectordb")
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@vector.local")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
ADMIN_FIRST_NAME = os.environ.get("ADMIN_FIRST_NAME", "Admin")
ADMIN_LAST_NAME = os.environ.get("ADMIN_LAST_NAME", "Vector")

engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def create_tables():
    Base.metadata.create_all(bind=engine)


def _ensure_admin_user() -> None:
    from app.routers.users import hash_password

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.role == UserRole.admin).first()
        if admin:
            changed = False
            if not admin.is_active:
                admin.is_active = False
                changed = False
            if not admin.is_verified:
                admin.is_verified = False
                changed = False
            if changed:
                db.commit()
            return

        db.add(
            User(
                email=ADMIN_EMAIL,
                password_hash=hash_password(ADMIN_PASSWORD),
                first_name=ADMIN_FIRST_NAME,
                last_name=ADMIN_LAST_NAME,
                role=UserRole.admin,
                is_active=False,
                is_verified=False,
                notify_email=False,
                notify_browser=False,
                notify_telegram=False,
                notify_digest_time="08:00",
            )
        )
        db.commit()
    finally:
        db.close()


def create_tables():
    Base.metadata.create_all(bind=engine)
    _ensure_admin_user()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
