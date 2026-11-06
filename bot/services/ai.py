"""Разбор учебных текстов через Qwen и запасной regex-парсер."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import Any

import httpx

from config import settings

logger = logging.getLogger("timing_bot.ai")

SUBJECTS = {
    "матем": "математика",
    "алгебр": "алгебра",
    "геометр": "геометрия",
    "русск": "русский язык",
    "литератур": "литература",
    "англ": "английский язык",
    "физик": "физика",
    "хими": "химия",
    "биолог": "биология",
    "истор": "история",
    "географ": "география",
    "информ": "информатика",
    "обж": "ОБЖ",
    "труд": "труд",
    "технолог": "технология",
    "общество": "обществознание",
}

HOMEWORK_MARKERS = (
    "домашн",
    "дз",
    "задани",
    "реши",
    "выучи",
    "напиши",
    "подготовь",
    "составь",
    "прочитай",
    "прочитать",
    "пересказ",
    "упражнени",
    "упр",
    "параграф",
    "страниц",
    "стр.",
    "стр ",
    "номер",
    "к завтра",
    "к понедельник",
    "к пятниц",
    "к среде",
    "до ",
)

_WEEKDAYS = {
    "понедельник": 0,
    "вторник": 1,
    "среду": 2,
    "среда": 2,
    "четверг": 3,
    "пятниц": 4,
    "суббот": 5,
    "воскресень": 6,
}

TASK_PROMPT = """Ты — школьный помощник TimingVK. Разбери текст и верни ТОЛЬКО JSON без пояснений:
{{"is_homework": true|false, "title": "краткая суть задания", "subject": "предмет или null", "deadline": "YYYY-MM-DD или null", "priority": "low|medium|high|critical"}}
Сегодня {today}. Если срок не указан — deadline null. Предмет выбирай из школьной программы. Приоритет high — если срок сегодня/завтра или сказано «срочно».

Текст:
{text}"""

BATCH_PROMPT = """Ты — школьный помощник TimingVK. Ниже сообщения из чата класса.
Верни ТОЛЬКО JSON-массив объектов для каждого сообщения, которое является домашним заданием:
[{{"index": 0, "title": "суть", "subject": "предмет или null", "deadline": "YYYY-MM-DD или null", "priority": "low|medium|high|critical"}}]
Пропускай болтовню, вопросы и сообщения без задания. Сегодня {today}.

Сообщения:
{messages}"""


def looks_like_homework(text: str) -> bool:
    """Дешёвый keyword-фильтр до обращения к модели."""
    if not settings.parser_keyword_filter:
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in HOMEWORK_MARKERS)


def _extract_json(raw: str) -> Any:
    """Достаёт первый JSON-объект или массив из ответа модели.

    Раньше сначала искались фигурные скобки, поэтому массив
    ``[{"index": 0}, {"index": 1}]`` обрезался до одного объекта и
    ``json.loads`` падал с ``Extra data``. ``raw_decode`` берёт ровно одно
    значение с того места, где оно начинается, и корректно разбирает оба
    варианта.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return value
    raise ValueError("no JSON in model response")


async def _ollama(messages: list[dict[str, str]]) -> str:
    async with httpx.AsyncClient(timeout=settings.ollama_timeout) as client:
        response = await client.post(
            f"{settings.ollama_url}/api/chat",
            json={
                "model": settings.ollama_model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.2},
            },
        )
    response.raise_for_status()
    return response.json()["message"]["content"]


def _subject(text: str) -> str | None:
    lowered = text.lower()
    for marker, name in SUBJECTS.items():
        if marker in lowered:
            return name
    return None


