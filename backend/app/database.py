"""Движок БД, фабрика сессий и первичное наполнение."""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import config
from app.models import Base


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
    from app.models import User, UserRole

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


def _add_enum_values() -> None:
    """Досыпает новые значения в PG-enum вне транзакции.

    ``ALTER TYPE ... ADD VALUE`` нельзя выполнять внутри транзакционного
    блока (до PostgreSQL 12 — совсем, позже — нельзя использовать новое
    значение в той же транзакции). Поэтому отдельное autocommit-соединение.
    """
    values = (("taskstatus", "accepted"), ("notificationchannel", "max"))
    with engine.connect() as raw:
        connection = raw.execution_options(isolation_level="AUTOCOMMIT")
        for type_name, value in values:
            present = connection.execute(
                text("SELECT 1 FROM pg_type WHERE typname = :name"),
                {"name": type_name},
            ).first()
            if not present:
                continue
            connection.execute(
                text(
                    f"ALTER TYPE {type_name} "
                    f"ADD VALUE IF NOT EXISTS '{value}'"
                )
            )


def _apply_schema_updates() -> None:
    """Идемпотентно догоняет схему до текущих моделей.

    ``create_all`` создаёт только отсутствующие таблицы и не трогает уже
    существующие. Полноценных миграций в проекте нет, поэтому несколько
    безопасных DDL-изменений выполняются здесь при старте. Все операции
    идемпотентны, повторный запуск ничего не ломает.
    """
    if engine.dialect.name != "postgresql":
        return
    _add_enum_values()
    statements = (
        # Приём задания учителем: статус accepted и время приёма.
        "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS accepted_at TIMESTAMPTZ",
        # MAX — основной канал уведомлений, выключается в настройках.
        "ALTER TABLE users "
        "ADD COLUMN IF NOT EXISTS notify_max BOOLEAN NOT NULL DEFAULT TRUE",
        # Ключ идемпотентности событий (приём, недельная сводка).
        "ALTER TABLE notifications "
        "ADD COLUMN IF NOT EXISTS dedupe_key VARCHAR(120)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_notifications_user_key "
        "ON notifications (user_id, dedupe_key) "
        "WHERE dedupe_key IS NOT NULL",
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
        # Несколько чатов в ИИ: ai_chats создаёт create_all, а ссылку в уже
        # существующей ai_messages добавляет только этот DDL.
        "ALTER TABLE ai_messages ADD COLUMN IF NOT EXISTS chat_id INTEGER "
        "REFERENCES ai_chats(id) ON DELETE CASCADE",
        "CREATE INDEX IF NOT EXISTS ix_ai_messages_chat_id "
        "ON ai_messages (chat_id)",
        # Вся старая переписка собирается в один архивный чат на пользователя.
        # Один запрос с RETURNING, чтобы не привязать сообщения к чужому
        # чату с тем же названием при повторном запуске.
        "WITH created AS ("
        " INSERT INTO ai_chats (user_id, title, created_at, updated_at)"
        " SELECT user_id, 'История', MIN(created_at), MAX(created_at)"
        " FROM ai_messages WHERE chat_id IS NULL GROUP BY user_id"
        " RETURNING id, user_id"
        ") UPDATE ai_messages m SET chat_id = created.id"
        " FROM created WHERE m.user_id = created.user_id"
        " AND m.chat_id IS NULL",
    )
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        connection.execute(text("ALTER TABLE assignments ADD COLUMN IF NOT EXISTS files TEXT"))
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


