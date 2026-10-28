# Vector — Умный планировщик для школьников

Веб-приложение для управления учебными задачами с ИИ-ассистентом. Личный кабинет
ученика, учителя, родителя и администратора. Разработано для WATA.

---

## Стек

### Backend

| Технология | Версия | Назначение |
|---|---|---|
| **FastAPI** | 0.115.0 | REST API, роутизация, валидация |
| **PostgreSQL** + **psycopg 3** | 3.3.6 | База данных |
| **SQLAlchemy** | 2.1.5 | ORM |
| **python-jose** | 3.3.0 | JWT-авторизация |
| **passlib** + **bcrypt** | 1.7.4 / 4.0.1 | Хэширование паролей |
| **APScheduler** | 3.10.4 | Фоновые задачи |
| **httpx** | 0.27.2 | Асинхронные запросы к Ollama |
| **smtplib** (stdlib) | — | Отправка писем через SMTP |
| **python-dotenv** | 1.2.4 | Загрузка `.env` |
| **pydantic** | 2.13.5 | Схемы запросов/ответов |
| **uvicorn** | 0.30.6 | ASGI-сервер |

### AI

| Технология | Назначение |
|---|---|
| **Ollama** | Локальный LLM-сервер (Docker-контейнер) |
| **Qwen2.5:7b** | Модель: планирование, приоритеты, разбивка задач |

### Frontend

| Технология | Назначение |
|---|---|
| **HTML / CSS / JS** | Без фреймворков и сборки |
| **Chart.js** (CDN) | График динамики успеваемости |
| **Inter / Nunito** (Google Fonts CDN) | Типографика |
| **Canvas API** | Кольцо прогресса |
| **LocalStorage** | JWT-токен и профиль сессии |

> Статику раздаёт сам FastAPI: каталог `frontend/` смонтирован в корень
> (и в `/static` для совместимости). Фронтенд — SPA на `frontend/index.html`,
> старые адреса отдают ту же оболочку.

---

## Архитектура

```
Интернет
    │
    ▼ :80 / :443
┌──────────────────────────────────┐
│  Docker: nginx      vector_nginx │  ← TLS, редирект HTTP→HTTPS, gzip
│  nginx.conf.template + envsubst  │
└────────────┬─────────────────────┘
             │ proxy_pass http://backend:8000   (весь трафик, включая /static)
┌────────────▼─────────────────────┐
│  Docker: backend   vector_backend│  ← FastAPI + раздача frontend/
└──────┬─────────────────────┬─────┘
       │                     │
┌──────▼──────────┐   ┌──────▼─────────────────┐
│ Docker: ollama  │   │ Docker: db  vector_db   │
│ vector_ollama   │   │ postgres:latest         │
│ Qwen2.5:7b      │   │ ./pg_data → /var/lib/… │
└─────────────────┘   └─────────────────────────┘

┌──────────────────┐
│ Docker: certbot  │  ← certbot renew каждые 12ч
└──────────────────┘

```

Все сервисы — в сети `vector_net` (bridge). Наружу открыты только `80` и `443`
через nginx, плюс `5432` у БД (bind на хост — для отладки).

### Домен настраивается в одном месте

`APP_DOMAIN` в `.env` — единственное упоминание домена в проекте:

```
.env: APP_DOMAIN=vector.myftp.org
        │
        ├─ docker-compose → environment nginx/certbot: APP_DOMAIN
        ├─ nginx/docker-entrypoint.sh → envsubst в nginx.conf.template
        └─ backend: CORS берёт прод-адрес из APP_URL
```

nginx не читает конфиг из репозитория как есть: `docker-entrypoint.sh`
подставляет `APP_DOMAIN` и пути к сертификату в `nginx.conf.template` через
`envsubst`. Подстановка идёт **явным списком переменных** — если оставить
envsubst без списка, он сожрёт и `$host`, и `$remote_addr`, которые nginx
обязан видеть как есть.

