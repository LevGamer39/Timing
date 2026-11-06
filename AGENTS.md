# AGENTS.md - Coding Guidelines & Best Practices

> Документ синхронизирован с кодом. Если правило ниже противоречит реальности —
> правь код, а не документ, и обнови этот файл.

## Project Structure

```
VectorVK/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── config.py            # ЕДИНЫЙ источник конфигурации (парсинг .env)
│   │   ├── main.py              # Точка входа: API-роутеры + раздача страниц
│   │   ├── database.py          # Engine (PostgreSQL), сессии, create_tables
│   │   ├── models.py            # SQLAlchemy ORM + ограничения
│   │   ├── dependencies.py      # get_current_user, require_role
│   │   ├── ai_text.py           # Чистая логика ИИ: парсинг, матчинг задач
│   │   └── routers/
│   │       ├── users.py            # Регистрация, auth, профиль, сброс пароля
│   │       ├── admin.py            # Пользователи, классы, инвайт-коды
│   │       ├── migration.py        # Перенос учеников: экспорт/импорт JSON
│   │       ├── classes.py          # Классы учителя, дашборд, вступление
│   │       ├── assignments.py      # Задания учителя, прогресс
│   │       ├── tasks.py            # Задачи ученика, подзадачи
│   │       ├── grades.py           # Оценки, массовая выставка, сводки
│   │       ├── schedule.py         # Расписание класса и ученика
│   │       ├── journal.py          # Уроки, оценки, «Н», итоговые
│   │       ├── attendance.py       # Отметки посещаемости
│   │       ├── messages.py         # Внутренняя почта школы
│   │       ├── school.py           # Настройки школы (звонки, периоды, шкала)
│   │       ├── ai.py               # ИИ: инструменты Ollama, чаты, сжатие истории
│   │       ├── notifications.py    # SMTP, APScheduler
│   │       └── yandex_auth.py      # OAuth Яндекса
│   ├── tests/                   # pytest, требует PostgreSQL
│   ├── Dockerfile
│   ├── pytest.ini
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── data/                    # остатки SQLite, см. «Известные проблемы»
│
├── frontend/                    # SPA без сборки: index.html + css/ js/ html/
│   ├── index.html               # единственная оболочка, навигация по #/...
│   ├── css/                     # app, palettes + <экран>.css
│   ├── js/                      # main, store, data, ui, <экран>.js
│   │   ├── shared/              # логика нескольких экранов
│   │   └── vendor/qr/           # генератор QR (MIT)
│   ├── html/                    # <экран>.html + manifest.json
│   ├── schemas/                 # JSON Schema переноса учеников (/static/schemas/)
│   ├── robots.txt
│   ├── *.cmd                    # запуск/остановка http.server на Windows
│   └── README.md                # единственная документация фронтенда
│
├── nginx/
│   ├── nginx.conf.template      # Шаблон: домен подставляется через envsubst
│   ├── docker-entrypoint.sh     # Подстановка APP_DOMAIN + самоподписанный cert
│   └── Dockerfile               # nginx:alpine + openssl
├── bot/                         # MAX-бот (переезд из плоского в пакетную структуру)
│   ├── bot.py                   # Точка входа: Bot + Dispatcher, polling или webhook
│   ├── config.py                # Настройки из общего .env
│   ├── core/                    # Общие зависимости
│   │   ├── context.py           # AppContext, общий для роутеров
│   │   ├── filters.py           # Фильтр callback-payload
│   │   ├── security.py          # HMAC-хеш и подпись запросов
│   │   ├── sessions.py          # JWT в памяти
│   │   ├── state.py             # JSON-состояние
│   │   └── ui.py                # Ответы, автор события, проверка привязки
│   ├── services/                # Бизнес-логика бота
│   │   ├── backend.py           # HTTP-клиент к бэкенду
│   │   ├── registration.py      # Диалог регистрации (логин → код → имя)
│   │   ├── ai.py                # Qwen через Ollama + regex-fallback
│   │   ├── ai_chat.py           # ИИ-чат
│   │   ├── homework.py          # Парсер ДЗ в чатах класса
│   │   ├── reminders.py         # Напоминания и недельная сводка
│   │   ├── tasks.py             # Создание заданий
│   │   ├── views.py             # Готовые ответы для команд и меню
│   │   └── voice.py             # faster-whisper: голосовое -> текст
│   ├── routers/                 # Обработчики команд и кнопок
│   │   ├── ai_chat.py           # /ai
│   │   ├── common.py            # /start, /help, /cancel, события бота
│   │   ├── registration.py      # Кнопки подтверждения имени
│   │   ├── grades.py            # /grades, /summary
│   │   ├── homework.py          # /hw и кнопки карточек
│   │   ├── menu.py              # callback-кнопки меню
│   │   ├── messages.py          # Свободный текст и голосовые
│   │   ├── schedule.py          # /schedule
│   │   └── tasks.py             # /today, /add, /done, кнопки заданий
│   ├── keyboards/               # Inline-клавиатуры maxapi
│   ├── texts/                   # Тексты и форматирование
│   ├── data/                    # runtime-состояние (volume)
│   ├── dockerfile
│   └── requirements.txt
│
├── docker-compose.yml           # backend, ollama, nginx, certbot, db
├── .env.example
└── AGENTS.md
```

