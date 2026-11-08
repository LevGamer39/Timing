"""Вход через OAuth Яндекса и привязка аккаунта."""
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.routers.users import create_access_token

router = APIRouter(prefix="/api/auth/yandex", tags=["yandex_auth"])

YANDEX_TOKEN_URL = "https://oauth.yandex.ru/token"
YANDEX_INFO_URL = "https://login.yandex.ru/info?format=json"
LINK_STATE_TTL_MINUTES = 10


def create_link_state(user_id: int) -> str:
    """Короткоживущий токен для OAuth ``state``.

    В ``state`` уходит не обычный access-токен, а отдельная подпись со
    ``scope``. Она годится только для завершения привязки и не даёт доступа
    к API, даже если попадёт в логи Яндекса или referer.
    """
    from datetime import datetime, timedelta, timezone

    from jose import jwt

    payload = {
        "sub": str(user_id),
        "scope": "yandex_link",
        "exp": datetime.now(timezone.utc)
        + timedelta(minutes=LINK_STATE_TTL_MINUTES),
    }
    return jwt.encode(payload, config.SECRET_KEY, algorithm=config.ALGORITHM)


def _require_config() -> None:
    if not config.YANDEX_CLIENT_ID or not config.YANDEX_CLIENT_SECRET:
        raise HTTPException(
            503, "Yandex OAuth is not configured (YANDEX_CLIENT_ID/SECRET)"
        )


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15.0)


def _auth_redirect(url: str) -> HTMLResponse:
    return HTMLResponse(
        "<script>"
        "alert('Аккаунт Яндекса не привязан! Войдите по почте или создайте аккаунт.');"
        "window.location.href='/login';"
        "</script>"
    )


async def fetch_yandex_id(code: str, redirect_uri: str) -> str:
    """Обменивает OAuth-код на yandex_id пользователя."""
    _require_config()
    try:
        async with _client() as client:
            response = await client.post(
                YANDEX_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": config.YANDEX_CLIENT_ID,
                    "client_secret": config.YANDEX_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                },
            )
            response.raise_for_status()
            access_token = response.json().get("access_token")
            if not access_token:
                raise HTTPException(400, "OAuth error: no access token")

            info = await client.get(
                YANDEX_INFO_URL,
                headers={"Authorization": f"OAuth {access_token}"},
            )
            info.raise_for_status()
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Yandex OAuth unavailable: {exc}") from exc

    yandex_id = info.json().get("id")
    if not yandex_id:
        raise HTTPException(400, "OAuth error: no user id")
    return str(yandex_id)


def auth_success_script(token: str, user: User) -> HTMLResponse:
    """Передаёт токен в SPA.

    Значения сериализуются в JSON и разбираются в браузере: прошлая версия
    подставляла ``user.first_name`` прямо в строковый литерал JS, из-за чего
    имя с кавычкой приводило к инъекции скрипта. Она же писала ``role`` и
    ``first_name`` отдельными ключами, тогда как ``api.js`` ждёт один
    объект ``user``.
    """
    payload = json.dumps(
        {
            "token": token,
            "user": {
                "id": user.id,
                "role": user.role.value,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email,
            },
        },
        ensure_ascii=False,
    )
    # ``</script>`` внутри JSON закрыл бы тег преждевременно.
    payload = payload.replace("</", "<\\/")
    return HTMLResponse(
        "<script>"
        f"(() => {{ const d = {payload};"
        " localStorage.setItem('timing-token', d.token);"
        " localStorage.setItem('user', JSON.stringify(d.user));"
        " window.location.replace('/home'); })();"
        "</script>"
    )


@router.get("/login-url")
def get_login_url():
    _require_config()
    url = (
        f"https://oauth.yandex.ru/authorize?response_type=code"
        f"&client_id={config.YANDEX_CLIENT_ID}"
        f"&redirect_uri={config.YANDEX_LOGIN_CALLBACK}"
    )
    return {"url": url}


@router.get("/callback")
async def yandex_callback(code: str, db: Session = Depends(get_db)):
    yandex_id = await fetch_yandex_id(code, config.YANDEX_LOGIN_CALLBACK)
    user = db.query(User).filter(User.yandex_id == yandex_id).first()

    if not user or not user.is_active:
        return _auth_redirect("/login")

    return auth_success_script(create_access_token(user.id), user)


@router.get("/link")
async def initiate_link(
    current_user: User = Depends(get_current_user),
):
    """Начинает привязку аккаунта *авторизованного* пользователя к Яндексу.

    ``token`` больше не принимается из query: клиент мог подставить чужой
    JWT, и привязка уходила не тому пользователю.

    Возвращает JSON с URL, как ``/login-url``: фронт ходит сюда fetch-ом с
    ``Authorization`` и сам делает ``location.assign(url)``. Прямой редирект
    из браузера сюда не доходит, потому что навигация не отправляет заголовок.
    """
    _require_config()
    url = (
        f"https://oauth.yandex.ru/authorize?response_type=code"
        f"&client_id={config.YANDEX_CLIENT_ID}"
        f"&redirect_uri={config.YANDEX_LINK_CALLBACK}"
        f"&state={create_link_state(current_user.id)}"
    )
    return {"url": url}


@router.get("/confirm-link")
async def confirm_link(
    code: str,
    state: str | None = None,
    db: Session = Depends(get_db),
):
    """Завершает привязку: Яндекс возвращает выданный нами JWT в ``state``."""
    if not state:
        raise HTTPException(401, "Not authenticated")

    from jose import JWTError, jwt

    try:
        payload = jwt.decode(
            state, config.SECRET_KEY, algorithms=[config.ALGORITHM]
        )
        if payload.get("scope") != "yandex_link":
            raise ValueError("wrong token scope")
        user_id = int(payload["sub"])
    except (JWTError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(401, "Not authenticated") from exc

    current_user = (
        db.query(User)
        .filter(User.id == user_id, User.is_active.is_(True))
        .first()
    )
    if not current_user:
        raise HTTPException(401, "Not authenticated")

    yandex_id = await fetch_yandex_id(code, config.YANDEX_LINK_CALLBACK)

    existing = db.query(User).filter(User.yandex_id == yandex_id).first()
    if existing:
        if existing.id == current_user.id:
            return RedirectResponse(url="/settings")
        return HTMLResponse(
            "<script>"
            "alert('Этот Яндекс уже привязан к другому аккаунту!');"
            "window.location.href='/settings';"
            "</script>"
        )

    current_user.yandex_id = yandex_id
    db.commit()
    return RedirectResponse(url="/settings")