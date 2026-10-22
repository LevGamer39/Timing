# VectorVK MAX Bot

Бот для мессенджера MAX. Работает как клиент к существующему FastAPI-бэкенду
`backend/` и не подключается к PostgreSQL напрямую.

MAX API вызывается через библиотеку [`maxapi`](https://pypi.org/project/maxapi/)
(аналог aiogram для MAX). Собственного HTTP-клиента к MAX в проекте нет.

## Структура

```
bot/
├── bot.py          # Точка входа: Bot + Dispatcher, polling или webhook
├── config.py       # Единственный источник настроек (load_dotenv)
├── handlers.py     # Команды, текст, голос, callback-кнопки, события чатов
├── parser.py       # Парсер ДЗ: keyword-фильтр → буфер → Qwen → карточка
├── ai.py           # Qwen через Ollama + regex-fallback
├── api.py          # HTTP-клиент к бэкенду VectorVK
├── sessions.py     # JWT в памяти, повторная выдача по хешу MAX user_id
├── security.py     # HMAC-хеш user_id, подпись запросов, маскирование токенов
├── scheduler.py    # Напоминания и недельная сводка
├── state.py        # JSON-состояние (пользователи, чаты, отметки)
├── keyboards.py    # Inline-клавиатуры maxapi
├── texts.py        # Форматирование сообщений и экранирование HTML
├── dockerfile
└── requirements.txt
```

## Запуск

Всё через общий `.env` в корне проекта — отдельный `bot/.env` не нужен.

```bash
docker compose up -d --build db backend ollama bot
docker compose logs -f bot
```

Для локальной разработки:

```bash
cd bot
pip install -r requirements.txt
python bot.py
```

Обязательна только переменная `MAX_BOT_TOKEN`. Для Docker `MAX_BOT_URL`
должен указывать на `http://backend:8000`, `OLLAMA_URL` — на
`http://ollama:11434` (это уже задано в `docker-compose.yml`).

## Команды

| Команда | Назначение |
|---|---|
| `/start` | Главное меню и проверка привязки аккаунта |
| `/today` | Задания на сегодня и просроченные |
| `/add <текст>` | Добавить задание (или просто прислать текст/голос) |
| `/done` | Список активных заданий с кнопкой «Сделал» |
| `/grades` | Оценки и средние баллы |
| `/summary` | Сводка успеваемости |
| `/schedule` | Расписание на сегодня |
| `/ai <вопрос>` | ИИ-помощник |
| `/hw on|off` | Включить/выключить парсер ДЗ в чате класса (админ чата) |
| `/hw` | Разобрать сообщение, на которое ответили командой |
| `/cancel` | Выйти из режима ИИ-чата |

## Эндпоинты бэкенда

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/api/users/auth/max-link` | Обмен подписанного хеша MAX user_id на JWT |
| POST | `/api/users/auth/link-max` | Привязка MAX к аккаунту после входа |
| GET | `/api/users/me/children` | Дети родителя |
| GET | `/api/tasks` | Задания ученика |
| POST | `/api/tasks` | Создать задание |
| POST | `/api/tasks/{id}/complete` | Отметить выполненным |
| DELETE | `/api/tasks/{id}` | Удалить задание |
| GET | `/api/grades/my`, `/my/averages`, `/my/summary` | Оценки |
| GET | `/api/grades/child/{id}` | Оценки ребёнка |
| GET | `/api/schedule/my`, `/child/{id}` | Расписание |
| GET | `/api/classes` | Классы учителя |
| POST | `/api/ai/chat` | ИИ-чат |

## Безопасность

- `MAX user_id` уходит в бэкенд только как HMAC-SHA256 с секретной солью.
- `/api/users/auth/max-link` принимает запрос только с подписью
  `X-Bot-Signature` на общем `MAX_BOT_TOKEN`.
- JWT хранится только в памяти процесса и никогда не пишется на диск.
- Токены в логах маскируются.
- На диск пишутся лишь MAX user_id, роли и служебные отметки напоминаний.