def _ensure_demo_data() -> None:
    """Создаёт демо-аккаунты и данные для тестирования функционала.
    
    Идемпотентно: проверяет наличие по login перед вставкой.
    Демо-аккаунты с фиксированными паролями для ознакомления с системой.
    Также создаёт класс, учеников, задания, оценки и почту.
    """
    from datetime import datetime, timedelta, timezone
    from app.models import (
        User, UserRole, Class, ClassMembership, Assignment, Task, Grade,
        Message, TaskStatus, TaskPriority, TeacherSubject, InviteCode,
        ParentStudent, ClassSchedule
    )
    from app.routers.users import hash_password
    import secrets

    demo_accounts = (
        # (login, email, password, first_name, last_name, role, grade, is_active)
        ("demo.admin", "admin@demo.timing", "demo2024", "Елена Андреевна", "Кузнецова", UserRole.admin, None, True),
        ("demo.teacher", "teacher@demo.timing", "demo2024", "Мария Петровна", "Иванова", UserRole.teacher, None, True),
        ("demo.student", "student@demo.timing", "demo2024", "Артём", "Белов", UserRole.student, "8А", True),
        ("demo.parent", "parent@demo.timing", "demo2024", "Анна Викторовна", "Белова", UserRole.parent, None, True),
        ("demo.student2", "student2@demo.timing", "demo2024", "Полина", "Егорова", UserRole.student, "8А", True),
        ("demo.student3", "student3@demo.timing", "demo2024", "Даниил", "Громов", UserRole.student, "8А", True),
        ("demo.wait1", "wait1@demo.timing", None, "Дмитрий", "Смирнов", UserRole.student, "8А", False),
        ("demo.wait2", "wait2@demo.timing", None, "Алина", "Козлова", UserRole.student, "8Б", False),
        ("demo.teacher2", "teacher2@demo.timing", None, "Ольга", "Смирнова", UserRole.teacher, None, False),
        ("demo.teacher3", "teacher3@demo.timing", None, "Игорь", "Павлов", UserRole.teacher, None, False),
    )
    
    db = SessionLocal()
    try:
        existing_logins = {
            row[0] for row in db.query(User.login).filter(
                User.login.in_([acc[0] for acc in demo_accounts])
            ).all()
        }
        
        users = {}
        for login, email, password, first_name, last_name, role, grade, is_active in demo_accounts:
            if login in existing_logins:
                users[login] = db.query(User).filter(User.login == login).first()
                continue
            
            user = User(
                login=login,
                email=email,
                password_hash=hash_password(password) if password else None,
                first_name=first_name,
                last_name=last_name,
                role=role,
                grade=grade,
                is_active=is_active,
                is_verified=is_active,
                notify_email=True,
                notify_browser=True,
                notify_max=True,
                notify_digest_time="08:00",
            )
            db.add(user)
            users[login] = user
        
        db.flush()
        
        # Предметы учителей
        teacher = users.get("demo.teacher")
        if teacher:
            for subj in ("Алгебра", "Геометрия"):
                if not db.query(TeacherSubject).filter(
                    TeacherSubject.user_id == teacher.id,
                    TeacherSubject.subject == subj,
                ).first():
                    db.add(TeacherSubject(user_id=teacher.id, subject=subj))
        for key, subj in (("demo.teacher2", "Русский язык"), ("demo.teacher3", "Физика")):
            t = users.get(key)
            if t and not db.query(TeacherSubject).filter(
                TeacherSubject.user_id == t.id
            ).first():
                db.add(TeacherSubject(user_id=t.id, subject=subj))
        
        # Классы: 8А, 8Б, 4Б
        def _ensure_class(name, teacher_obj, subject):
            cls = db.query(Class).filter(Class.name == name).first()
            if not cls and teacher_obj:
                cls = Class(
                    name=name,
                    teacher_id=teacher_obj.id,
                    subject=subject,
                    invite_code=secrets.token_urlsafe(9)[:12],
                )
                db.add(cls)
                db.flush()
            return cls

        class_8a = _ensure_class("8А", teacher, "Математика")
        class_8b = _ensure_class("8Б", teacher, "Математика")
        class_4b = _ensure_class("4Б", users.get("demo.teacher2"), "Начальная школа")
        
        # Зачисление учеников в классы (в т.ч. не вошедших — они видны в ростере)
        membership = {
            "demo.student": class_8a, "demo.student2": class_8a,
            "demo.student3": class_8a, "demo.wait1": class_8a,
            "demo.wait2": class_8b,
        }
        for login, cls in membership.items():
            student = users.get(login)
            if student and cls and not db.query(ClassMembership).filter(
                ClassMembership.student_id == student.id
            ).first():
                db.add(ClassMembership(student_id=student.id, class_id=cls.id))
        
        db.flush()

        # Привязка родителя к ученику: без неё родитель падает в home (student=null)
        parent = users.get("demo.parent")
        child = users.get("demo.student")
        if parent and child and not db.query(ParentStudent).filter(
            ParentStudent.parent_id == parent.id,
            ParentStudent.student_id == child.id,
        ).first():
            db.add(ParentStudent(parent_id=parent.id, student_id=child.id))

        # Расписание 8А: без него дневник показывает «Выходной», а страница оценок — пусто
        if class_8a:
            plan = (
                (0, 0, "Алгебра", "08:30", "09:10", "214"),
                (0, 1, "Русский язык", "09:20", "10:00", "305"),
                (1, 0, "Геометрия", "08:30", "09:10", "214"),
                (1, 1, "Физика", "09:20", "10:00", "208"),
                (2, 0, "Алгебра", "08:30", "09:10", "214"),
                (2, 1, "Русский язык", "09:20", "10:00", "305"),
                (3, 0, "Физика", "08:30", "09:10", "208"),
                (3, 1, "Геометрия", "09:20", "10:00", "214"),
                (4, 0, "Алгебра", "08:30", "09:10", "214"),
            )
            for weekday, idx, subject, start, end, room in plan:
                if not db.query(ClassSchedule).filter(
                    ClassSchedule.class_id == class_8a.id,
                    ClassSchedule.weekday == weekday,
                    ClassSchedule.lesson_index == idx,
                ).first():
                    db.add(ClassSchedule(
                        class_id=class_8a.id,
                        weekday=weekday,
                        lesson_index=idx,
                        subject=subject,
                        teacher_id=teacher.id if teacher else None,
                        teacher_name="Иванова Мария Петровна" if teacher else None,
                        start_time=start,
                        end_time=end,
                        room=room,
                    ))
        
        db.flush()
        
        # Инвайт-коды для не вошедших учителей (их выдаёт админ)
        admin = users.get("demo.admin")
        if admin:
            for key in ("demo.teacher2", "demo.teacher3"):
                t = users.get(key)
                if t and not db.query(InviteCode).filter(
                    InviteCode.created_by_id == admin.id,
                    InviteCode.is_used.is_(False),
                ).filter(
                    # грубая идемпотентность: хотя бы один свободный код на админа
                    InviteCode.code.like("DEMO%"),
                ).first():
                    pass
            # Создаём два кода, если их ещё нет
            if db.query(InviteCode).filter(InviteCode.code.in_(["DEMO-TEACHER-2", "DEMO-TEACHER-3"])).count() == 0:
                db.add(InviteCode(code="DEMO-TEACHER-2", created_by_id=admin.id))
                db.add(InviteCode(code="DEMO-TEACHER-3", created_by_id=admin.id))
        
        db.flush()
        now = datetime.now(timezone.utc)
        if class_8a and teacher:
            assignment = db.query(Assignment).filter(
                Assignment.class_id == class_8a.id,
                Assignment.title == "Контрольная работа по алгебре"
            ).first()
            
            if not assignment:
                assignment = Assignment(
                    title="Контрольная работа по алгебре",
                    subject="Алгебра",
                    description="Повторить §11-13, подготовиться к контрольной",
                    deadline=now + timedelta(days=3),
                    class_id=class_8a.id,
                    teacher_id=teacher.id,
                    priority=TaskPriority.high
                )
                db.add(assignment)
                db.flush()
            
            # Задачи учеников по заданию
            for login in ["demo.student", "demo.student2", "demo.student3"]:
                student = users.get(login)
                if student and not db.query(Task).filter(
                    Task.student_id == student.id,
                    Task.assignment_id == assignment.id
                ).first():
                    status = TaskStatus.done if login == "demo.student" else TaskStatus.in_progress
                    db.add(Task(
                        student_id=student.id,
                        assignment_id=assignment.id,
                        title=assignment.title,
                        description=assignment.description,
                        subject=assignment.subject,
                        deadline=assignment.deadline,
                        status=status,
                        priority=assignment.priority,
                        completed_at=now - timedelta(hours=2) if status == TaskStatus.done else None
                    ))
        
        db.flush()
        
        # Оценки: value 1–5, graded_by обязателен
        student = users.get("demo.student")
        if student and teacher and not db.query(Grade).filter(Grade.student_id == student.id).first():
            grades_data = [
                ("Алгебра", 5, "Самостоятельная"),
                ("Алгебра", 4, "Контрольная"),
                ("Геометрия", 4, "Работа на уроке"),
                ("Русский язык", 3, "Работа на уроке"),
            ]
            for subject, value, comment in grades_data:
                db.add(Grade(
                    student_id=student.id,
                    subject=subject,
                    value=value,
                    comment=comment,
                    graded_by_id=teacher.id,
                ))
        
        # Почта: sender_id/recipient_id, is_read
        admin = users.get("demo.admin")
        if admin and student and not db.query(Message).filter(
            Message.sender_id == admin.id
        ).first():
            db.add(Message(
                sender_id=admin.id,
                recipient_id=student.id,
                subject="Приветствие в системе",
                body="Добро пожаловать в Timing! Это демо-аккаунт для ознакомления с функционалом.",
                is_read=False,
            ))
        # Дополнительно: письмо от учителя ученику
        if teacher and student and not db.query(Message).filter(
            Message.sender_id == teacher.id,
            Message.recipient_id == student.id,
        ).first():
            db.add(Message(
                sender_id=teacher.id,
                recipient_id=student.id,
                subject="Контрольная по алгебре в пятницу",
                body="Артём, не забудь повторить §11-13 к контрольной. Вопросы можно задать в чате.",
                is_read=False,
            ))
        
        db.commit()
    except Exception:
        db.rollback()
        raise
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
    _ensure_demo_data()