**Конфигурация — только в `app/config.py`.** Значения парсятся и валидируются
один раз: `_int` для чисел, `_bool` для флагов. Модули делают
`from app import config` и **не читают `os.environ` напрямую**.

> Это не косметика. `ACCESS_TOKEN_EXPIRE_MINUTES` раньше читался строкой и
> попадал в `timedelta()`, из-за чего `POST /api/users/login` всегда
> возвращал 500. Покрыто тестом `test_users.py::test_token_expiry_is_numeric`.

**Платформа:** `requirements.txt` помечает `uvloop`, `httptools`,
`watchfiles`, `websockets` как `sys_platform != "win32"` — они не собираются на
Windows. Локальная разработка на Windows работает.

**Фронтенд — SPA без сборки.** Роль `parent` реализована в API
(`/api/grades/child/{id}`, `/api/tasks/child/{id}`, `/api/schedule/child/{id}`,
`/api/users/me/children`) и в интерфейсе. Связка SPA с API сделана:
`js/api.js` — сеть, `js/server.js` — раскладка ответов в структуры экранов,
`js/store.js` — единая точка чтения/записи. Без токена (`vector-token`)
работает демо-режим на `localStorage`; демо сохранено намеренно.



---

## Backend Guidelines

### 1. Общие принципы

- **Async/Await**: эндпоинты объявлены `async def`, но работают с БД
  **синхронно** через `Depends(get_db)`. Не добавляй `await` к сессии SQLAlchemy
  и не переходи на `AsyncSession` без отдельной задачи по миграции.
- **Dependency Injection**: `db: Session = Depends(get_db)`.
- **Роли**: `Depends(require_role(UserRole.teacher))` — единственный способ
  ограничения доступа. Порядок: сначала `require_role`, потом `db`.
- **Пароли**: `hash_password()` из `users.py`. Не импортируй passlib напрямую.
- **Токены**: `create_access_token(user_id)` — единая точка выдачи JWT.

### 2. Модели (models.py)

```python
# DO: str-enum для статусов — значения коллег по enum совпадают
class TaskStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    done = "done"
    overdue = "overdue"

# DO: внешние ключи с явными индексами
email = Column(String(255), unique=True, nullable=False, index=True)

# DO: DateTime(timezone=True) везде, значения — UTC
created_at = Column(DateTime(timezone=True), server_default=func.now())

# DO: самоссылающаяся связь для подзадач
parent_task = relationship("Task", back_populates="subtasks", remote_side="[Task.id]")
```

Тонкости, которые легко сломать:

- `User.tasks` и `User.grades` требуют `foreign_keys=`, потому что у `User`
  несколько путей к `Task`/`Grade`. Не добавляй связь, не указав `foreign_keys`.
- `User.login` — идентификатор входа: у веб-аккаунта он равен email, у
  аккаунта из MAX — придуманный логин. `email` и `password_hash` nullable:
  аккаунт из бота создаётся без них и входит только по `max_user_id`.
