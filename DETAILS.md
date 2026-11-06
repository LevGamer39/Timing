# Vector — детальное описание

> Подробная документация после урезания README. Оглавление ниже — все разделы на месте.

## Оглавление

- [Стек](#стек)
- [Архитектура](#архитектура)
- [Структура проекта](#структура-проекта)
- [Роли пользователей](#роли-пользователей)
- [API](#api)
- [Тесты](#тесты)
- [Локальный запуск](#локальный-запуск)
- [Деплой](#деплой)
- [Переменные окружения](#переменные-окружения)
- [Система авторизации](#система-авторизации)
- [ИИ-ассистент](#ии-ассистент)
- [Статистика и приём работ](#статистика-и-приём-работ)
- [Перенос учеников между школами](#перенос-учеников-между-школами)
- [База данных](#база-данных)
- [Отправка писем](#отправка-писем)
- [Уведомления](#уведомления)
- [Известные проблемы](#известные-проблемы)
- [Лицензия](#лицензия)

## Стек

| Технология | Версия | Назначение |
|---|---|---|
| **FastAPI** | 0.115.0 | REST API |
| **PostgreSQL** + psycopg 3 | 3.3.6 | База данных |
| **SQLAlchemy** | 2.1.5 | ORM |
| **python-jose** | 3.3.0 | JWT-авторизация |
| **passlib + bcrypt** | 1.7.4 / 4.0.1 | Пароли |
| **APScheduler** | 3.10.4 | Фоновые задачи |
| **httpx** | 0.27.2 | Запросы к Ollama |
| **smtplib** (stdlib) | — | Письма SMTP |
| **pydantic** | 2.13.5 | Схемы |
| **uvicorn** | 0.30.6 | ASGI |
| **Ollama + Qwen2.5:7b** | — | Локальная LLM-модель для ИИ-ассистента |
| **HTML/CSS/JS** | — | Фронтенд без фреймворков и сборки |

Графики — на div/CSS без либ. Статику раздаёт FastAPI (`frontend/` в корень + `/static`).

## Архитектура

```
Интернет
  └─ nginx (TLS, редирект HTTP→HTTPS, gzip)
       └─ backend (FastAPI + frontend/)
            ├─ ollama (Qwen2.5:7b)
            └─ db (PostgreSQL)
  certbot (renew каждые 12ч)
```

- Все сервисы в `vector_net`. Наружу: `80`/`443` (nginx), `5432` (БД, loopback, для отладки).
- Бот (`bot/`) не ходит в PostgreSQL — дергает backend по `MAX_BOT_URL`.
- `APP_DOMAIN` в `.env` — единственное место домена; через `envsubst` (`nginx/docker-entrypoint.sh`) подставляется в `nginx.conf.template` (явным списком переменных, чтобы не съесть `$host`).
- Без сертификата entrypoint генерирует самоподписанный. Настоящий: `certbot certonly --webroot -w /var/www/certbot -d <домен>` (сначала `--dry-run`), порт 80 должен быть снаружи.

## Структура проекта

```
VectorVK/
├── backend/            # FastAPI: app/ (config, main, database, models, routers/*) + tests/
├── bot/                # MAX-бот: bot.py, core/, services/, routers/, keyboards/, texts/
├── frontend/           # SPA без сборки: index.html, css/, js/, html/, schemas/
├── nginx/              # nginx.conf.template, Dockerfile, docker-entrypoint.sh
├── docker-compose.yml  # backend, ollama, nginx, certbot, bot, db
├── .env.example
└── README.md / DETAILS.md / AGENTS.md
```

`bot/data/bot_state.json` — runtime-состояние бота (в `.gitignore`, в репо только `.gitkeep`).

## Роли пользователей

| Роль | Интерфейс |
|---|---|
| **Ученик** | `#/home`, `#/diary`, `#/grades`, `#/stats`, `#/tasks`, `#/mail`, `#/ai` (задачи, дневник, оценки, почта, ИИ) |
| **Учитель** | `#/journal`, `#/myclass`, `#/mail` (журнал, задания, приём работ) |
| **Родитель** | Те же экраны, что у ученика + переключатель ребёнка (только чтение + привязка по коду) |
| **Админ** | `#/classes`, `#/teachers`, `#/schedule`, `#/attendance`, `#/mail` |

Вход общий: пароль, код из MAX-бота или Яндекс ID. Учитель — только по инвайту. Ученик вступает в класс по 12-символьному коду. Родительские эндпоинты: `/api/grades/child/{id}`, `/api/tasks/child/{id}`, `/api/schedule/child/{id}`, `/api/stats/*/child/{id}`, `/api/users/me/children`.

### Демо-аккаунты

При первом запуске в БД создаются (пароль `demo2024`, SQLAlchemy-фикс, идемпотентно):

| Логин | Роль |
|---|---|
| `demo.admin` | Администратор |
| `demo.teacher` | Учитель (Алгебра, Геометрия) |
| `demo.student` | Ученик 8А (Артём) |
| `demo.student2` / `demo.student3` | Ученики 8А |
| `demo.parent` | Родитель (привязан к `demo.student`) |
| `demo.wait1` / `demo.wait2` | Не вошедшие ученики (`is_active=False`) |
| `demo.teacher2` / `demo.teacher3` | Не вошедшие учителя (`is_active=False`, инвайты `DEMO-TEACHER-2/3`) |

Классы: 8А, 8Б, 4Б. Данные: расписание 8А, задание «Контрольная работа по алгебре», оценки 3–5, 2 письма. Для тестирования MAX-бота зарегистрироваться по инвайту от `demo.admin`.

## API

Префиксы: `/api/users`, `/api/admin`, `/api/admin/migration`, `/api/stats`, `/api/classes`, `/api/assignments`, `/api/tasks`, `/api/grades`, `/api/schedule`, `/api/journal`, `/api/attendance`, `/api/messages`, `/api/school`, `/api/ai`, `/api/notifications`, `/api/auth/yandex`. Swagger — `/docs` (локально). Ошибки — `{"detail": "..."}` без обёртки.

## Тесты

pytest против настоящего PostgreSQL (SQLite не поддерживается — не проверяются enum/FK/агрегаты):

```powershell
docker compose up -d db
docker exec vector_db psql -U vector -d vectordb -c "CREATE DATABASE vectordb_test;"
cd backend; pip install -r requirements-dev.txt
python run_tests.py   # берёт DB_* из ../.env, подставляет 127.0.0.1
```

Без `TEST_DATABASE_URL` conftest падает сразу (тихой подмены БД нет). На Windows используйте `127.0.0.1`, не `localhost`.

## Локальный запуск

1. **Зависимости:** `cd backend; python3 -m venv .venv; pip install -r requirements.txt`
2. **.env:** `cp .env.example .env` — как минимум `DB_PASS`, `SECRET_KEY`, `ADMIN_PASSWORD`, `APP_URL`
3. **Ollama:** `ollama serve && ollama pull qwen2.5:7b`
4. **Backend:** `uvicorn app.main:app --reload --host 127.0.0.1 --port 8000` (таблицы + админ создаются автоматически)

## Деплой

- Сервер: Yandex Compute Cloud Ubuntu 22.04, 4 vCPU, 8 GB RAM, 40 GB диск; DNS A-запись, статический IP.
- Проброс портов `80`、`443` обязателен для Let's Encrypt.
- Переменные домена — только в `.env` (`APP_DOMAIN`, `APP_URL`, `YANDEX_*_CALLBACK`), совпадают с консолью Яндекса.

## Переменные окружения

Ключевые: `APP_DOMAIN`, `DB_*` (или `DATABASE_URL`), `ADMIN_*`, `SECRET_KEY`, `SMTP_*`, `OLLAMA_*`, `AI_*` (контекст/таймауты), `TIMEZONE` (в БД UTC), `WEEKLY_SUMMARY_*`, `ENABLE_SCHEDULER`, `YANDEX_*`, `MAX_BOT_TOKEN`, `MAX_BOT_URL`, `BOT_UPDATE_MODE`. Полный список — в `.env.example`.

## Система авторизации

- Регистрация → подтверждение email 6-значным кодом (15 мин)
- MAX-регистрация: логин + инвайт-код, без email/пароля; код класса → ученик, одноразовый код из `invite_codes` → учитель
- JWT HS256, 7 дней, `localStorage.token`
- Сброс пароля/смена email — ссылка (1 час), привязка родителя — код 8 цифр (1 час)

## ИИ-ассистент

Qwen2.5:7b через Ollama, системный промпт запрещает решать задания (только планирование). Чат с контекстом, анализ нагрузки, разбивка на подзадачи, приоритеты, итог по динамике недель (кэш в `stats_summaries`).

## Статистика и приём работ

`#/stats` и `/api/stats/*`: срез дня (задания + «ждёт закрытия»), неделя (в срок/позже/открыто/принято), динамика 8 недель (div-полосы). «Позже срока» = `completed_at > deadline`. Учитель: `/api/assignments/workload/{class_id}` — подсказка нагрузки, приём работ `done → accepted` (+`accepted_at`), отказ → `in_progress`/`overdue`.

## Перенос учеников между школами

Один сервер — одна школа. `GET/POST /api/admin/migration/students` (JSON, схема в `frontend/schemas/student-migration.schema.json`). Переносятся профиль, класс по названию, оценки, итоговые. Импорт идемпотентен по логину.

## База данных

PostgreSQL, 28 таблиц. `create_all()` создаёт только отсутствующие таблицы — колонки требуют `ALTER TABLE`; идемпотентный DDL в `_apply_schema_updates()` (`backend/app/database.py`), enum-значения — через autocommit (`_add_enum_values()`). Каскады в `models.py` (удаление класса/задания уносит зависимые строки).

## Отправка писем

Python `smtplib` (stdlib). Транспорт по порту: `465` — `SMTP_SSL`, `587` — `SMTP+STARTTLS`, остальные — без TLS. Очередь (раз в минуту), джоб overdue (10 мин), недельная сводка; backoff 5→15→60→240→720 мин, списание через 24ч. Проверка: `python test_mail.py --check`. Свой SMTP невозможен (домен на `no-ip.com`, нет DNS-контроля).

## Уведомления

- **MAX** (основной): `notifications` → бот забирает `/api/notifications/max/pending`, подтверждает `/delivered`; `users.notify_max` выключает целиком
- **Email** (опциональный, `notify_email`), **браузер** (хранятся в БД), ожидание overdue, `Notification.dedupe_key` (уникален с `user_id`)
- Настройки юзера: `notify_email`, `notify_browser`, `notify_max`

## Известные проблемы

| # | Проблема | Файл |
|---|---|---|
| 11 | Миграций нет: `create_all()` не меняет таблицы | `backend/app/database.py` |
| 12 | Планировщик на 1 процесс uvicorn | `backend/app/routers/notifications.py` |
| 10, 13 | Мелкие: неиспользуемые зависимости; свой SMTP | requirements / DNS |

## Лицензия

См. `LICENSE`.
