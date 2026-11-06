"""Голосовые: извлечение текста и удаление временного файла.

Запуск из каталога ``bot``::

    cd bot && python -m pytest tests
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from maxapi.enums.attachment import AttachmentType

from services import voice


def _body(text=None, attachments=None):
    return SimpleNamespace(text=text, attachments=attachments)


def _audio(url="https://example/voice.ogg", transcription=None):
    return SimpleNamespace(
        type=AttachmentType.AUDIO,
        payload=SimpleNamespace(url=url),
        transcription=transcription,
    )


class FakeTranscriber:
    def __init__(self, text: str = "математика номер 42") -> None:
        self.text = text
        self.calls: list[tuple[str, int]] = []

    async def transcribe(self, url: str, user_id: int) -> str:
        self.calls.append((url, user_id))
        return self.text


def test_text_is_returned_without_transcription():
    transcriber = FakeTranscriber()
    text = asyncio.run(voice.body_text(_body("готово"), transcriber, 7))
    assert text == "готово"
    assert transcriber.calls == []


def test_max_transcription_is_preferred_over_download():
    transcriber = FakeTranscriber()
    body = _body(None, [_audio(transcription="нативное распознавание")])
    text = asyncio.run(voice.body_text(body, transcriber, 7))
    assert text == "нативное распознавание"
    assert transcriber.calls == []


def test_audio_is_transcribed():
    transcriber = FakeTranscriber()
    body = _body(None, [_audio()])
    text = asyncio.run(voice.body_text(body, transcriber, 7))
    assert text == "математика номер 42"
    assert transcriber.calls == [("https://example/voice.ogg", 7)]


def test_audio_can_be_disabled():
    transcriber = FakeTranscriber()
    body = _body(None, [_audio()])
    text = asyncio.run(voice.body_text(body, transcriber, 7, audio=False))
    assert text is None
    assert transcriber.calls == []


def test_non_audio_attachment_is_ignored():
    image = SimpleNamespace(
        type=AttachmentType.IMAGE,
        payload=SimpleNamespace(url="https://example/photo.jpg"),
        transcription=None,
    )
    transcriber = FakeTranscriber()
    body = _body(None, [image])
    assert asyncio.run(voice.body_text(body, transcriber, 7)) is None
    assert transcriber.calls == []


def _transcriber(tmp_path) -> voice.Transcriber:
    return voice.Transcriber(
        model="small",
        model_dir=str(tmp_path / "models"),
        device="cpu",
        compute_type="int8",
        language="ru",
        temp_dir=str(tmp_path / "audio"),
        timeout=5,
    )


def test_transcriber_removes_file_and_creates_dir(tmp_path, monkeypatch):
    transcriber = _transcriber(tmp_path)
    seen = {}

    async def fake_download(url: str, path: Path) -> None:
        path.write_bytes(b"OggS")
        seen["path"] = path

    class FakeModel:
        def transcribe(self, path: str, **kwargs) -> tuple:
            assert Path(path).exists()
            seen["during"] = Path(path)
            return [SimpleNamespace(text="  привет  ")], None

    transcriber._model = FakeModel()
    monkeypatch.setattr(transcriber, "_download", fake_download)

    text = asyncio.run(transcriber.transcribe("https://example/voice.ogg", 42))

    assert text == "привет"
    path = seen["path"]
    assert path.parent == tmp_path / "audio"
    assert path.name.startswith("audio_42_")
    assert path.suffix == ".ogg"
    assert seen["during"] == path
    assert not path.exists()


def test_transcriber_removes_file_on_error(tmp_path, monkeypatch):
    transcriber = _transcriber(tmp_path)
    seen = {}

    async def fake_download(url: str, path: Path) -> None:
        path.write_bytes(b"broken")
        seen["path"] = path

    class BrokenModel:
        def transcribe(self, path: str, **kwargs) -> tuple:
            raise RuntimeError("broken audio")

    transcriber._model = BrokenModel()
    monkeypatch.setattr(transcriber, "_download", fake_download)

    assert (
        asyncio.run(transcriber.transcribe("https://example/x.ogg", 1))
        is None
    )
    assert not seen["path"].exists()


def test_manual_hw_reply_transcribes_voice(monkeypatch):
    from services import homework

    sent = []

    async def fake_send_card(item, source):
        sent.append((item, source))

    async def fake_parse(text):
        return {"is_homework": True, "title": "№42"}

    linked = _body(None, [_audio(url="https://example/hw.ogg")])
    answers = []

    async def answer(text):
        answers.append(text)

    message = SimpleNamespace(
        body=_body("/hw"),
        link=SimpleNamespace(message=linked),
        sender=SimpleNamespace(user_id=7),
        recipient=SimpleNamespace(chat_id=1),
        answer=answer,
    )
    parser = homework.HomeworkParser(
        None, None, FakeTranscriber("сделать №42")
    )
    parser._send_card = fake_send_card
    monkeypatch.setattr(homework, "parse_task", fake_parse)

    asyncio.run(parser.manual(SimpleNamespace(message=message)))

    assert parser._transcriber.calls == [("https://example/hw.ogg", 7)]
    assert answers == ["Обрабатываю сообщение…"]
    assert sent[0][1] == "сделать №42"


def test_manual_hw_fetches_linked_voice_without_attachments(monkeypatch):
    from services import homework

    sent = []
    fetched = []

    async def fake_send_card(item, source):
        sent.append(source)

    async def fake_parse(text):
        return {"is_homework": True, "title": "№42"}

    async def answer(text):
        pass

    class FakeBot:
        async def get_messages(self, message_ids):
            fetched.extend(message_ids)
            return SimpleNamespace(
                messages=[
                    SimpleNamespace(
                        body=_body(
                            None, [_audio(url="https://example/full.ogg")]
                        )
                    )
                ]
            )

    # В link.message MAX иногда присылает только mid, без вложений.
    linked = SimpleNamespace(mid="orig-mid", text=None, attachments=None)
    message = SimpleNamespace(
        body=_body("/hw"),
        link=SimpleNamespace(message=linked),
        sender=SimpleNamespace(user_id=7),
        recipient=SimpleNamespace(chat_id=1),
        answer=answer,
    )
    parser = homework.HomeworkParser(
        FakeBot(), None, FakeTranscriber("сделать №42")
    )
    parser._send_card = fake_send_card
    monkeypatch.setattr(homework, "parse_task", fake_parse)

    asyncio.run(parser.manual(SimpleNamespace(message=message)))

    assert fetched == ["orig-mid"]
    assert parser._transcriber.calls == [("https://example/full.ogg", 7)]
    assert sent == ["сделать №42"]


def test_manual_hw_uses_remembered_voice_text(monkeypatch):
    from services import homework

    sent = []

    async def fake_send_card(item, source):
        sent.append(source)

    async def fake_parse(text):
        return {"is_homework": True, "title": "№42"}

    async def answer(text):
        pass

    class NoBot:
        async def get_messages(self, message_ids):
            raise AssertionError("голосовое уже расшифровано при отправке")

    # В ответе MAX не было вложений, но текст бот запомнил заранее.
    linked = SimpleNamespace(mid="orig-mid", text=None, attachments=None)
    message = SimpleNamespace(
        body=_body("/hw"),
        link=SimpleNamespace(message=linked),
        sender=SimpleNamespace(user_id=7),
        recipient=SimpleNamespace(chat_id=1),
        answer=answer,
    )
    parser = homework.HomeworkParser(NoBot(), None, FakeTranscriber())
    parser.remember("orig-mid", "сделать №42")
    parser._send_card = fake_send_card
    monkeypatch.setattr(homework, "parse_task", fake_parse)

    asyncio.run(parser.manual(SimpleNamespace(message=message)))

    assert sent == ["сделать №42"]
    assert parser._transcriber.calls == []


def test_dialog_text_and_voice_reach_task_creation(monkeypatch):
    from maxapi.enums.chat_type import ChatType

    from core.context import AppContext
    from routers import messages as messages_router

    created = []

    async def fake_create(ctx, event, token, text):
        created.append((token, text))

    monkeypatch.setattr(messages_router, "create_from_text", fake_create)

    class FakeSessions:
        async def token(self, user_id):
            return "jwt"

    class FakeStore:
        def upsert_user(self, *args, **kwargs):
            pass

    answers = []

    async def answer(text, attachments=None, format=None):
        answers.append(text)

    def event(body):
        message = SimpleNamespace(
            body=body,
            sender=SimpleNamespace(user_id=7, first_name="И", username="u"),
            recipient=SimpleNamespace(chat_id=1, chat_type=ChatType.DIALOG),
            answer=answer,
        )
        return SimpleNamespace(message=message)

    ctx = AppContext(
        bot=None,
        backend=None,
        store=FakeStore(),
        sessions=FakeSessions(),
        parser=SimpleNamespace(feed=None, remember=lambda *a: None),
        transcriber=FakeTranscriber("математика №42"),
    )
    handler = messages_router.build_router(ctx).event_handlers[0].func_event

    asyncio.run(handler(event(_body(None, [_audio()]))))
    asyncio.run(handler(event(_body("математика №43"))))

    assert created == [("jwt", "математика №42"), ("jwt", "математика №43")]
    # По одному подтверждению на сообщение: у голосового — до распознавания.
    assert answers == ["Обрабатываю сообщение…", "Обрабатываю сообщение…"]