- `Task.subtasks` / `Task.parent_task` — рекурсивная связь через `parent_task_id`.
- `Class.class_` и `Assignment.class_` используют подчёркивание, потому что
  `class` — зарезервированное слово Python.
- Время в БД — UTC. Локальное время вычисляется только на отображающем слое
  (фронт). Не пиши `datetime.now()` без `timezone.utc`.
- `Schedule.weekday`: 0 = понедельник, 6 = воскресенье.
- `Schedule.start_time` / `Task.ai_suggested_time` — строки `"HH:MM"`, не `TIME`.
- Журнал и быт школы лежат в отдельных таблицах: `journal_lessons`,
  `journal_absences`, `journal_finals`, `journal_work_types`;
  `attendance_marks`; `class_schedule`; `messages` + `mail_files`;
  `school_settings`. Оценка журнала — обычный `Grade` с
  `journal_lesson_id`, поэтому она сразу видна ученику.
- У учителя несколько предметов: строки `teacher_subjects` (названия, не
  ключи фронта), свойство `User.teacher_subjects`. Колонки
  `users.teacher_subject` больше нет — не возвращай её.
- В `journal_lessons` и `journal_absences` дату дополняет `slot`: у одного
  предмета бывает несколько уроков в день. Уникальность —
  `(class_id, subject, date, slot)` и `(student_id, subject, date, slot)`.
- `Message.files` — JSON-список объектов `{token, name, size}`. Сами файлы
  лежат в `backend/data/uploads/mail/`, доступ — по токену `mail_files`.
- ИИ-чаты: `ai_chats` + `ai_messages.chat_id`. `AIChat.summary` и
  `summary_upto_id` — сжатая старая часть диалога; всё, что новее этого id,
  уходит модели как есть. История одного чата не растёт в промпте бесконечно.

### 3. Роутеры (routers/*.py)

```python
router = APIRouter(prefix="/api/tasks", tags=["tasks"])

@router.post("/{task_id}/complete", response_model=TaskResponse)
def complete_task(task_id: int, db: Session = Depends(get_db),
                 current_user: User = Depends(require_role(UserRole.student))):
    ...
    return task
```

- Все префиксы — `/api/...`. Страницы отдаются из `main.py`, не из роутеров.
- Эндпоинты **синхронные** (`def`), даже если в `main.py` страницы — `async def`.
- Схемы Pydantic объявляются в том же файле роутера над эндпоинтами.
- Ошибки — `raise HTTPException(status_code=..., detail="текст на английском")`.
  Фронт показывает `detail` пользователю как есть.
- Родительские эндпоинты именуются `/child/{student_id}` и живут рядом со
  «своими» (`/my`), а не в отдельном роутере.

### 4. Схема БД и миграции

- `create_all()` создаёт **только отсутствующие таблицы**. Он не добавляет
  колонки и не меняет типы.
- **Фреймворка миграций в проекте нет.** `create_all()` создаёт только
  отсутствующие таблицы. Несколько идемпотентных DDL-правок
  (`notifications.attempts`, `users.avatar_url` → `TEXT`,
  `parent_link_tokens.code` → `VARCHAR(8)`, `users.login` с бэкфиллом из
  `email`, `email`/`password_hash` → nullable, `ai_messages.chat_id` с
  переносом старой переписки в чат «История») применяются в
  `_apply_schema_updates()` при старте; всё остальное по-прежнему требует
  ручного `ALTER TABLE` — предупреди об этом явно.
- `create_tables()` объявлена в `database.py` один раз (с `_ensure_admin_user()`).
  Не добавляй вторую — сначала почини существующую.

### 5. Конфигурация

`APP_DOMAIN` в `.env` — единственное место, где задан домен. Он попадает в
nginx через `envsubst` в `nginx/docker-entrypoint.sh` и в backend через
`APP_URL` (из него собирается CORS). Правя домен в конфигах вручную, создаёшь
расхождение с `.env`.

Что важно знать при правках:

- `nginx/nginx.conf.template` — шаблон, а не рабочий конфиг. Правь шаблон.
- `envsubst '${APP_DOMAIN} ${SSL_CERT} ${SSL_KEY}'` — список переменных
  обязателен. Пустой список означает «подставить всё», и nginx теряет `$host`.
