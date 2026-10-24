# VectorVK MAX Bot

Бот для мессенджера MAX. Работает как клиент к существующему FastAPI-бэкенду
`backend/` и не подключается к PostgreSQL напрямую.

MAX API вызывается через библиотеку [`maxapi`](https://pypi.org/project/maxapi/)
(аналог aiogram для MAX). Собственного HTTP-клиента к MAX в проекте нет.

## Структура

```
bot/
├── bot.py              # Точка входа: Bot + Dispatcher, polling или webhook
├── config.py           # Настройки из общего .env
├── core/
│   ├── context.py      # Зависимости, общие для роутеров
│   ├── filters.py      # Фильтр callback-payload
│   ├── security.py     # HMAC-хеш и подпись запросов
│   ├── sessions.py     # JWT в памяти
│   ├── state.py        # JSON-состояние
│   └── ui.py           # Ответы, автор события, проверка привязки
├── services/
│   ├── backend.py      # HTTP-клиент к бэкенду
│   ├── ai.py           # Qwen через Ollama + regex-fallback
│   ├── homework.py     # Парсер ДЗ в чатах класса
│   ├── reminders.py    # Напоминания и недельная сводка
│   ├── tasks.py        # Создание заданий
│   ├── ai_chat.py      # ИИ-чат
│   └── views.py        # Готовые ответы для команд и меню
├── routers/
│   ├── common.py       # /start, /help, /cancel, события бота
│   ├── tasks.py        # /today, /add, /done, кнопки заданий
│   ├── grades.py       # /grades, /summary
│   ├── schedule.py     # /schedule
│   ├── ai_chat.py      # /ai
│   ├── homework.py     # /hw и кнопки карточек
│   ├── menu.py         # callback-кнопки меню
│   └── messages.py     # Свободный текст и голосовые
├── keyboards/          # Inline-клавиатуры
├── texts/              # Тексты и форматирование
├── data/               # runtime-состояние (volume)
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

Опциональная переменная `BOT_USER_ID_SALT` задаёт соль для хеширования
MAX user_id. Если не задана, соль выводится из `MAX_BOT_TOKEN` — при
ротации токена все привязки инвалидируются, поэтому для продакшена
рекомендуется явно задать случайную строку не короче 16 символов.

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
