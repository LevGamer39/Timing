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
│   │   ├── models.py            # SQLAlchemy ORM, 13 моделей + ограничения
│   │   ├── dependencies.py      # get_current_user, require_role
│   │   ├── ai_text.py           # Чистая логика ИИ: парсинг, матчинг задач
│   │   └── routers/
│   │       ├── users.py            # Регистрация, auth, профиль, сброс пароля
│   │       ├── admin.py            # Пользователи, классы, инвайт-коды
│   │       ├── classes.py          # Классы учителя, дашборд, вступление
│   │       ├── assignments.py      # Задания учителя, прогресс
│   │       ├── tasks.py            # Задачи ученика, подзадачи
│   │       ├── grades.py           # Оценки, массовая выставка, сводки
│   │       ├── schedule.py         # Расписание
│   │       ├── ai.py               # Оркестрация Ollama (логика — в ai_text.py)
│   │       ├── notifications.py    # SMTP, APScheduler
│   │       └── yandex_auth.py      # OAuth Яндекса
│   ├── tests/                   # pytest, требует PostgreSQL
│   ├── Dockerfile
│   ├── pytest.ini
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── data/                    # остатки SQLite, см. «Известные проблемы»
│
├── frontend/
│   ├── *.html                   # Лендинг, login, register, verify,
│   │                            #   reset-password*, change-*, settings, 404
│   ├── css/                     # base, index, login, register, verify, reset-password
│   ├── js/                      # api.js, auth-guard.js, login.js, register.js,
│   │                            #   verify.js, mobile-nav.js
│   ├── student/                 # dashboard, tasks, calendar, progress, ai
│   │   ├── css/  js/            #   + settings.js
│   ├── teacher/                 # dashboard, classes, class, assignments,
│   │                            #   assignment-new, profile, settings, common
│   ├── admin/                   # dashboard, settings  (+ js/dashboard.js)
│   ├── parent/                  # ⚠ папки нет — см. «Известные проблемы»
│   └── favicon/
│
├── nginx/
│   ├── nginx.conf.template      # Шаблон: домен подставляется через envsubst
│   ├── docker-entrypoint.sh     # Подстановка APP_DOMAIN + самоподписанный cert
│   └── Dockerfile               # nginx:alpine + openssl
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

**Каталога `frontend/parent/` нет.** Роль `parent` полностью реализована в API
(`/api/grades/child/{id}`, `/api/tasks/child/{id}`, `/api/schedule/child/{id}`,
`/api/users/me/children`), но маршруты в `main.py` ссылаются на несуществующие
`parent/*.html` (теперь отдают 404, а не 500).



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
- `Task.subtasks` / `Task.parent_task` — рекурсивная связь через `parent_task_id`.
- `Class.class_` и `Assignment.class_` используют подчёркивание, потому что
  `class` — зарезервированное слово Python.
- Время в БД — UTC. Локальное время вычисляется только на отображающем слое
  (фронт). Не пиши `datetime.now()` без `timezone.utc`.
- `Schedule.weekday`: 0 = понедельник, 6 = воскресенье.
- `Schedule.start_time` / `Task.ai_suggested_time` — строки `"HH:MM"`, не `TIME`.

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
- **Миграций в проекте нет.** `create_all()` создаёт только отсутствующие
  таблицы, поэтому добавление колонки или ограничения требует ручного
  `ALTER TABLE` — предупреди об этом явно. Ограничения, объявленные в
  моделях, попадают в схему только при её создании.
- `create_tables()` объявлена в `database.py` дважды; используется вторая
  (с `_ensure_admin_user()`). Не добавляй третью — сначала почини дубликат.

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

### 1. Загрузка ресурсов

- Все страницы отдаёт FastAPI. Стили и скрипты — по абсолютному пути
  `/static/...`: `<script src="/static/student/js/tasks.js">`.
- nginx проксирует **весь** трафик на backend, включая `/static`. Отдельного
  `location /static` в `nginx.conf.template` нет — не добавляй.
- Стилизуй через существующие классы в `frontend/*/css/*.css` и
  `css/base.css`. Дизайн-система: тёмная «аврора» (фиолетовые градиенты
  `#654DFD` → `#2400FF`), акцент `#F1642F`, шрифты Inter (внутренние) и
  Nunito (публичные).
- UI-текст — на русском, язык интерфейса `ru-RU`.

### 2. api.js — единственная точка доступа к API