- Перед `envsubst` переменные нужно `export`: `envsubst` читает окружение,
  а не shell-переменные. Без экспорта подстановка пуста и nginx падает с
  `invalid number of arguments in "ssl_certificate"`.
- Самоподписанный сертификат — осознанный костыль на первый запуск: без него
  контейнер не поднимается вообще. Не выноси его в основной конфиг.
- В `.env` значения пишутся как `КЛЮЧ=значение` без пробелов вокруг `=` и без
  BOM: compose не примет ни то, ни другое.

### 6. Значения по умолчанию

- Секреты — только из `.env`. Никаких значений по умолчанию для
  `SECRET_KEY`, `DB_PASS`, `ADMIN_PASSWORD`.
- `DB_HOST` в Docker переопределяется на `vector_db` в `docker-compose.yml`.
  Не хардкодь имя хоста в коде.
- `docker-compose.yml` требует `DB_PASS` (`:?db password must be set`) —
  падает без него намеренно.

---

## Frontend Guidelines

Фронтенд — статический SPA без сборки: `frontend/index.html` и три каталога
`css/`, `js/`, `html/`. Навигация по хешу (`#/home`, `#/diary`,
`#/journal/...`); сервер отдаёт одну оболочку, а страницы — функции экранов.

### 1. Загрузка ресурсов

- Стили и скрипты лежат рядом с `index.html` и подключаются относительно:
  `css/app.css`, `js/main.js`. Backend монтирует `frontend/` в корень, nginx
  проксирует весь трафик — отдельный `location` не нужен.
- Старые серверные адреса (`/login`, `/dashboard`, `/teacher/*`) отдают ту же
  оболочку: `serve()` в `main.py` подставляет `index.html` для отсутствующих
  страниц.
- Стилизуй через `css/app.css` и `<экран>.css`. Дизайн-система: две темы
  (`data-theme`), акценты (`data-accent`), шрифт Inter.
- UI-текст — на русском, язык интерфейса `ru-RU`.

### 2. Данные

- Единственная точка работы с данными — `js/store.js`; сеть — только через
  `js/api.js`. Ответы API раскладываются в структуры экранов в `js/server.js`.
- Демо-данные — в `js/data.js`, `js/teacher-data.js`, `js/admin-data.js`.
- Без токена (`localStorage['vector-token']`) работает демо-режим; с токеном
  `store.bootstrap()` поднимает сессию через `/api/users/me`.
- В серверном режиме журнал, почта, посещаемость и расписание класса живут
  в `server.session` (память), а не в `vector-demo-v1`: `save()` их не пишет.
- Прямой `fetch` в экранах не пиши.
- Демо хранится в `localStorage` под ключом `vector-demo-v1`.

### 3. Экраны и шаблоны

- Экран — `xxxView({ params, path, go, rerender })`, возвращает
  `{ html, mount }` (плюс `captureDraft`/`restoreDraft`, если нужно).
- HTML-фрагменты — `html/<экран>.html` с комментариями
  `<!-- fragment:имя -->`; список файлов — `html/manifest.json`.
- Подстановки — `fragment('экран', 'имя', [значения])`, `{{0}}`, `{{1}}`.
- Любой пользовательский текст — через `esc()`.
- Новый экран: `html/x.html`, `css/x.css`, `js/x.js`, запись в
  `html/manifest.json`, `ROUTES` в `js/main.js` и `<link>` в `index.html`.

### 4. Роли

| Роль | Меню |
|---|---|
| student / parent | главная, дневник, оценки, задачи, почта |
| teacher | главная, журнал, мой класс, почта |
| admin | главная, классы, учителя, расписание, пропуски, почта |

Клиентская роль — только для интерфейса; серверная проверка `require_role`
обязательна.

### 5. Стиль кода

- ES-модули, `camelCase`, без сборщиков и npm.
- Только vanilla JS; из CDN — только Google Fonts (Inter).
- Даты — `new Date(iso).toLocaleDateString('ru-RU', ...)`; на бэке UTC.

---

## Известные проблемы

Не исправлены. Не докладывай о них как о новых, но и не считай их нормой.

