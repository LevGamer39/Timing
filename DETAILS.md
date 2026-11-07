# Timing — детальное описание

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
- [MAX-бот и голосовые](#max-бот-и-голосовые)
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

- Все сервисы в `timing_net`. Наружу: `80`/`443` (nginx), `5432` (БД, loopback, для отладки).
- Бот (`bot/`) не ходит в PostgreSQL — дергает backend по `MAX_BOT_URL`.
- `APP_DOMAIN` в `.env` — единственное место домена; через `envsubst` (`nginx/docker-entrypoint.sh`) подставляется в `nginx.conf.template` (явным списком переменных, чтобы не съесть `$host`).
- Без сертификата entrypoint генерирует самоподписанный. Настоящий: `certbot certonly --webroot -w /var/www/certbot -d <домен>` (сначала `--dry-run`), порт 80 должен быть снаружи.
- Выпуск настоящего сертификата — через `docker compose run --rm --entrypoint certbot certbot certonly --webroot -w /var/www/certbot -d <домен> --agree-tos --no-eff-email --dry-run`. `--entrypoint certbot` обязателен: у сервиса certbot в compose свой `entrypoint` (цикл `certbot renew`), без подмены аргументы игнорируются. ACME-проверка ходит снаружи на порт 80 — если он не проброшен (`80 → <IP машины>:80`, `443 → <IP>:443`), Let's Encrypt получает `Connection refused`. Пока порт 80 закрыт, работает только самоподписанный (браузер ругается — осознанный режим для локали).
- `frontend/` монтируется в backend как `/frontend:ro`; `frontend_path` в `main.py` = `../../frontend` от `app/` — в контейнере это `/frontend`, локально — корень репозитория. Логика одна и та же.

## Структура проекта

```
Timing/
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
docker exec timing_db psql -U timing -d timingdb -c "CREATE DATABASE timingdb_test;"
cd backend; pip install -r requirements-dev.txt
python run_tests.py   # берёт DB_* из ../.env, подставляет 127.0.0.1
```

Без `TEST_DATABASE_URL` conftest падает сразу (тихой подмены БД нет). На Windows используйте `127.0.0.1`, не `localhost`.

Тесты бота — отдельно, без БД (клавиатуры, регистрация, голос):
```powershell
cd bot; pytest
```

## Локальный запуск

1. **Зависимости:** `cd backend; python3 -m venv .venv; pip install -r requirements.txt`
2. **.env:** `cp .env.example .env` — как минимум `DB_PASS`, `SECRET_KEY`, `ADMIN_PASSWORD`, `APP_URL`
3. **Ollama:** `ollama serve && ollama pull qwen2.5:7b`
4. **Backend:** `uvicorn app.main:app --reload --host 127.0.0.1 --port 8000` (таблицы + админ создаются автоматически)

## Деплой

- Сервер: Yandex Compute Cloud Ubuntu 22.04, 4 vCPU, 8 GB RAM, 40 GB диск; DNS A-запись, статический IP.
- Проброс портов `80`、`443` обязателен для Let's Encrypt.
- Переменные домена — только в `.env` (`APP_DOMAIN`, `APP_URL`, `YANDEX_*_CALLBACK`), совпадают с консолью Яндекса.
- Порядок первого деплоя: Docker → `git clone` + `cp .env.example .env` → SSL-сертификат standalone (пока nginx не запущен, порт 80 свободен) → `docker compose up -d ollama && docker exec timing_ollama ollama pull qwen2.5:7b` → `docker compose up -d --build`.
- В Docker модель качается **вручную** из-за ~4.7 ГБ: `OLLAMA_AUTO_PULL=true` в `.env` включает одноразовую задачу `ollama-init` (тянет `OLLAMA_MODEL` при старте и завершается). Без флага она мгновенно выходит — модель потом можно дотянуть вручную той же командой `docker exec`.

## Переменные окружения

Ключевые: `APP_DOMAIN`, `DB_*` (или `DATABASE_URL`), `ADMIN_*`, `SECRET_KEY`, `SMTP_*`, `OLLAMA_*`, `AI_*` (контекст/таймауты), `TIMEZONE` (в БД UTC), `WEEKLY_SUMMARY_*`, `ENABLE_SCHEDULER`, `YANDEX_*`, `MAX_BOT_TOKEN`, `MAX_BOT_URL`, `BOT_UPDATE_MODE`. Полный список — в `.env.example`.

Бот (`bot/`, см. также `bot/README.md`):

| Переменная | Значение по умолчанию | Смысл |
|---|---|---|
| `COMPOSE_PROJECT_NAME` | `timing` | Префикс volume/сети Compose (`timing_ollama_data`, `timing_timing_net`), а не имя папки |
| `MAX_BOT_TOKEN` | — | Токен MAX-бота; им же подписываются его запросы к API (`X-Bot-Signature`) |
| `MAX_BOT_URL` | `http://backend:8000` | Адрес API для бота (в Docker) |
| `BOT_UPDATE_MODE` | `long_polling` | `long_polling` или `webhook` |
| `WEBHOOK_URL` / `WEBHOOK_SECRET` | — | Только при `BOT_UPDATE_MODE=webhook` |
| `BOT_STATE_PATH` | `data/bot_state.json` | Файл состояния бота (volume `./bot/data`) |
| `BOT_USER_ID_SALT` | из `MAX_BOT_TOKEN` | Соль хеширования MAX user_id; смена соли инвалидирует привязки |
| `OLLAMA_AUTO_PULL` | `false` | Тянуть модель при первом старте (см. «Деплой») |

## Система авторизации

- Регистрация → подтверждение email 6-значным кодом (15 мин)
- MAX-регистрация: логин + инвайт-код, без email/пароля; код класса → ученик, одноразовый код из `invite_codes` → учитель
- Учитель регистрируется **только по инвайт-коду** (16 символов, выдаёт админ); код списывается при подтверждении email
- Вход в класс ученика — по инвайт-коду класса (**12 символов**)
- JWT HS256, 7 дней, `localStorage.token`
- Сброс пароля/смена email — ссылка (1 час), привязка родителя — код 8 цифр (1 час)
- OAuth Яндекса: `/api/auth/yandex/login-url` → `/callback`; привязка веб-аккаунта к MAX — через `/link` в боте

## ИИ-ассистент

Qwen2.5:7b через Ollama, системный промпт запрещает решать задания (только планирование). Чат с контекстом, анализ нагрузки, разбивка на подзадачи, приоритеты, итог по динамике недель (кэш в `stats_summaries`).

## Статистика и приём работ

`#/stats` и `/api/stats/*`: срез дня (задания + «ждёт закрытия»), неделя (в срок/позже/открыто/принято), динамика 8 недель (div-полосы). «Позже срока» = `completed_at > deadline`. Учитель: `/api/assignments/workload/{class_id}` — подсказка нагрузки, приём работ `done → accepted` (+`accepted_at`), отказ → `in_progress`/`overdue`.

## Перенос учеников между школами

Один сервер — одна школа. `GET/POST /api/admin/migration/students` (JSON, схема в `frontend/schemas/student-migration.schema.json`). Переносятся профиль, класс по названию, оценки, итоговые. Классы принимающей стороны остаются локальными, **журнал не переносится**. Импорт идемпотентен по логину. Внутри школы ученика переносит админ в карточке класса (`app/enrollment.py` — членство и `User.grade` вместе, задачи по заданиям нового класса досоздаются).

## База данных

PostgreSQL, 28 таблиц. `create_all()` создаёт только отсутствующие таблицы — колонки требуют `ALTER TABLE`; идемпотентный DDL в `_apply_schema_updates()` (`backend/app/database.py`), enum-значения — через autocommit (`_add_enum_values()`). Каскады в `models.py` (удаление класса/задания уносит зависимые строки).

Ограничения целостности (в моделях, попадают в схему при создании):

| Ограничение | Смысл |
|---|---|
| `uq_grades_student_assignment` | Одна оценка на ученика на задание (`NULL` не ограничены — отдельные оценки работают) |
| `uq_class_memberships_student` | Ученик ровно в одном классе (это же предполагает `User.grade`) |
| `ck_grades_value_range` | Оценка 1..5 |

На старой базе ограничения догоняет `_apply_schema_updates()` при старте (идемпотентный DO-блок по `pg_constraint`). Если на базе есть дубли/оценки вне 1..5, старт упадёт — сначала почистить данные.

## Отправка писем

Python `smtplib` (stdlib). Транспорт по порту: `465` — `SMTP_SSL`, `587` — `SMTP+STARTTLS`, остальные — без TLS. Очередь (раз в минуту), джоб overdue (10 мин), недельная сводка; backoff 5→15→60→240→720 мин, списание через 24ч. Проверка: `python test_mail.py --check` (без отправки), `python test_mail.py someone@example.com [--template overdue]` — шаблоны `plain|verify|reset|overdue`. Санитизация заголовков и HTML-экранирование — в `notifications.py` (`_header`/`_escape`), транспорт по порту — `config.starttls_for_port()`.
Яндекс SMTP: нужен **пароль приложения** (не пароль от аккаунта) — https://id.yandex.ru/security/apppasswords. `EMAIL_FROM` должен совпадать с `SMTP_USER`, иначе Яндекс отклоняет.
Свой SMTP невозможен: домен на `no-ip.com` (зона не делегирована — TXT/SPF/DKIM не добавить), у IP нет PTR, адрес динамический, порт 25 фильтруется. Нужен VPS со статическим IP и домен с обычным DNS.

## Уведомления

- **MAX** (основной): `notifications` → бот забирает `/api/notifications/max/pending`, подтверждает `/delivered`; `users.notify_max` выключает целиком
- **Email** (опциональный, `notify_email`), **браузер** (хранятся в БД), ожидание overdue, `Notification.dedupe_key` (уникален с `user_id`)
- Настройки юзера: `notify_email`, `notify_browser`, `notify_max`
- Планировщик (письма, overdue, сводка) запускается только в одном процессе-лидере: PostgreSQL advisory-лок (`SCHEDULER_LOCK_KEY` в `notifications.py`), остальные воркеры uvicorn джобы не дублируют

## MAX-бот и голосовые

Бот (`bot/`) не ходит в PostgreSQL: он вызывает backend по `MAX_BOT_URL` (`http://backend:8000` в Docker) и подписывает запросы токеном (`X-Bot-Signature`, из `MAX_BOT_TOKEN`). Режимы: `long_polling` (по умолчанию) или `webhook` (`WEBHOOK_URL`/`WEBHOOK_SECRET`). Состояние — `data/bot_state.json` (volume). Детали команд, клавиатур и регистрации — `bot/README.md`.

Голосовые: обработка на нашей стороне готова — `bot/services/voice.py` берёт `attachment.transcription` от MAX, а при его отсутствии скачивает аудио и распознаёт через faster-whisper. Через `body_text()` её вызывают `/hw`, «Добавить», личный чат и парсер ДЗ в чатах класса.

**Но MAX голосовые боту не доставляет** (проблема №13 ниже): `message_created` приходит без поля `message` — ни чата, ни `mid`, ни вложения `audio`; `GET /messages` аудио тоже не отдаёт. Поэтому голос сейчас не работает ни в polling, ни в webhook (кружки и видео при этом работают). Как только MAX начнёт отдавать `message` с аудио, всё заработает без правок кода.

## Известные проблемы

| # | Проблема | Файл |
|---|---|---|
| 13 | MAX не доставляет боту голосовые: `message_created` приходит без `message` | `bot/services/voice.py` |

Исправлены ранее и убраны из таблицы: №10 — `pydantic-settings`/`PyYAML` удалены из `backend/requirements.txt`; №11 — ограничения моделей догоняются идемпотентным DDL в `_apply_schema_updates()` (полноценных миграций по-прежнему нет); №12 — планировщик выбирает один процесс-лидер через PostgreSQL advisory-лок (`SCHEDULER_LOCK_KEY` в `notifications.py`), при рестарте лидера остальные воркеры джобы не подхватывают — нужен рестарт.

Про 13: ограничение на стороне MAX ([maxapi#191](https://github.com/love-apples/maxapi/issues/191), не исправлено). Воспроизводится и в long polling, и в webhook. Обработка готова (см. «MAX-бот и голосовые» выше) — ждать только, пока MAX начнёт отдавать аудио.

## Лицензия

См. `LICENSE`.