Сертификата на свежей машине нет, а nginx с несуществующим путём к сертификату
просто не стартует. Поэтому entrypoint при отсутствии сертификата генерирует
**самоподписанный** — сайт поднимается сразу, дальше certbot заменит его
настоящим. Статус виден в логах:

```bash
docker compose logs nginx
```

Выпуск настоящего сертификата. Команду сначала стоит прогнать с `--dry-run`:
Let's Encrypt использует тестовый сервер, лимиты на реальные попытки не
тратятся.

```bash
docker compose run --rm --entrypoint certbot certbot certonly \
  --webroot -w /var/www/certbot \
  -d vector.myftp.org \
  --agree-tos --no-eff-email --email твой@адрес \
  --dry-run

# если прошло - повторить без --dry-run
```

`--entrypoint certbot` обязателен: у сервиса certbot в compose переопределён
`entrypoint` на цикл `certbot renew`, и без этой подмены переданные аргументы
просто проигнорируются.

#### Проброс портов обязателен

ACME-проверка ходит снаружи на **порт 80**. Если он не проброшен на машину,
Let's Encrypt получает `Connection refused` и сертификат не выдаётся:

```
109.120.131.130: Fetching http://vector.myftp.org/.well-known/acme-challenge/...
Connection refused
```

В роутере пробрось `80 → 192.168.255.15:80` и `443 → 192.168.255.15:443`.
Пока порт 80 закрыт, сайт работает только с самоподписанным сертификатом
(браузер покажет предупреждение) — это осознанный режим для локальной работы.

**Важно:** `frontend/` монтируется в backend как `/frontend:ro`. Путь
`frontend_path` в `main.py` вычисляется как `../../frontend` от `app/`, поэтому
в контейнере это ровно `/frontend`, а локально — корень репозитория. Логика одна
и та же в обоих режимах.

---

## Структура проекта

```
VectorVK/
├── backend/
│   ├── app/
│   │   ├── config.py            # Единая конфигурация: парсинг и валидация .env
│   │   ├── main.py              # Точка входа: API-роутеры + раздача страниц
│   │   ├── database.py          # Engine (PostgreSQL), сессии, create_tables
│   │   ├── models.py            # 13 моделей + ограничения целостности
│   │   ├── dependencies.py      # get_current_user, require_role
│   │   ├── ai_text.py           # Чистая логика ИИ: парсинг, матчинг задач
│   │   └── routers/             # users, admin, classes, assignments, tasks,
│   │                            #   grades, schedule, ai, notifications,
│   │                            #   yandex_auth
│   ├── tests/                   # pytest (нужен PostgreSQL)
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
│   │   ├── shared/              # общая логика экранов
│   │   └── vendor/qr/           # генератор QR (MIT)
│   ├── html/                    # <экран>.html + manifest.json
│   └── README.md                # документация фронтенда
│
├── nginx/
│   ├── nginx.conf.template      # Шаблон: домен подставляется через envsubst
│   ├── docker-entrypoint.sh     # Подстановка APP_DOMAIN + самоподписанный cert
│   └── Dockerfile               # nginx:alpine + openssl
├── docker-compose.yml           # backend, ollama, nginx, certbot, db
├── .env.example
└── README.md
```

## Роли пользователей

| Роль | Возможности | Интерфейс |
|---|---|---|
| **Ученик** | Задачи, календарь, ИИ-чат, прогресс, оценки, расписание, профиль | `/student/*` ✅ |
| **Учитель** | Классы, создание заданий, прогресс учеников, выставление оценок | `/teacher/*` ✅ |
| **Родитель** | Просмотр задач, оценок и расписания ребёнка (только чтение) | SPA: роль `parent` ✅ |
| **Администратор** | Инвайт-коды, классы, пользователи, сводка по школе | `/admin/*` ✅ |

API родителя работает полностью (`/api/grades/child/{id}`,
`/api/tasks/child/{id}`, `/api/schedule/child/{id}`, `/api/users/me/children`).
В SPA роль `parent` есть; связка с сервером пока не сделана — данные демо.