def _deadline(text: str, today: date | None = None) -> str | None:
    today = today or date.today()
    lowered = text.lower()

    if "послезавтра" in lowered:
        return (today + timedelta(days=2)).isoformat()
    if "завтра" in lowered:
        return (today + timedelta(days=1)).isoformat()
    if "сегодня" in lowered:
        return today.isoformat()

    match = re.search(r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b", lowered)
    if match:
        day, month, year = match.groups()
        year_value = int(year) if year else today.year
        if year_value < 100:
            year_value += 2000
        try:
            return date(year_value, int(month), int(day)).isoformat()
        except ValueError:
            pass

    for name, weekday in _WEEKDAYS.items():
        if name in lowered:
            days_ahead = (weekday - today.weekday()) % 7 or 7
            return (today + timedelta(days=days_ahead)).isoformat()
    return None


def _priority(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("срочно", "важно", "сегодня", "завтра")):
        return "high"
    if any(word in lowered for word in ("не срочно", "потом", "позже")):
        return "low"
    return "medium"


def _title(text: str) -> str:
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    line = re.sub(r"^(дз|домашн\w*|задание)\s*[:\-–—]?\s*", "", line, flags=re.I)
    return (line or text.strip())[:120]


def regex_parse(text: str) -> dict[str, Any]:
    """Предсказуемый разбор без нейросети."""

    return {
        "is_homework": looks_like_homework(text),
        "title": _title(text),
        "subject": _subject(text),
        "deadline": _deadline(text),
        "priority": _priority(text),
    }


def _normalize(parsed: dict[str, Any], source: str) -> dict[str, Any]:
    deadline = parsed.get("deadline")
    if not isinstance(deadline, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
        deadline = _deadline(source)

    priority = parsed.get("priority")
    if priority not in {"low", "medium", "high", "critical"}:
        priority = _priority(source)

    subject = parsed.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        subject = _subject(source)

    title = parsed.get("title")
    if not isinstance(title, str) or not title.strip():
        title = _title(source)

    return {
        "is_homework": bool(parsed.get("is_homework", True)),
        "title": title.strip()[:120],
        "subject": subject.strip() if isinstance(subject, str) else None,
        "deadline": deadline,
        "priority": priority,
    }


async def parse_task(text: str) -> dict[str, Any]:
    """Разбирает одно сообщение в задание."""

    prompt = TASK_PROMPT.format(today=date.today().isoformat(), text=text[:4000])
    try:
        raw = await _ollama([{"role": "user", "content": prompt}])
        return _normalize(_extract_json(raw), text)
    except Exception as exc:  # noqa: BLE001 — модель может быть недоступна
        logger.warning("Qwen недоступен для разбора задания: %s", exc)
        return regex_parse(text)


async def parse_batch(texts: list[str]) -> list[dict[str, Any]]:
    """Разбирает пачку сообщений одним запросом к модели."""

    numbered = "\n".join(f"[{index}] {text[:1500]}" for index, text in enumerate(texts))
    prompt = BATCH_PROMPT.format(
        today=date.today().isoformat(), messages=numbered[:12000]
    )
    try:
        raw = await _ollama([{"role": "user", "content": prompt}])
        payload = _extract_json(raw)
        if not isinstance(payload, list):
            raise ValueError("expected JSON array")
        result: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            if not isinstance(index, int) or not 0 <= index < len(texts):
                continue
            normalized = _normalize(item, texts[index])
            normalized["source"] = texts[index]
            result.append(normalized)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("Qwen недоступен для разбора пачки: %s", exc)
        return [
            {**regex_parse(text), "source": text}
            for text in texts
            if looks_like_homework(text)
        ]


async def chat_reply(text: str, context: str = "") -> str:
    """Запасной ИИ-чат, если бэкенд недоступен."""

    system = (
        "Ты — дружелюбный помощник школьника TimingVK. Отвечай кратко, "
        "по-русски, без выдуманных фактов."
    )
    if context:
        system += f"\n\nКонтекст ученика:\n{context}"
    try:
        return await _ollama(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": text[:4000]},
            ]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Qwen недоступен для чата: %s", exc)
        return "ИИ временно недоступен. Попробуйте позже."
