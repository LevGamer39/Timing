"""Привязка аккаунта к Яндекс ID.

Поток: авторизованный пользователь открывает /link, подтверждает доступ
на Яндексе, возвращается на /confirm-link с code и state; только там
yandex_id пишется в users.
"""
import asyncio
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from jose import jwt

from app import config
from app.routers import yandex_auth


@pytest.fixture()
def yandex_config(monkeypatch):
    monkeypatch.setattr(config, "YANDEX_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(config, "YANDEX_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(
        config, "YANDEX_LINK_CALLBACK", "http://localhost:8000/api/auth/yandex/confirm-link"
    )


def test_link_returns_json_url_with_signed_state(db, student, yandex_config):
    result = asyncio.run(yandex_auth.initiate_link(student))

    query = parse_qs(urlparse(result["url"]).query)
    assert query["redirect_uri"] == [config.YANDEX_LINK_CALLBACK]
    payload = jwt.decode(query["state"][0], config.SECRET_KEY, algorithms=[config.ALGORITHM])
    assert payload["sub"] == str(student.id)
    assert payload["scope"] == "yandex_link"


def test_link_without_oauth_config_is_503(db, student, monkeypatch):
    monkeypatch.setattr(config, "YANDEX_CLIENT_ID", "")
    monkeypatch.setattr(config, "YANDEX_CLIENT_SECRET", "")

    with pytest.raises(HTTPException) as error:
        asyncio.run(yandex_auth.initiate_link(student))
    assert error.value.status_code == 503


def test_confirm_link_without_state_is_401(db):
    with pytest.raises(HTTPException) as error:
        asyncio.run(yandex_auth.confirm_link(code="x", state=None, db=db))
    assert error.value.status_code == 401


def _bind_yandex(db, student, monkeypatch, yandex_id="12345"):
    async def fake_fetch_yandex_id(code, redirect_uri):
        return yandex_id

    monkeypatch.setattr(yandex_auth, "fetch_yandex_id", fake_fetch_yandex_id)
    state = yandex_auth.create_link_state(student.id)
    return asyncio.run(yandex_auth.confirm_link(code="x", state=state, db=db))


def test_confirm_link_binds_yandex_id(db, student, monkeypatch, yandex_config):
    response = _bind_yandex(db, student, monkeypatch)

    db.refresh(student)
    assert student.yandex_id == "12345"
    assert response.headers["location"] == "/settings"


def test_confirm_link_rejects_yandex_of_another_account(
    db, student, student2, monkeypatch, yandex_config
):
    student2.yandex_id = "12345"
    db.commit()

    response = _bind_yandex(db, student, monkeypatch)

    db.refresh(student)
    assert student.yandex_id is None
    assert response.status_code == 200  # HTML с alert, а не редирект
