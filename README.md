# Timing — умный планировщик для школьников

Веб-приложение для управления учебными задачами с ИИ-ассистентом: ученик, учитель, родитель, администратор. Подробнее — в [DETAILS.md](DETAILS.md), бот — в [bot/README.md](bot/README.md), фронтенд — в [frontend/README.md](frontend/README.md).

## Быстрый старт (Docker)

```bash
cp .env.example .env   # заполнить значения из раздела «.env» ниже
docker compose up -d --build
docker exec timing_ollama ollama pull qwen2.5:7b   # модель ИИ, ~4.7 ГБ, один раз
```

| Что | Адрес |
|---|---|
| Сайт (локально) | http://127.0.0.1:8000 |
| Сайт (через nginx) | https://timing.myftp.org |
| Документация API | http://localhost:8000/docs |
| Панелька Mailpit (письма) | http://localhost:8025 или https://timing.myftp.org/mailpit/ (логин `timing`, пароль — `MAILPIT_PANEL_PASSWORD`, по умолчанию `demo2024`) |

## Готовые Docker-образы (GHCR)

Образы публикуются автоматически GitHub Actions (`.github/workflows/docker.yml`) при каждом push в `main`:

- `ghcr.io/levgamer39/vectorvk/backend:latest`
- `ghcr.io/levgamer39/vectorvk/bot:latest`
- `ghcr.io/levgamer39/vectorvk/nginx:latest`

Имена образов зашиты в `docker-compose.yml` (поля `image:`), поэтому `docker compose up -d` без `--build` подтянет готовый образ. Если форкнули репозиторий — поменяйте owner в `image:` и в workflow (там он берётся из `github.repository` сам).

## Демо-аккаунты

Создаются автоматически при первом запуске, пароль у всех **`demo2024`**:

| Роль | Логин | Что посмотреть |
|---|---|---|
| Администратор | `demo.admin` | классы, учителя, расписание, пропуски, почта |
| Учитель | `demo.teacher` | журнал, мой класс, приём работ |
| Ученик | `demo.student` | дневник, оценки, статистика, задачи, ИИ |
| Родитель | `demo.parent` | то же, что у ученика, + привязка ребёнка |
| Ученики | `demo.student2`, `demo.student3` | дополнительные профили ученика |

Невошедшие (видны в ростере как «ожидают», пароля нет): `demo.wait1`/`wait2`, `demo.teacher2`/`teacher3` (инвайты учителей `DEMO-TEACHER-2/3`). Классы в демо: 8А, 8Б, 4Б. Данные: расписание 8А, задание «Контрольная работа по алгебре», оценки 3–5, пара писем.

## ⚠️ Критично: бот — только один экземпляр!

**Ни при каких обстоятельствах нельзя запускать два экземпляра MAX-бота с одним токеном.** Два бота будут отвечать на каждое сообщение дважды, дублировать уведомления и ломать диалоги регистрации (состояние живёт в памяти процесса).

- В Docker это один контейнер `timing_bot` — не запускайте второй `bot.py` локально на той же машине и не поднимайте бот на двух серверах с одним `MAX_BOT_TOKEN`.
- То же для backend: планировщик писем/сводок запускается только в одном процессе (PostgreSQL advisory-лок), но второй backend всё равно лишний.

## .env — что заполнить

Скопируйте `.env.example` в `.env` и заполните. Значения по умолчанию — только для локального запуска, на сервер меняйте всё помеченное.