---

## API

Префиксы роутеров:

| Роутер | Префикс |
|---|---|
| users | `/api/users` |
| admin | `/api/admin` |
| classes | `/api/classes` |
| assignments | `/api/assignments` |
| tasks | `/api/tasks` |
| grades | `/api/grades` |
| schedule | `/api/schedule` |
| ai | `/api/ai` |
| notifications | `/api/notifications` |
| yandex_auth | `/api/auth/yandex` |

Полный список — в Swagger: `http://localhost:8000/docs` (в проде недоступен).

Ответы возвращаются «как есть» (`{"detail": "..."}` при ошибке), без обёртки
`{"data": ...}`.

---

## Тесты

pytest против **настоящего PostgreSQL** — SQLite намеренно не поддерживается,
потому что под ним не проверяются нативные enum-типы, реальное поведение
внешних ключей и типы агрегатов.

```powershell
docker compose up -d db
docker exec vector_db psql -U vector -d vectordb `
    -c "CREATE DATABASE vectordb_test;"

cd backend
pip install -r requirements-dev.txt

$env:TEST_DATABASE_URL="postgresql://vector:<pw>@localhost:5432/vectordb_test"
pytest
```

193 теста: `test_ai_text`, `test_tasks`, `test_assignments`, `test_grades`,
`test_schedule`, `test_classes_admin`, `test_users`, `test_db_constraints`.
Без переменной `TEST_DATABASE_URL` conftest падает сразу — молча подменить БД
нельзя.

---

## Локальный запуск

### 1. Зависимости

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Нужна **PostgreSQL** (SQLite не поддерживается).

### 2. Окружение

```bash
cp .env.example .env
```

Заполнить как минимум:

```env
DB_PASS=<пароль БД>          # обязателен — docker-compose падает без него
SECRET_KEY=<случайная строка>
ADMIN_PASSWORD=<пароль админа>
APP_URL=https://vector.myftp.org
```

Локально БД берётся из `DB_HOST=localhost`, `DB_PORT=5432`, `DB_NAME=vectordb`,
`DB_USER=user` (значения по умолчанию в `config.py`). В контейнере `DB_HOST`
переопределяется на `vector_db` через `docker-compose.yml`.

### 3. Ollama (для ИИ)

```bash
ollama serve
ollama pull qwen2.5:7b
```

Без работающего Ollama приложение стартует, но все `/api/ai/*` вернут ошибку.

В Docker модель по умолчанию **не** скачивается — это ~4.7 ГБ лишнего
ожидания при каждом запуске на чистой машине. Есть два пути:

```env
# .env — включить автоустановку
OLLAMA_AUTO_PULL=true
```

С этим флагом сервис `ollama-init` при старте докачает `OLLAMA_MODEL`, если её
нет, и завершится. Скачивание идёт в фоне и **не** блокирует запуск сайта:
ИИ заработает через несколько минут. Если модель уже есть, шаг пропускается.

Либо вручную:

```bash
docker exec vector_ollama ollama pull qwen2.5:7b
```

### 4. Backend

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

При старте автоматически создаются таблицы (`create_all`) и админ-пользователь
из `ADMIN_*`. Документация: `http://localhost:8000/docs`.

---

## Деплой

### Домен

Домен проекта — **vector.myftp.org**. Он прописан в четырёх местах:

| Где | Что именно |
|---|---|
| `nginx/nginx.conf.template` | `server_name` и путь `ssl_certificate` |
| `backend/app/main.py` | список `CORS_ORIGINS` |
| `.env` | `APP_URL`, `YANDEX_LOGIN_CALLBACK`, `YANDEX_LINK_CALLBACK` |
| консоль Яндекса OAuth | два callback URL |

Последние два должны совпадать **посимвольно** с `.env`, иначе OAuth вернёт
`redirect_uri mismatch`:

```
https://vector.myftp.org/api/auth/yandex/callback
https://vector.myftp.org/api/auth/yandex/confirm-link
```

### Требования

```
Yandex Compute Cloud (Ubuntu 22.04)
  vCPU: 4+   RAM: 8+ GB   (Qwen2.5:7b ~6 GB RAM)
  Диск: 40+ GB (модель ~5 GB)
Yandex DNS: A-запись vector.myftp.org → IP виртуальной машины
Статический внешний IP
```

### Первый запуск

```bash
# 1. Docker
curl -fsSL https://get.docker.com | sh

# 2. Репозиторий и .env
git clone <repo> && cd VectorVK
cp .env.example .env && nano .env

# 3. SSL-сертификат (первый раз — standalone, до старта nginx)
docker run --rm \
  -v ./nginx/certbot/conf:/etc/letsencrypt \
  -v ./nginx/certbot/www:/var/www/certbot \
  -p 80:80 certbot/certbot certonly --standalone \
  -d vector.myftp.org \
  --email you@example.com --agree-tos --no-eff-email

# 4. Скачать модель
docker compose up -d ollama
docker exec vector_ollama ollama pull qwen2.5:7b

# 5. Запустить всё
docker compose up -d --build
```

Домен задаётся **один раз** — в `APP_DOMAIN` в `.env`. Оттуда он попадает в
nginx (через `envsubst` в `docker-entrypoint.sh`) и в CORS бэкенда (через
`APP_URL`). Если меняешь домен, поправь оба значения в `.env` и зарегистрируй
новые адреса в консоли Яндекса OAuth.

---

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `APP_DOMAIN` | — | Домен развёртывания. **Обязателен** в compose: из него nginx берёт `server_name` и пути к сертификату |
| `DB_USER` | `user` | Пользователь PostgreSQL |
| `DB_PASS` | — | Пароль PostgreSQL (**обязателен** в compose) |
| `DB_HOST` | `localhost` | Хост БД (`vector_db` в Docker) |
| `DB_PORT` | `5432` | Порт БД |
| `DB_NAME` | `vectordb` | Имя базы |
| `ADMIN_EMAIL` | `admin@vector.local` | Email автосоздаваемого админа |
| `ADMIN_PASSWORD` | — | Его пароль |
| `ADMIN_FIRST_NAME` / `ADMIN_LAST_NAME` | `Admin` / `Vector` | Имя админа |
| `SECRET_KEY` | — | Ключ подписи JWT (**обязателен**) |
| `ALGORITHM` | `HS256` | Алгоритм JWT |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `10080` | Время жизни токена (7 дней) |
| `SMTP_HOST` | `smtp.yandex.ru` | SMTP-сервер |
| `SMTP_PORT` | `587` | Порт SMTP (465 — неявный TLS, тоже работает) |
| `SMTP_USER` / `SMTP_PASSWORD` | — | Учётные данные SMTP (нужен **пароль приложения**) |
| `EMAIL_FROM` | — | Отправитель писем |
| `SMTP_STARTTLS` | `SMTP_PORT == 587` | STARTTLS; для нестандартных портов задать явно |
| `SMTP_TIMEOUT` | `15` | Таймаут SMTP, секунд |
| `OLLAMA_URL` | — | Адрес Ollama |
| `OLLAMA_MODEL` | — | Модель |
| `APP_URL` | — | Базовый URL для ссылок в письмах |
| `DEBUG` | `false` | Флаг отладки |
| `DATABASE_URL` | — | Перекрывает `DB_USER/PASS/HOST/PORT/NAME` целиком |
| `ENABLE_SCHEDULER` | `true` | Фоновые джобы (выключаются в тестах) |
| `YANDEX_CLIENT_ID` / `YANDEX_CLIENT_SECRET` | — | OAuth Яндекса |
| `YANDEX_LOGIN_CALLBACK` / `YANDEX_LINK_CALLBACK` | из `APP_URL` | Redirect URI Яндекса |
| `MAX_BOT_TOKEN` | — | Токен MAX-бота; им же подписываются его запросы к API (`X-Bot-Signature`) |
| `BOT_USER_ID_SALT` | из `MAX_BOT_TOKEN` | Соль хеширования MAX user_id. Смена соли инвалидирует привязки |

---

## Система авторизации

- Регистрация → подтверждение email **6-значным кодом, 15 минут**
- Регистрация из **MAX-бота**: логин + инвайт-код, без email и пароля.
  Код класса делает пользователя учеником и зачисляет в класс, одноразовый
  код из `invite_codes` — учителем
- Учитель регистрируется **только по инвайт-коду** (16 символов, выдаёт админ).
  Код списывается при подтверждении email
- JWT (`HS256`), время жизни 7 дней, хранится в `localStorage.token`
- Сброс пароля по ссылке на email (токен живёт **1 час**)
- Смена email — по ссылке (токен живёт **1 час**)
- Привязка родителя к ребёнку — **6-значный код** в профиле ученика (**24 часа**)
- Вход в класс ученика — по инвайт-коду класса (**12 символов**)
- OAuth Яндекса: `/api/auth/yandex/login-url` → `/callback`

---

## ИИ-ассистент

Модель **Qwen2.5:7b** через Ollama. Системный промпт запрещает решать задания и
писать тексты — только планирование и организацию.

- Чат с контекстом текущих задач и расписания
- Анализ нагрузки на неделю
- Разбивка задачи на подзадачи (сохраняются в БД как `Task` с `parent_task_id`)
- Определение приоритетов и подсказка времени работы

---

## База данных

**PostgreSQL**. 15 таблиц: `users`, `invite_codes`, `email_verifications`,
`classes`, `class_memberships`, `parent_students`, `parent_link_tokens`,
`assignments`, `tasks`, `grades`, `schedule`, `notifications`,
`email_change_tokens`, `password_reset_tokens`, `ai_messages`.

Таблицы создаются автоматически через `Base.metadata.create_all()` при старте.
**Фреймворка миграций нет.** Важно: `create_all` создаёт только отсутствующие
таблицы и никогда не меняет существующие — добавление колонки требует ручного
`ALTER TABLE`.

### Ограничения целостности

Объявлены прямо в моделях, поэтому попадают в схему при её создании:

| Ограничение | Смысл |
|---|---|
| `uq_grades_student_assignment` | Одна оценка на ученика на задание. Строки с `NULL` не ограничены — отдельные оценки работают |
| `uq_class_memberships_student` | Ученик состоит ровно в одном классе (это же предполагает `User.grade`) |
| `ck_grades_value_range` | Оценка в диапазоне 1..5 — как и проверяет API |

**Ограничение на существующей базе.** `create_all()` создаёт только
отсутствующие таблицы и никогда не меняет уже созданные, поэтому на базе,
поднятой до появления этих ограничений, их не будет — проверка уйдёт на
уровень приложения, а не базы. Чтобы добавить их вручную, выполни
`ALTER TABLE` по одному ограничению на таблицу `grades` и
`class_memberships` — или пересоздай схему на пустой базе.

### Каскады удаления

Связи в `models.py` помечены `cascade="all, delete-orphan"`, поэтому удаление
задания, класса, задачи или ученика уносит с собой зависимые строки. Без этого
коммит падал бы с нарушением внешнего ключа.

# удалить и пересоздать таблицы вручную, затем:
docker compose restart backend
```

`create_all` не удаляет и не меняет существующие колонки — только создаёт
отсутствующие таблицы. Поэтому изменение типа или добавление `NOT NULL`-колонки
на живой базе требует ручного `ALTER`.

---

## Отправка писем

Письма отправляет сам Python (`smtplib` из стандартной библиотеки) —
отдельный почтовый сервер в проекте не нужен. Нужен только доступный
SMTP-сервер.

### Что уже сделано в коде

| Механизм | Где |
|---|---|
| Отправка с выбором транспорта по порту | `app/config.py` → `starttls_for_port()` |
| Санитизация заголовков (CRLF в теме не ломает письмо) | `notifications.py` → `_header()` |
| Экранирование HTML в письмах | `notifications.py` → `_escape()` |
| Четыре шаблона | код, сброса, просрочки, уведомления |
| Очередь уведомлений | джоб раз в минуту |
| Пометка просроченных | джоб раз в 10 минут |
| **Ограниченные повторы** | backoff 5→15→60→240→720 мин, отказ через 24 ч |

Повторы — важная деталь: раньше при ошибке доставки письмо оставалось
в очереди **навсегда** и перебиралось каждую минуту, так что битый адрес
вечно крутился бы в очереди. Теперь после N попыток интервал растёт,
а через 24 часа письмо списывается.

### Проверка, что SMTP работает

```bash
cd backend

# Посмотреть конфигурацию, ничего не отправляя
python test_mail.py --check

# Отправить реальное письмо
python test_mail.py someone@example.com
python test_mail.py someone@example.com --template overdue
```

Шаблоны: `plain`, `verify`, `reset`, `overdue`.

### Почта через Яндекс

Отправка идёт через сторонний SMTP `smtp.yandex.ru`. Транспорт выбирается по
порту, `STARTTLS` включается автоматически (таблица ниже, в «Уведомления»).

Нужен **пароль приложения**, а не пароль от аккаунта — обычный пароль Яндекс
для SMTP не принимает: <https://id.yandex.ru/security/apppasswords>.

```env
SMTP_HOST=smtp.yandex.ru
SMTP_PORT=587
SMTP_USER=твой@яндекс.ру
SMTP_PASSWORD=пароль-приложения
EMAIL_FROM=твой@яндекс.ру
APP_URL=https://vector.myftp.org
```

`EMAIL_FROM` должен совпадать с `SMTP_USER`: Яндекс отклоняет письма, где
отправитель не соответствует авторизованному ящику.

Проверка:

```bash
cd backend
python test_mail.py --check                     # без отправки
python test_mail.py твой@адрес.ру               # реальное письмо
```

#### Почему здесь нельзя поднять свой почтовый сервер

Схема «Postfix + OpenDKIM на своём IP» для этого домена нерабочая, причём
не из-за настройки, а из-за того, чего нет в DNS. Проверено:

| Что требуется для отправки | Что фактически есть у `vector.myftp.org` |
|---|---|
| SPF и DKIM в TXT | зона обслуживается `no-ip.com`, поддомен **не делегирован** — TXT-записи добавить нельзя |
| PTR / обратный DNS для IP | у `109.120.131.130` его **нет**, и задать нельзя |
| Статический IP | адрес **динамический**, выдаётся `no-ip.com` |
| Исходящий порт 25 | фильтруется: до `mx.yandex.net` доходит, до `mx.google.com` нет |

DKIM-подпись без TXT-записи получатель не может проверить — она не даёт
ничего. Gmail и Яндекс отправку с такого адреса отклоняют. Чтобы вернуться
к своему SMTP, нужен VPS со статическим IP и отдельный домен с обычным
DNS-хостингом.

### Куда уходят письма

| Письмо | Когда | Куда |
|---|---|---|
| Код подтверждения | при регистрации и повторной отправке | на email пользователя |
| Сброс пароля | по запросу | на email пользователя |
| Уведомление | из очереди, раз в минуту | `user.email` |
| Просроченная задача | раз в 10 минут | `user.email` |

Если у пользователя `notify_email = False`, запись сразу помечается
обработанной, чтобы очередь не копилась.

---

## Уведомления

Транспорт выбирается по порту и переопределяется через `SMTP_STARTTLS`:

| Порт | Транспорт |
|---|---|
| 465 | `SMTP_SSL` (неявный TLS) |
| 587 | `SMTP` + `STARTTLS` |
| прочие (2525 и т.п.) | `SMTP` без TLS |

- **Email** — SMTP, очередь (`is_sent = False`) разбирается **каждую минуту**
- **Браузер** — хранятся в БД, фронт запрашивает при загрузке страницы
- **Просрочка** — джоб `_job_check_overdue` каждые **10 минут**
- Каналы настраиваются пользователем: `notify_email`, `notify_browser`,
  `notify_telegram` (telegram — заглушка), `notify_digest_time`

---

## Известные проблемы

Найдены при сверке документации с кодом. Не исправлены — описаны, чтобы не
вводить в заблуждение.

### Бэкенд (исправлено в рефакторинге)

| Что было | Статус |
|---|---|
| `ACCESS_TOKEN_EXPIRE_MINUTES` строкой → `TypeError` в `timedelta()`, **логин всегда 500** | ✅ конфиг централизован в `app/config.py` |
| XSS: `first_name` подставлялся в JS-литерал в ответе OAuth | ✅ значения сериализуются через JSON |
| Вход через Яндекс клал `role`/`first_name` отдельными ключами, `api.js` ждал `user` | ✅ контракт выровнен |
| Удаление задания/класса/задачи падало на внешних ключах (500) | ✅ каскады в `models.py` |
| Ученик мог править задание учителя и свой `grade` | ✅ закрыто |
| `/api/classes/available` отдавал все классы школы любому авторизованному | ✅ только свои |
| Подзадачи ИИ копировали `assignment_id` и завышали прогресс | ✅ не копируется |
| SMTP на порту 465 через `SMTP` вместо `SMTP_SSL` | ✅ выбирается по порту |
| HTML-инъекция в письма (заголовки задач без экранирования) | ✅ `_escape` |
| CORS `allow_origins=["*"]` вместе с credentials | ✅ явный allow-list |
| 5 маршрутов отдавали 500 вместо 404 | ✅ 404 |
| `_ensure_admin_user` создавал админа неактивным, инвертированная логика | ✅ исправлено |
| `create_tables` объявлена дважды | ✅ одна |
| `get_current_user` существовал в двух расходящихся копиях | ✅ один в `dependencies.py` |
| `get_db` не откатывал транзакцию при ошибке после `db.add()` | ✅ откат |
| ИИ-матчинг задач падал с `IndexError` при пустом списке | ✅ `app/ai_text.py` |
| Дашборд учителя показывал 0 учеников, если нет заданий | ✅ роустр всегда |
| Переход ученика в новый класс менял `grade`, но не членство | ✅ перенос явный |
| Нет ограничений целостности | ✅ модели + миграция 001 |
| `uvloop` не ставится на Windows | ✅ маркеры платформы |

### Фронтенд

| # | Что сделано | Где |
|---|---|---|
| 1 | Старый мультистраничный фронтенд заменён SPA без сборки | `frontend/index.html` |
| 2 | Ресурсы разложены по `css/`, `js/`, `html/`; тесты и `docs/` убраны | `frontend/` |
| 3 | Старые адреса (`/login`, `/dashboard`, `/teacher/*`) отдают оболочку SPA | `backend/app/main.py` |
| 4 | Связка SPA с API пока не сделана: данные демо, `localStorage` | `frontend/js/store.js` |

### Прочее

| # | Проблема | Файл |
|---|---|---|
| 9 | `DB_PATH=data/app.db` — мёртвая переменная, код её не читал | ✅ убрана, остатки SQLite удалены |
| 10 | `pydantic-settings` и `PyYAML` установлены, но не используются | `backend/requirements.txt` |
| 11 | Фреймворка миграций нет: `create_all()` не меняет созданные таблицы | `backend/app/database.py` |
| 12 | Планировщик рассчитан на один процесс uvicorn | `backend/app/routers/notifications.py` |

Про 12: `docker-compose` запускает один `uvicorn`, поэтому джобы не дублируются.
При нескольких воркерах каждый поднимет свой APScheduler — поставь
`ENABLE_SCHEDULER=false` лишним либо вынеси джобы в отдельный процесс.

---

## Лицензия

См. `LICENSE`.
