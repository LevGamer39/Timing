"""Настройки школы: звонки, периоды, шкала оценок."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models import SchoolSetting, User, UserRole

router = APIRouter(prefix="/api/school", tags=["school"])

MAX_PAYLOAD = 200_000


class SchoolSettings(BaseModel):
    payload: dict


def _row(db: Session) -> SchoolSetting | None:
    return db.query(SchoolSetting).order_by(SchoolSetting.id).first()


@router.get("/settings", response_model=SchoolSettings)
def get_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _row(db)
    if not row:
        return SchoolSettings(payload={})
    try:
        payload = json.loads(row.payload)
    except ValueError:
        payload = {}
    return SchoolSettings(payload=payload if isinstance(payload, dict) else {})


@router.put("/settings", response_model=SchoolSettings)
def save_settings(
    body: SchoolSettings,
    current_user: User = Depends(require_role(UserRole.admin)),
    db: Session = Depends(get_db),
):
    raw = json.dumps(body.payload, ensure_ascii=False)
    if len(raw.encode("utf-8")) > MAX_PAYLOAD:
        raise HTTPException(413, "Settings payload is too large")
    row = _row(db)
    if row:
        row.payload = raw
    else:
        row = SchoolSetting(payload=raw)
        db.add(row)
    db.commit()
    return SchoolSettings(payload=body.payload)