| # | Проблема | Файл |
|---|---|---|
| 10 | `pydantic-settings` и `PyYAML` установлены, но не используются | `backend/requirements.txt` |
| 11 | Фреймворка миграций нет: `create_all()` не меняет созданные таблицы; часть колонок догоняется в `_apply_schema_updates()` | `backend/app/database.py` |
| 12 | Планировщик рассчитан на один процесс uvicorn | `backend/app/routers/notifications.py` |
| 13 | Свой SMTP невозможен: домен на `no-ip.com`, нет контроля над TXT- и PTR-записями | `docker-compose.yml`, DNS |

Про 12: `docker-compose` запускает один `uvicorn`, поэтому джобы не
дублируются. При нескольких воркерах каждый поднимет свой APScheduler —
сначала поставь `ENABLE_SCHEDULER=false` лишним, либо вынеси джобы в
отдельный процесс.

## Testing Guidelines

Тесты — **pytest против настоящего PostgreSQL**. SQLite намеренно не
поддерживается: под ним не проверяются нативные enum-типы, реальное
поведение FK и типы агрегатов, поэтому тесты могли бы проходить здесь и
падать в проде.

### Запуск

```powershell
# 1. Поднять БД из композа
docker compose up -d db

# 2. Создать отдельную базу под тесты (схему тесты пересоздают сами)
docker exec vector_db psql -U vector -d vectordb `
    -c "CREATE DATABASE vectordb_test;"

# 3. Зависимости
pip install -r requirements-dev.txt

# 4. Прогон: скрипт сам берёт DB_* из ../.env и подставляет 127.0.0.1
cd backend
python run_tests.py
```

Вручную (переменная живёт только в этом окне):

```powershell
$env:TEST_DATABASE_URL="postgresql://vector:<pw>@127.0.0.1:5432/vectordb_test"
pytest
```

`run_tests.py` берёт `.env` только когда переменной нет. Если она осталась от
прошлой попытки с другим паролем — `Remove-Item Env:TEST_DATABASE_URL`.

Без `TEST_DATABASE_URL` conftest **падает сразу** — тихо подменять БД нельзя.

На Windows берите `127.0.0.1`, а не `localhost`: Docker Desktop слушает IPv4,
а `localhost` сначала пробует IPv6 и подключение зависает без ошибки. Если
`connect_timeout` не указан, conftest добавляет его сам (5 секунд).

### Что есть

| Файл | Что покрывает |
|---|---|
| `tests/conftest.py` | Фикстуры: `engine` (session), `db` (откат транзакции), `client` (TestClient), `real_db` (реальные коммиты), фабрики |
| `test_ai_text.py` | Чистые хелперы ИИ, матчинг задач, план на неделю, часовые пояса |
| `test_ai.py` | Чаты и их владелец, история, сжатие, инструменты, подтверждения, фантомные действия |
| `test_tasks.py` | Владение, запрет правки заданий учителя, каскады |
| `test_assignments.py` | Раскладка по ученикам, вычисляемые поля, каскадное удаление |
| `test_grades.py` | Авторизация, массовая выставка, сводки |
| `test_schedule.py` | Валидация `HH:MM`, границы `weekday` |
| `test_classes_admin.py` | Классы, дашборд учителя, админ-панель |
| `test_admin_migration.py` | Экспорт/импорт учеников JSON: идемпотентность, перенос с сохранением оценок |
| `test_users.py` | HTTP-флоу регистрация→подтверждение→вход, auth-зависимости |
| `test_messages.py` | Получатели почты: неподтверждённые видны, заблокированные — нет |
| `test_db_constraints.py` | Ограничения БД реально отвергают плохие данные |

### Правила

- Тестируй функции роутов напрямую (`router.some_fn(body, current_user, db)`) —
  так не нужен HTTP-слой. `TestClient` нужен там, где важен жизненный цикл
  (lifespan, отправка писем, 404-страницы).
- Фикстура `db` откатывает транзакцию. Если тесту нужен `client` (он работает
  в своей сессии) — создавай данные через `real_db`, иначе запрос их не увидит.
- Email в схемах должен быть на нормальном домене: `email-validator`
  отклоняет зарезервированные TLD вроде `.local`.
- Домен `@test.local` годится для сырых строк, но не для `EmailStr`.

---

## Development Workflow

```bash
# Локально
cd backend
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Ollama для ИИ
ollama serve && ollama pull qwen2.5:7b

