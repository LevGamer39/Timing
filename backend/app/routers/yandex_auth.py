import httpx
import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from jose import JWTError, jwt

from app.database import get_db
from app.models import User
from app.dependencies import get_current_user
from app.routers.users import create_access_token 
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(prefix="/api/auth/yandex", tags=["yandex_auth"])

SECRET_KEY = os.environ.get("SECRET_KEY")
ALGORITHM = os.environ.get("ALGORITHM", "HS256")
CLIENT_ID = os.environ.get("YANDEX_CLIENT_ID")
CLIENT_SECRET = os.environ.get("YANDEX_CLIENT_SECRET")

LOGIN_CALLBACK = "http://vkcollege.ru/api/auth/yandex/callback"
LINK_CALLBACK = "http://vkcollege.ru/api/auth/yandex/confirm-link"

@router.get("/login-url")
def get_login_url():
    return {"url": f"https://oauth.yandex.ru/authorize?response_type=code&client_id={CLIENT_ID}&redirect_uri={LOGIN_CALLBACK}"}

@router.get("/callback")
async def yandex_callback(code: str, db: Session = Depends(get_db)):
    y_id = await fetch_yandex_id(code, LOGIN_CALLBACK)
    user = db.query(User).filter(User.yandex_id == y_id).first()
    
    if not user:
        return HTMLResponse("<script>alert('Аккаунт Яндекса не привязан! Войдите по почте или создайте аккаунт.'); window.location.href='/login';</script>")

    token = create_access_token(user.id)
    return auth_success_script(token, user)

@router.get("/link")
def initiate_link(token: str):
    url = f"https://oauth.yandex.ru/authorize?response_type=code&client_id={CLIENT_ID}&redirect_uri={LINK_CALLBACK}&state={token}"
    return RedirectResponse(url)

@router.get("/confirm-link")
async def confirm_link(code: str, state: str | None = None, db: Session = Depends(get_db)):
    if not state:
        raise HTTPException(401, "Not authenticated")

    try:
        payload = jwt.decode(state, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(401, "Not authenticated")

    current_user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not current_user:
        raise HTTPException(401, "Not authenticated")

    y_id = await fetch_yandex_id(code, LINK_CALLBACK)
    
    existing = db.query(User).filter(User.yandex_id == y_id).first()
    if existing:
        return HTMLResponse("<script>alert('Этот Яндекс уже занят!'); window.location.href='/dashboard';</script>")
    
    current_user.yandex_id = y_id
    db.commit()
    return RedirectResponse(url="/settings?event=yandex_linked")

async def fetch_yandex_id(code: str, redirect: str):
    async with httpx.AsyncClient() as client:
        r = await client.post("https://oauth.yandex.ru/token", data={
            "grant_type": "authorization_code", "code": code,
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET
        })
        token = r.json().get("access_token")
        if not token: raise HTTPException(400, "OAuth Error")
        
        # Получение ID
        info = await client.get("https://login.yandex.ru/info?format=json", 
                                headers={"Authorization": f"OAuth {token}"})
        return str(info.json().get("id"))

def auth_success_script(token, user):
    return HTMLResponse(f"""
        <script>
            localStorage.setItem('token', '{token}');
            localStorage.setItem('role', '{user.role.value}');
            localStorage.setItem('first_name', '{user.first_name}');
            window.location.href = '/dashboard';
        </script>
    """)