| Переменная | Обязательность | Значение |
|---|---|---|
| `APP_DOMAIN` | **обязательно** | Домен сайта, напр. `timing.myftp.org`. Единственное место домена: уходит в nginx и `APP_URL`. |
| `DB_PASS` | **обязательно** | Пароль PostgreSQL. Без него compose не стартует (намеренно). |
| `SECRET_KEY` | **обязательно** | Случайная строка ≥32 символов (JWT). |
| `ADMIN_PASSWORD` | **обязательно** | Пароль первого администратора (создаётся при старте). |
| `SMTP_*` | для писем | См. «Почта» ниже. |
| `YANDEX_CLIENT_ID` / `YANDEX_CLIENT_SECRET` | для входа через Яндекс | См. «Яндекс OAuth» ниже. Без них кнопка входа через Яндекс отдаёт 503. |
| `MAX_BOT_TOKEN` | для бота | Токен MAX-бота. Им же бот подписывает запросы к API. |
| `BOT_USER_ID_SALT` | **рекомендуется** | Случайная строка ≥16 символов. Соль хеширования MAX user_id; смена соли или ротация `MAX_BOT_TOKEN` инвалидирует все привязки MAX. |
| `MAILPIT_PANEL_PASSWORD` | для панельки Mailpit | Пароль от https://…/mailpit/ (логин `timing`). По умолчанию `demo2024` — **смените на сервере**. |
| `OLLAMA_MODEL` | по умолчанию `qwen2.5:7b` | Модель ИИ. Менять только здесь, не в коде. |
| `OLLAMA_AUTO_PULL` | `false` | `true` — модель докачается сама при первом старте (~4.7 ГБ, несколько минут). |

Формат `.env`: `КЛЮЧ=значение` без пробелов вокруг `=` и без BOM — compose иначе не примет.

## Если что-то не работает

- **ИИ отвечает «Сервис ИИ недоступен»** — модель не скачана (`docker exec timing_ollama ollama pull qwen2.5:7b`) или Docker Desktop/серверу не хватает RAM: `qwen2.5:7b` нужно ~6 ГБ, на Docker Desktop поднимите Settings → Resources → Memory до 8 ГБ. На сервере для всего стека нужно **12–16 ГБ RAM** (8 ГБ — впритык, лучше модель `qwen2.5:3b`).
- **Вход через Яндекс не работает** — это нормально без `YANDEX_CLIENT_ID/SECRET` в `.env` (кнопка вернёт 503). Сам OAuth реализован полностью: вход (`/api/auth/yandex/login-url` → `/callback`) и привязка в настройках (`/link` → `/confirm-link`, кнопка «Привязать Яндекс»). Блок `YANDEX_*_CALLBACK` должен совпадать с адресами в консоли Яндекса, иначе Яндекс не вернёт управление.
- **Письма не приходят** — проверьте `SMTP_*`. Для Яндекса нужен **пароль приложения** (https://id.yandex.ru/security/apppasswords), не пароль от аккаунта, и `EMAIL_FROM` = `SMTP_USER`. Для демки без внешней почты — Mailpit: `SMTP_HOST=mailpit`, `SMTP_PORT=1025`, пустые `SMTP_USER/PASSWORD`, `SMTP_STARTTLS=false`, запуск `docker compose --profile mailpit up -d mailpit`, письма смотрятся в панельке (см. таблицу адресов). Свой SMTP на домене `no-ip.com` не взлетит: нельзя прописать SPF/DKIM/PTR, письма уйдут в спам.
- **Бот отвечает дважды** — запущено два экземпляра бота. Оставьте один (см. «Критично» выше), второй остановите.
- **Бот не видит аккаунт / «MAX user not linked»** — пользователь не привязан: регистрация в боте по инвайту или `/link` с логином и паролем с сайта.
- **Голосовые в боте не работают** — ограничение на стороне MAX: голосовые боту не доставляются (известная проблема №13). Обработка в коде готова, заработает сама, когда MAX начнёт отдавать аудио.

## Локальный запуск (без Docker)

Нужна PostgreSQL (SQLite не поддерживается), Python, Ollama:

```powershell
docker compose up -d db                    # БД
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## Тесты

```powershell
docker compose up -d db
docker exec timing_db psql -U timing -d timingdb -c "CREATE DATABASE timingdb_test;"
cd backend; pip install -r requirements-dev.txt
python run_tests.py
```

Тесты бота — отдельно: `cd bot; pytest`.

## Ссылки

- [DETAILS.md](DETAILS.md) — архитектура, API, деплой, уведомления, БД
- `bot/README.md` — MAX-бот
- `frontend/README.md` — фронтенд
