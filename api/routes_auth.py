"""Authentication routes for the dashboard API."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from api.security import (
    audit_event,
    authenticate,
    create_token,
    create_user,
    require_admin,
    require_user,
    security_config_public,
    user_public,
    _security_cfg,
)

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    name: str
    username: str
    password: str
    role: str = "Analyst"


def _token_response(user):
    return {
        "access_token": create_token(user),
        "token_type": "bearer",
        "user": user_public(user),
    }


@router.post("/login")
def login(payload: LoginRequest, request: Request):
    user = authenticate(payload.username, payload.password, request)
    if not user:
        audit_event("login_failed", request=request, success=False, target=payload.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    audit_event("login_success", request=request, user=user, target=payload.username)
    return _token_response(user)


@router.post("/register")
def register(payload: RegisterRequest, request: Request):
    if not _security_cfg()["allow_registration"]:
        audit_event("registration_blocked", request=request, success=False, target=payload.username)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Self-registration is disabled")
    try:
        user = create_user(payload.username, payload.password, payload.name, payload.role)
    except ValueError as exc:
        audit_event("registration_failed", request=request, success=False, target=payload.username, detail=str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    audit_event("registration_success", request=request, user=user, target=payload.username)
    return _token_response(user)


@router.get("/me")
def me(user=Depends(require_user)):
    return user


@router.get("/security")
def security_settings(user=Depends(require_user)):
    return security_config_public()


@router.get("/audit")
def audit_tail(limit: int = 100, user=Depends(require_admin)):
    cfg = _security_cfg()
    path = cfg["audit_log_path"]
    if not path.exists():
        return {"events": []}
    lines = path.read_text().splitlines()[-max(1, min(limit, 500)):]
    return {"events": [line for line in lines]}
