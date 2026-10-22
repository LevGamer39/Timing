from sqlalchemy import (
    CheckConstraint, Column, DateTime, Enum as SAEnum, Float, ForeignKey,
    Integer, String, Text, UniqueConstraint, Boolean,
)
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func
import enum

Base = declarative_base()


class UserRole(str, enum.Enum):
    student = "student"
    teacher = "teacher"
    parent = "parent"
    admin = "admin"


class TaskStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    done = "done"
    overdue = "overdue"


class TaskPriority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class NotificationChannel(str, enum.Enum):
    email = "email"
    browser = "browser"
    telegram = "telegram"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    role = Column(SAEnum(UserRole), nullable=False)
    avatar_url = Column(String(500), nullable=True)
    grade = Column(String(20), nullable=True)  # для ученика: "10А"
    teacher_subject = Column(String(100), nullable=True)
    teacher_invite_code = Column(String(16), nullable=True)
    telegram_id = Column(String(100), nullable=True, unique=True)
    is_active = Column(Boolean, default=False)  # активируется после подтверждения email
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    yandex_id = Column(String(100), unique=True, nullable=True, index=True)
    max_user_id = Column(String(100), unique=True, nullable=True, index=True)  # ID пользователя в MAX
    # Настройки уведомлений
    notify_email = Column(Boolean, default=True)
    notify_browser = Column(Boolean, default=True)
    notify_telegram = Column(Boolean, default=False)
    notify_digest_time = Column(String(5), default="08:00")  # "HH:MM"

    classes_taught = relationship("Class", back_populates="teacher")
    class_memberships = relationship(
        "ClassMembership",
        back_populates="student",
        cascade="all, delete-orphan",
    )
    tasks = relationship(
        "Task",
        back_populates="student",
        foreign_keys="Task.student_id",
        cascade="all, delete-orphan",
    )
    grades = relationship(
        "Grade",
        back_populates="student",
        foreign_keys="Grade.student_id",
        cascade="all, delete-orphan",
    )
    ai_messages = relationship(
        "AIMessage", back_populates="user", cascade="all, delete-orphan"
    )
    notifications = relationship(
        "Notification", back_populates="user", cascade="all, delete-orphan"
    )
    schedule_entries = relationship(
        "Schedule", back_populates="student", cascade="all, delete-orphan"
    )


class InviteCode(Base):
    __tablename__ = "invite_codes"

    id = Column(Integer, primary_key=True)
    code = Column(String(16), unique=True, nullable=False, index=True)
    is_used = Column(Boolean, default=False)
    used_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    used_at = Column(DateTime(timezone=True), nullable=True)


class EmailVerification(Base):
    __tablename__ = "email_verifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    code = Column(String(6), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_used = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Class(Base):
    __tablename__ = "classes"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    teacher_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    subject = Column(String(100), nullable=True)
    invite_code = Column(String(12), unique=True, nullable=False)  # код для вступления в класс
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    teacher = relationship("User", back_populates="classes_taught")
    memberships = relationship(
        "ClassMembership", back_populates="class_", cascade="all, delete-orphan"
    )
    assignments = relationship(
        "Assignment", back_populates="class_", cascade="all, delete-orphan"
    )


class ClassMembership(Base):
    __tablename__ = "class_memberships"
    __table_args__ = (
        # Ученик состоит ровно в одном классе: в User.grade лежит одно
        # значение, и все запросы считают, что строка на ученика одна.
        # create_all() создаёт только отсутствующие таблицы, поэтому на базе,
        # созданной до этого ограничения, его не будет.
        UniqueConstraint("student_id", name="uq_class_memberships_student"),
    )

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=False)
    joined_at = Column(DateTime(timezone=True), server_default=func.now())

    student = relationship("User", back_populates="class_memberships")
    class_ = relationship("Class", back_populates="memberships")


class ParentStudent(Base):
    __tablename__ = "parent_students"

    id = Column(Integer, primary_key=True)
    parent_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    linked_at = Column(DateTime(timezone=True), server_default=func.now())


class Assignment(Base):
    """Задание, созданное учителем для класса или конкретных учеников."""
    __tablename__ = "assignments"

    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)
    subject = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    deadline = Column(DateTime(timezone=True), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=False)
    teacher_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    priority = Column(SAEnum(TaskPriority), default=TaskPriority.medium)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    class_ = relationship("Class", back_populates="assignments")
    tasks = relationship(
        "Task", back_populates="assignment", cascade="all, delete-orphan"
    )
    grades = relationship(
        "Grade", back_populates="assignment", cascade="all, delete-orphan"
    )