```javascript
// DO: все страницы используют хелперы api.js
const me = await get('/api/users/me');
const task = await post('/api/tasks', { title: '...' });

// DON'T: писать fetch() напрямую в страницах/скриптах порталов
```

Функции: `getToken`, `getUser`, `setAuth`, `logout`, `requireAuth(roles)`,
`get`, `post`, `patch`, `del`, `postForm`.

`request()` сам обрабатывает 401 (вызывает `logout`), парсит ответ и бросает
`Error` с `detail` из тела.

API-база — `window.location.origin`, а не жёсткий порт: страница и запросы
идут на один домен, всё проксирует nginx. Возврат в логин — на `/login`
(имя файла во `frontend/` — `login.html`, но маршрут именно такой).

### 3. LocalStorage

```javascript
// Ровно два ключа, фиксированы во всём проекте:
localStorage.getItem('token')   // JWT
localStorage.getItem('user')    // JSON: { id, role, first_name, ... }
```

`role` из `user` используется для клиентских редиректов и показа ролевых
блоков. Он не заменяет серверную проверку — `require_role` обязателен на бэке.

### 4. Роли и страницы

| Раздел | Пути | Роль |
|---|---|---|
| Публичные | `/`, `/login`, `/register`, `/verify`, `/reset-password*` | — |
| Ученик | `/dashboard`, `/student/tasks`, `/calendar`, `/progress`, `/ai` | student |
| Учитель | `/teacher/*` | teacher |
| Админ | `/admin/*` | admin |
| Общие | `/settings`, `/settings/change-email`, `/settings/change-password` | любая |

- Новую страницу **обязательно** зарегистрируй роутом в `main.py` через
  `serve("путь/к/file.html")` — иначе она недоступна.
- `requireAuth` вызывай **до** рендера данных, иначе возможен flash
  неавторизованного контента.
- На каждой странице подключай `/static/js/mobile-nav.js`.

### 5. Скрипты страниц

- Скрипт портала (`student/js/*.js`, `teacher/js/*.js`) подключается в конце
  `<body>` и стартует из `init()` или `DOMContentLoaded`.
- Экранируй всё, что пришло от пользователя: `escapeHtml()` есть в
  `tasks.js`, `progress.js`, `settings.js`, `ai.js` — переиспользуй паттерн,
  не вставляй innerHTML сырыми данными.
- Даты форматируй через `new Date(iso).toLocaleDateString('ru-RU', ...)`;
  на бэке всегда UTC.

### 6. Адаптивный дизайн

- Реализация интерфейса должна быть адаптивна хотя бы для 16x9 мониторов
- Использование фиксированных размеров допустимо, но не рекомендуется
- При реализации интерфейса по примеру от дизайнера (скриншот, фото) необходимо
примерно определять какой layout хотел показать дизайнер (например если на фото элементы расположены примерно по центру,
нужно использовать центрированный layout, чтобы реализация была адаптивной) 
- Избегай CSS кода внутри HTML файлов. Весь CSS должен быть в style.css или другом файле, если того требует пользователь

---

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

# 4. Прогон
$env:TEST_DATABASE_URL="postgresql://vector:<pw>@localhost:5432/vectordb_test"
pytest
```

Без `TEST_DATABASE_URL` conftest **падает сразу** — тихо подменять БД нельзя.

### Что есть

| Файл | Что покрывает |
|---|---|
| `tests/conftest.py` | Фикстуры: `engine` (session), `db` (откат транзакции), `client` (TestClient), `real_db` (реальные коммиты), фабрики |
| `test_ai_text.py` | Чистые хелперы ИИ, матчинг задач |
| `test_tasks.py` | Владение, запрет правки заданий учителя, каскады |
| `test_assignments.py` | Раскладка по ученикам, вычисляемые поля, каскадное удаление |
| `test_grades.py` | Авторизация, массовая выставка, сводки |
| `test_schedule.py` | Валидация `HH:MM`, границы `weekday` |
| `test_classes_admin.py` | Классы, дашборд учителя, админ-панель |
| `test_users.py` | HTTP-флоу регистрация→подтверждение→вход, auth-зависимости |
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
- Frontend: страницы `kebab-case.html`, скрипты и стили — `camelCase.js` /
  `kebab-case.css` (в проекте смешано: `api.js`, `auth-guard.js`,
  `mobile-nav.js`, но `reset-password.css`)
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

**Last Updated**: 2026-10-18 (рефакторинг роутеров: `app/config.py`,
`app/ai_text.py`, ограничения БД, pytest против PostgreSQL)
