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
│   ├── registration.py # Диалог регистрации из MAX
│   ├── ai.py           # Qwen через Ollama + regex-fallback
│   ├── homework.py     # Парсер ДЗ в чатах класса
│   ├── reminders.py    # Напоминания и недельная сводка
│   ├── tasks.py        # Создание заданий
│   ├── ai_chat.py      # ИИ-чат
│   └── views.py        # Готовые ответы для команд и меню
├── routers/
│   ├── common.py       # /start, /help, /cancel, события бота
│   ├── registration.py # Кнопки подтверждения имени при регистрации
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
├── tests/              # pytest без сети: бэкенд и MAX подменены
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
| `/code` | Одноразовый код для входа на сайт (10 минут) |
| `/link` | Привязать аккаунт, созданный на сайте |
| `/cancel` | Выйти из ИИ-чата или регистрации |

## Регистрация

Пользователь без привязки видит кнопку «Зарегистрироваться» вместо меню.
Диалог хранится в памяти процесса (`AppContext.registration`) и состоит из
шагов:

1. **Логин** — бот проверяет `GET /api/users/login-available`
   (регистр не важен) и не принимает занятый.
2. **Инвайт-код** — `GET /api/users/invite-code` отвечает, существует ли код
   и для какой роли он выдан:
   - код класса (`classes.invite_code`, 12 символов) → ученик, сразу
     зачисляется в этот класс;
   - одноразовый код из `invite_codes` (16 символов) → учитель, код гасится.
3. **Пароль** — для входа на сайт, не короче 8 символов. Хранится только
   хешем (`password_hash`), в открытом виде нигде не сохраняется.
4. **Имя** — бот берёт `first_name` и `last_name` из профиля MAX и просит
   подтвердить кнопками «Всё верно» / «Изменить». При правке имя и фамилия
   отправляются **двумя строками** (в них бывают пробелы), после чего бот
   переспрашивает с разобранными полями.
5. **Создание аккаунта** — `POST /api/users/auth/max-register` с подписью
   `X-Bot-Signature`: бэкенд создаёт пользователя (`email = NULL`,
   `password_hash` из пароля, `is_active = true`), сохраняет хеш MAX user_id и
   возвращает JWT. Бот кладёт токен в память и сразу показывает меню.

Пароль и код из MAX — два независимых способа входа в SPA. Код выдаётся
командой `/code` или кнопкой «Вход на сайт» в меню.

Аккаунт, созданный на сайте, привязывается командой `/link`: бот спрашивает
логин и пароль, вызывает `/api/users/login` и `/api/users/auth/link-max` с
хешем MAX user_id. После этого работает и вход по коду, и обычные команды бота.

`/start` и `/cancel` прерывают диалог и начинают его заново.

## Эндпоинты бэкенда

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/api/users/auth/max-link` | Обмен подписанного хеша MAX user_id на JWT |
| POST | `/api/users/auth/max-register` | Регистрация из бота: логин + пароль + инвайт-код → JWT |
| POST | `/api/users/auth/max-code/issue` | Бот выдаёт одноразовый код для входа на сайт |
| POST | `/api/users/auth/max-code` | Обмен кода на JWT |
| GET | `/api/users/login-available` | Свободен ли логин |
| GET | `/api/users/invite-code` | Существует ли инвайт-код и для какой роли |
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

## Тесты

```bash
cd bot
pip install -r requirements.txt pytest
pytest
```

`bot/pytest.ini` добавляет корень бота в `pythonpath`, поэтому работают и
`pytest`, и `python -m pytest tests` — импорты в боте плоские (`core.*`,
`routers.*`).

Тесты не ходят в сеть: бэкенд подменён заглушкой, события MAX — простыми
объектами. Проверяются диалог регистрации и разбор имени.

## Безопасность

- `MAX user_id` уходит в бэкенд только как HMAC-SHA256 с секретной солью.
- `/api/users/auth/max-link` и `/api/users/auth/max-register` принимают
  запрос только с подписью `X-Bot-Signature` на общем `MAX_BOT_TOKEN`.
- JWT хранится только в памяти процесса и никогда не пишется на диск.
- Токены в логах маскируются.
- На диск пишутся лишь MAX user_id, роли и служебные отметки напоминаний.
