"""Общие зависимости аутентификации.

Раньше `get_current_user` был продублирован здесь и в `app.routers.users`,
и копии разошлись: в этой не проверялся `is_active`, поэтому
деактивированный пользователь всё ещё доходил до части эндпоинтов
(`/api/notifications/*` импортировал последней именно эту версию и
перекрывал вторую). Сейчас реализация одна, и все роутеры импортируют
именно её отсюда.
"""
from typing import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.models import TokenBlacklist, User, UserRole

SECRET_KEY = config.SECRET_KEY
ALGORITHM = config.ALGORITHM

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/users/login")


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Разрешает bearer-токен в активного пользователя.

    Проверяет blacklist (отозванные токены) и наличиеclaims sub, exp, jti.
    """
    if not SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SECRET_KEY is not configured",
        )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
        jti = payload.get("jti")
    except (JWTError, KeyError, TypeError, ValueError):
        raise _unauthorized()

    # Проверка черного списка токенов
    if jti:
        blacklisted = (
            db.query(TokenBlacklist)
            .filter(TokenBlacklist.jti == jti, TokenBlacklist.user_id == user_id)
            .first()
        )
        if blacklisted:
            raise _unauthorized()

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise _unauthorized()
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is not active",
        )
    return user


def require_role(*roles: UserRole) -> Callable[..., User]:
    """Фабрика зависимости: эндпоинт доступен только этих ролей."""

    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied",
            )
        return current_user

    return dependency


def require_self_or_roles(*roles: UserRole) -> Callable[..., User]:
    """Разрешает указанные роли или самому пользователю читать себя.

    Используется эндпоинтами, где смешаны «свои данные» и данные по роли.
    """
    allowed = (*roles, UserRole.admin)

    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied",
            )
        return current_user

    return dependency