class Task(Base):
    """
    Задача ученика. Может быть создана:
    - учителем (assignment_id заполнен, is_personal=False)
    - самим учеником (assignment_id=None, is_personal=True)
    - ИИ как подзадача (parent_task_id заполнен)
    """
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    assignment_id = Column(Integer, ForeignKey("assignments.id"), nullable=True)
    parent_task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)

    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    subject = Column(String(100), nullable=True)
    deadline = Column(DateTime(timezone=True), nullable=True)
    status = Column(SAEnum(TaskStatus), default=TaskStatus.pending)
    priority = Column(SAEnum(TaskPriority), default=TaskPriority.medium)
    is_personal = Column(Boolean, default=False)
    ai_suggested_time = Column(String(5), nullable=True)  # "HH:MM" — оптимальное время по версии ИИ

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    student = relationship("User", back_populates="tasks", foreign_keys=[student_id])
    assignment = relationship("Assignment", back_populates="tasks")
    subtasks = relationship(
        "Task",
        back_populates="parent_task",
        cascade="all, delete-orphan",
    )
    parent_task = relationship("Task", back_populates="subtasks", remote_side="[Task.id]")


class Grade(Base):
    __tablename__ = "grades"
    __table_args__ = (
        # Одна оценка на ученика на задание. Строки с пустым assignment_id
        # под ограничение не попадают, поэтому отдельные оценки допустимы.
        UniqueConstraint(
            "student_id", "assignment_id", name="uq_grades_student_assignment"
        ),
        CheckConstraint("value >= 1 AND value <= 5", name="ck_grades_value_range"),
    )

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    assignment_id = Column(Integer, ForeignKey("assignments.id"), nullable=True)
    subject = Column(String(100), nullable=False)
    value = Column(Float, nullable=False)  # 1-5 или 100-балльная
    comment = Column(Text, nullable=True)
    graded_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    graded_at = Column(DateTime(timezone=True), server_default=func.now())

    student = relationship("User", back_populates="grades", foreign_keys=[student_id])
    assignment = relationship("Assignment", back_populates="grades")


class Schedule(Base):
    """Расписание уроков — как в дневнике."""
    __tablename__ = "schedule"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    subject = Column(String(100), nullable=False)
    teacher_name = Column(String(255), nullable=True)
    weekday = Column(Integer, nullable=False)  # 0=пн, 6=вс
    start_time = Column(String(5), nullable=False)  # "HH:MM"
    end_time = Column(String(5), nullable=False)
    room = Column(String(50), nullable=True)

    student = relationship("User", back_populates="schedule_entries")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    channel = Column(SAEnum(NotificationChannel), nullable=False)
    is_read = Column(Boolean, default=False)
    is_sent = Column(Boolean, default=False)
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="notifications")


class ParentLinkToken(Base):
    """6-значный код который ученик показывает родителю для привязки."""
    __tablename__ = "parent_link_tokens"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    code = Column(String(6), nullable=False, index=True)
    is_used = Column(Boolean, default=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token = Column(String(64), unique=True, nullable=False, index=True)  # обратная совместимость
    token_hash = Column(String(128), unique=True, nullable=True, index=True)  # хеш токена (безопасное хранение)
    is_used = Column(Boolean, default=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class EmailChangeToken(Base):
    __tablename__ = "email_change_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    new_email = Column(String(255), nullable=False)
    token = Column(String(64), unique=True, nullable=False, index=True)  # обратная совместимость
    token_hash = Column(String(128), unique=True, nullable=True, index=True)  # хеш токена (безопасное хранение)
    is_used = Column(Boolean, default=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AIMessage(Base):
    __tablename__ = "ai_messages"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(String(20), nullable=False)  # "user" | "assistant"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="ai_messages")


class TokenBlacklist(Base):
    """Список отозванных JWT (logout, смена пароля, скомпрометированный токен)."""

    __tablename__ = "token_blacklist"

    id = Column(Integer, primary_key=True)
    jti = Column(String(64), unique=True, nullable=False, index=True)  # ID токена
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User")
