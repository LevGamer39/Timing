from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, Enum as SAEnum, Float,
    ForeignKey, Integer, String, Text, UniqueConstraint,
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


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    login = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True, index=True)
    # NULL у аккаунтов, зарегистрированных из MAX: пароль не запрашивается,
    # вход идёт по привязке max_user_id.
    password_hash = Column(String(255), nullable=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    role = Column(SAEnum(UserRole), nullable=False)
    avatar_url = Column(Text, nullable=True)
    grade = Column(String(20), nullable=True)  # для ученика: "10А"
    teacher_invite_code = Column(String(16), nullable=True)
    is_active = Column(Boolean, default=False)  # активируется после подтверждения email
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    yandex_id = Column(String(100), unique=True, nullable=True, index=True)
    max_user_id = Column(String(100), unique=True, nullable=True, index=True)  # ID пользователя в MAX
    # Настройки уведомлений
    notify_email = Column(Boolean, default=True)
    notify_browser = Column(Boolean, default=True)
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
    # Учитель ведёт несколько предметов: строки таблицы, а не одна колонка.
    subject_links = relationship(
        "TeacherSubject",
        back_populates="teacher",
        cascade="all, delete-orphan",
        order_by="TeacherSubject.id",
    )

    @property
    def teacher_subjects(self) -> list[str]:
        """Названия предметов учителя в порядке назначения."""
        return [link.subject for link in self.subject_links]


class TeacherSubject(Base):
    """Предмет, который ведёт учитель. У учителя их может быть несколько."""

    __tablename__ = "teacher_subjects"
    __table_args__ = (
        UniqueConstraint("user_id", "subject", name="uq_teacher_subjects"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject = Column(String(100), nullable=False)

    teacher = relationship("User", back_populates="subject_links")


class InviteCode(Base):
    __tablename__ = "invite_codes"

    id = Column(Integer, primary_key=True)
    code = Column(String(16), unique=True, nullable=False, index=True)
    is_used = Column(Boolean, default=False)
    used_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    used_at = Column(DateTime(timezone=True), nullable=True)


class MaxLoginCode(Base):
    """Одноразовый код входа в SPA, который выдаёт MAX-бот."""

    __tablename__ = "max_login_codes"

    id = Column(Integer, primary_key=True)
    code = Column(String(8), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_used = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


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
    journal_lesson_id = Column(
        Integer,
        ForeignKey("journal_lessons.id", ondelete="CASCADE"),
        nullable=True,
    )
    subject = Column(String(100), nullable=False)
    value = Column(Float, nullable=False)  # 1-5 или 100-балльная
    comment = Column(Text, nullable=True)
    graded_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    graded_at = Column(DateTime(timezone=True), server_default=func.now())

    student = relationship("User", back_populates="grades", foreign_keys=[student_id])
    assignment = relationship("Assignment", back_populates="grades")
    journal_lesson = relationship("JournalLesson", back_populates="grades")


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
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="notifications")


class ParentLinkToken(Base):
    """Код, который ученик показывает родителю для привязки.

    Код восьмизначный: шестизначный перебирался за время жизни, тем более
    что TTL раньше был сутки.
    """
    __tablename__ = "parent_link_tokens"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    code = Column(String(8), nullable=False, index=True)
    is_used = Column(Boolean, default=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token = Column(String(64), unique=True, nullable=False, index=True)
    is_used = Column(Boolean, default=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class EmailChangeToken(Base):
    __tablename__ = "email_change_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    new_email = Column(String(255), nullable=False)
    token = Column(String(64), unique=True, nullable=False, index=True)
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


class JournalWorkType(Base):
    """Вид работы в журнале: название и вес оценки."""

    __tablename__ = "journal_work_types"

    id = Column(Integer, primary_key=True)
    code = Column(String(40), unique=True, nullable=False, index=True)
    name = Column(String(80), nullable=False)
    weight = Column(Integer, nullable=False, default=1, server_default="1")


class JournalLesson(Base):
    """Урок журнала: дата, вид работы и домашнее задание."""

    __tablename__ = "journal_lessons"
    __table_args__ = (
        # У одного предмета бывает несколько уроков в день (две алгебры
        # подряд), поэтому дату дополняет порядковый slot.
        UniqueConstraint(
            "class_id", "subject", "date", "slot",
            name="uq_journal_lesson_slot",
        ),
    )

    id = Column(Integer, primary_key=True)
    class_id = Column(
        Integer,
        ForeignKey("classes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject = Column(String(100), nullable=False)
    date = Column(Date, nullable=False, index=True)
    slot = Column(Integer, nullable=False, default=0, server_default="0")
    type_code = Column(String(40), nullable=False, default="lesson")
    homework = Column(Text, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    grades = relationship(
        "Grade", back_populates="journal_lesson", cascade="all, delete-orphan"
    )


class JournalFinal(Base):
    """Итоговая оценка за период: журнал, колонка «Итог»."""

    __tablename__ = "journal_finals"
    __table_args__ = (
        UniqueConstraint(
            "student_id", "subject", "period", name="uq_journal_final_slot"
        ),
    )

    id = Column(Integer, primary_key=True)
    student_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subject = Column(String(100), nullable=False)
    period = Column(String(60), nullable=False)
    value = Column(Float, nullable=False)
    updated_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class JournalAbsence(Base):
    """«Н» в журнале: отсутствие на уроке конкретного предмета."""

    __tablename__ = "journal_absences"
    __table_args__ = (
        UniqueConstraint(
            "student_id", "subject", "date", "slot",
            name="uq_journal_absence_slot",
        ),
    )

    id = Column(Integer, primary_key=True)
    class_id = Column(
        Integer,
        ForeignKey("classes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    student_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subject = Column(String(100), nullable=False)
    date = Column(Date, nullable=False, index=True)
    slot = Column(Integer, nullable=False, default=0, server_default="0")
    marked_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AttendanceMark(Base):
    """Отметка об отсутствии ученика на конкретном уроке."""
    __tablename__ = "attendance_marks"
    __table_args__ = (
        UniqueConstraint(
            "student_id", "date", "lesson_index", name="uq_attendance_slot"
        ),
    )

    id = Column(Integer, primary_key=True)
    class_id = Column(
        Integer,
        ForeignKey("classes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    student_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    date = Column(Date, nullable=False, index=True)
    lesson_index = Column(Integer, nullable=False)  # 1 — первый урок дня
    subject = Column(String(100), nullable=True)
    marked_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ClassSchedule(Base):
    """Расписание класса: правится учителем или администратором."""

    __tablename__ = "class_schedule"
    __table_args__ = (
        UniqueConstraint(
            "class_id", "weekday", "lesson_index",
            name="uq_class_schedule_slot",
        ),
    )

    id = Column(Integer, primary_key=True)
    class_id = Column(
        Integer,
        ForeignKey("classes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    weekday = Column(Integer, nullable=False)  # 0=пн, 6=вс
    lesson_index = Column(Integer, nullable=False)  # 0 — первый урок дня
    subject = Column(String(100), nullable=False)
    teacher_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    teacher_name = Column(String(255), nullable=True)
    start_time = Column(String(5), nullable=False)
    end_time = Column(String(5), nullable=False)
    room = Column(String(50), nullable=True)


class SchoolSetting(Base):
    """Настройки школы: звонки, периоды, шкала — JSON одной строкой."""

    __tablename__ = "school_settings"

    id = Column(Integer, primary_key=True)
    payload = Column(Text, nullable=False, default="{}")
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Message(Base):
    """Внутренняя почта школы: письмо от одного пользователя другому."""

    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)
    sender_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    recipient_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subject = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    files = Column(Text, nullable=True)  # JSON-массив имён вложений
    is_read = Column(Boolean, default=False, nullable=False)
    deleted_by_sender = Column(Boolean, default=False, nullable=False)
    deleted_by_recipient = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    sender = relationship("User", foreign_keys=[sender_id])
    recipient = relationship("User", foreign_keys=[recipient_id])


class MailFile(Base):
    """Файл, приложенный к письму. Токен — случайная строка в ссылке."""

    __tablename__ = "mail_files"

    id = Column(Integer, primary_key=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    path = Column(String(500), nullable=False)
    size = Column(Integer, nullable=False, default=0)
    uploader_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