# Docker
docker compose up -d --build
docker exec vector_ollama ollama pull qwen2.5:7b
```

Для навигации по связям используй граф знаний: `graphify query "<вопрос>"`
(см. `graphify-out/`).

---

## Style Guide

### Python

- PEP 8, линия 79 символов
- Type hints на всех параметрах и возвращаемых значениях
- Docstrings на публичных функциях и классах
- Сортировка импортов через isort

### JavaScript

- ES6+, стрелочные функции, `camelCase`
- Только vanilla JS — **кроме** Chart.js и Google Fonts, которые приходят с CDN
- Никаких сборщиков, бандлеров и npm-зависимостей

---

## File Naming

- Backend: `snake_case` (`main.py`, `database.py`)
- Frontend: экраны `kebab-case` (`teacher-home.html`, `my-class.css`,
  `teacher-home.js`), общие модули — `camelCase` (`store.js`, `ui.js`,
  `templates.js`)
- Роутеры — по домену: `users`, `admin`, `classes`, `assignments`, `tasks`,
  `grades`, `schedule`, `ai`, `notifications`, `yandex_auth`

---

## Environment Variables (.env.example)

```env
# PostgreSQL. DATABASE_URL перекрывает все четыре значения ниже.
# DATABASE_URL=postgresql://user:pw@localhost:5432/vectordb
DB_USER=user
DB_PASS=your-database-password
DB_HOST=localhost
DB_PORT=5432
DB_NAME=vectordb

ADMIN_EMAIL=admin@vector.local
ADMIN_PASSWORD=changeme
ADMIN_FIRST_NAME=Admin
ADMIN_LAST_NAME=Vector

SECRET_KEY=your-secret-key-here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=10080

SMTP_HOST=smtp.yandex.ru
SMTP_PORT=587
SMTP_USER=your@yandex.ru
SMTP_PASSWORD=your-app-password
EMAIL_FROM=your@yandex.ru

OLLAMA_URL=http://ollama:11434
OLLAMA_MODEL=qwen2.5:7b

# Пояс по умолчанию; браузер присылает свой и он важнее. В БД время в UTC.
TIMEZONE=Europe/Moscow

# Модель не трогаем в коде: при переезде на мощное железо правится только .env.
AI_NUM_CTX=8192
AI_TIMEOUT=90
AI_TEMPERATURE=0.3
AI_NUM_PREDICT=700
AI_KEEP_ALIVE=30m

# Базовый контекст и бюджет истории (символы). Остальное модель берёт
# инструментами; при переполнении история сжимается в резюме.
AI_CONTEXT_GRADES=3
AI_CONTEXT_TASKS=30
AI_CONTEXT_DAYS=7
AI_HISTORY_CHARS=8000
AI_COMPACT_KEEP=20
AI_SUMMARY_CHARS=2000
AI_CHAT_TITLE_CHARS=60
AI_PROMPT_EXTRA=

APP_URL=https://vector.myftp.org
DEBUG=false

# Выключает фоновые джобы (тесты, одноразовые команды)
ENABLE_SCHEDULER=true

YANDEX_CLIENT_ID=your-yandex-client-id
YANDEX_CLIENT_SECRET=your-yandex-client-secret
YANDEX_LOGIN_CALLBACK=https://vector.myftp.org/api/auth/yandex/callback
YANDEX_LINK_CALLBACK=https://vector.myftp.org/api/auth/yandex/confirm-link
```

Переменной `DB_PATH` больше нет: код её никогда не читал, это остаток от SQLite.

Порты SMTP: `465` включает неявный TLS и включает `SMTP_SSL`; для `587`
используется `SMTP` + `STARTTLS`. Это решается автоматически по `SMTP_PORT`.

---

**Last Updated**: 2026-11-01 (журнал, почта с вложениями, посещаемость,
расписание, настройки школы, уведомления и ИИ подключены к API; админка
управляет классами и учителями; `/link` привязывает веб-аккаунт к MAX)
