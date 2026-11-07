"""Автовход из мини-приложения MAX по initData."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException

from app import config
from app.routers.users import _max_user_hash, _verify_webapp_init_data, max_webapp_login


def _sign(fields: dict, token: str) -> str:
    data_check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return hmac.new(key, data_check.encode(), hashlib.sha256).hexdigest()


def _init_data(user_id: int, token: str = "test-bot-token") -> str:
    fields = {
        "user": json.dumps({"id": user_id}, ensure_ascii=False),
        "auth_date": str(int(time.time())),
    }
    fields["hash"] = _sign(fields, token)
    return urlencode(fields)


def test_valid_init_data_returns_user_id():
    assert _verify_webapp_init_data(_init_data(4242)) == 4242


def test_forged_signature_is_rejected():
    data = _init_data(4242).replace("4242", "9999")
    with pytest.raises(HTTPException) as error:
        _verify_webapp_init_data(data)
    assert error.value.status_code == 401


def test_expired_init_data_is_rejected():
    fields = {"user": json.dumps({"id": 1}), "auth_date": "1"}
    fields["hash"] = _sign(fields, config.MAX_BOT_TOKEN)
    with pytest.raises(HTTPException) as error:
        _verify_webapp_init_data(urlencode(fields))
    assert error.value.status_code == 401


def test_hash_matches_bot_derivation(db, student):
    hs = _max_user_hash(61133398)
    assert len(hs) == 64
    student.max_user_id = hs
    db.commit()

    class Body:
        init_data = _init_data(61133398)

    result = max_webapp_login(Body(), db)
    assert result.login == student.login


def test_unlinked_user_is_404(db):
    class Body:
        init_data = _init_data(123456789)

    with pytest.raises(HTTPException) as error:
        max_webapp_login(Body(), db)
    assert error.value.status_code == 404
