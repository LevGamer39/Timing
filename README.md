# Timing — умный планировщик для школьников

Веб-приложение для управления учебными задачами с ИИ-ассистентом: ученик, учитель, родитель, администратор. Подробнее — в [DETAILS.md](DETAILS.md).

## Быстрый старт (Docker)

```bash
cp .env.example .env   # заполнить DB_PASS, SECRET_KEY, ADMIN_PASSWORD, APP_DOMAIN
docker compose up -d --build
docker exec timing_ollama ollama pull qwen2.5:7b   # модель ИИ (один раз)
```

Сайт — `http://127.0.0.1:8000` (Direct backend) или `https://timing.myftp.org` (через nginx). Документация API — `http://localhost:8000/docs`.

## Локальный запуск (без Docker)

Нужна PostgreSQL (SQLite не поддерживается), Python, Ollama:

```powershell
docker compose up -d db                    # БД
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## Демо-аккаунты

Создаются автоматически при первом запуске (пароль `demo2024`):

| Роль | Логин |
|---|---|
| Администратор | `demo.admin` |
| Учитель | `demo.teacher` |
| Ученик | `demo.student` |
| Родитель | `demo.parent` |

Не вошедшие (видны в ростере как «ожидают»): `demo.wait1`/`wait2` (ученики), `demo.teacher2`/`teacher3` (учителя, инвайты `DEMO-TEACHER-2/3`).

Для тестирования MAX-бота — `demo.admin` в админке.

## Ссылки

- [DETAILS.md](DETAILS.md) — архитектура, API, тесты, деплой, уведомления, БД
- `bot/README.md` — MAX-бот
- `frontend/README.md` — фронтенд
