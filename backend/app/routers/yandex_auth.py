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
        " localStorage.setItem('token', d.token);"
        " localStorage.setItem('user', JSON.stringify(d.user));"
        " window.location.replace('/dashboard'); })();"
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
    token: str | None = None,
):
    """Начинает привязку аккаунта *авторизованного* пользователя к Яндексу.

    Раньше у эндпоинта не было зависимости авторизации, и он доверял строке
    с токеном, присланной клиентом.
    """
    _require_config()
    if not token:
        token = create_access_token(current_user.id)
    url = (
        f"https://oauth.yandex.ru/authorize?response_type=code"
        f"&client_id={config.YANDEX_CLIENT_ID}"
        f"&redirect_uri={config.YANDEX_LINK_CALLBACK}"
        f"&state={token}"
    )
    return RedirectResponse(url)


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
            return RedirectResponse(url="/settings?event=yandex_already_linked")
        return HTMLResponse(
            "<script>"
            "alert('Этот Яндекс уже привязан к другому аккаунту!');"
            "window.location.href='/settings';"
            "</script>"
        )

    current_user.yandex_id = yandex_id
    db.commit()
    return RedirectResponse(url="/settings?event=yandex_linked")