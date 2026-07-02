"""
Production-oriented security helpers for the dashboard API.

Implemented controls:
  - HMAC-signed bearer tokens using a secret from NETSCOPE_JWT_SECRET
  - PBKDF2 password hashing for local NOC users
  - Role based access control for admin actions
  - Login rate limiting and lockout
  - Optional LAN/IP allow-list middleware support
  - JSONL audit logging for security-sensitive actions

This intentionally uses only the Python standard library plus FastAPI so the
project does not need a large authentication dependency for the prototype.
"""

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from utils.config_loader import PROJECT_ROOT, load_config

_bearer = HTTPBearer(auto_error=False)
_lock = threading.Lock()
_failed_logins: Dict[str, Dict[str, float]] = {}
_ephemeral_secret = secrets.token_urlsafe(32)

ROLE_ADMIN = "admin"
ROLE_ENGINEER = "engineer"
ROLE_ANALYST = "analyst"

ROLE_DISPLAY = {
    ROLE_ADMIN: "NOC Admin",
    ROLE_ENGINEER: "Engineer",
    ROLE_ANALYST: "Analyst",
}


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + pad).encode("ascii"))


def _security_cfg() -> Dict[str, Any]:
    try:
        cfg = load_config()
    except Exception:
        cfg = {}
    security = cfg.get("security", {}) or {}
    paths = cfg.get("paths", {}) or {}

    def path_value(key: str, default: str) -> Path:
        raw = security.get(key, default)
        p = Path(raw)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    return {
        "token_ttl_minutes": int(security.get("token_ttl_minutes", 480)),
        "users_file": path_value("users_file", "data/security/users.json"),
        "audit_log_path": path_value("audit_log_path", "data/audit/audit.log"),
        "allow_registration": _env_bool("NETSCOPE_ALLOW_REGISTRATION", bool(security.get("allow_registration", False))),
        "allow_admin_registration": _env_bool("NETSCOPE_ALLOW_ADMIN_REGISTRATION", False),
        "login_max_failures": int(security.get("login_max_failures", 5)),
        "login_lockout_minutes": int(security.get("login_lockout_minutes", 15)),
        "allowed_cidrs": _env_list("NETSCOPE_IP_ALLOWLIST") or security.get("allowed_cidrs", []),
        "enforce_ip_allowlist": _env_bool("NETSCOPE_ENFORCE_IP_ALLOWLIST", bool(security.get("enforce_ip_allowlist", False))),
        "cors_allowed_origins": _env_list("NETSCOPE_CORS_ORIGINS") or security.get("cors_allowed_origins", ["http://localhost:5173", "http://127.0.0.1:5173"]),
        "force_https": _env_bool("NETSCOPE_FORCE_HTTPS", bool(security.get("force_https", False))),
    }


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> List[str]:
    value = os.environ.get(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


def token_secret() -> str:
    # Production should always set this. The generated fallback is deliberately
    # ephemeral so a forgotten secret does not become a hardcoded shared secret.
    return os.environ.get("NETSCOPE_JWT_SECRET") or _ephemeral_secret


def normalize_role(role: Optional[str]) -> str:
    value = (role or ROLE_ANALYST).strip().lower()
    if "admin" in value:
        return ROLE_ADMIN
    if "engineer" in value or "operator" in value:
        return ROLE_ENGINEER
    return ROLE_ANALYST


def user_public(user: Dict[str, Any]) -> Dict[str, Any]:
    role = normalize_role(user.get("role"))
    return {
        "id": user.get("id") or user.get("username"),
        "username": user.get("username"),
        "name": user.get("name") or user.get("username"),
        "role": ROLE_DISPLAY.get(role, role),
        "role_code": role,
    }


def _hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    rounds = 200000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), rounds)
    return "pbkdf2_sha256${}${}${}".format(rounds, salt, _b64e(digest))


def _verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds_s, salt, digest = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
        calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), rounds)
        return hmac.compare_digest(_b64e(calc), digest)
    except Exception:
        return False


def _ensure_user_store() -> Path:
    cfg = _security_cfg()
    path = cfg["users_file"]
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path

    users: List[Dict[str, Any]] = []
    bootstrap_password = os.environ.get("NETSCOPE_BOOTSTRAP_PASSWORD")
    if bootstrap_password:
        username = os.environ.get("NETSCOPE_BOOTSTRAP_USERNAME", "admin")
        name = os.environ.get("NETSCOPE_BOOTSTRAP_NAME", "NOC Administrator")
        users.append({
            "id": 1,
            "username": username,
            "name": name,
            "role": ROLE_ADMIN,
            "password_hash": _hash_password(bootstrap_password),
            "created_at": time.time(),
            "disabled": False,
        })
    with open(path, "w") as f:
        json.dump({"users": users, "next_id": len(users) + 1}, f, indent=2)
    return path


def _load_store() -> Dict[str, Any]:
    path = _ensure_user_store()
    with open(path) as f:
        store = json.load(f)

    # If the file was previously created without a bootstrap password, allow
    # the first admin to be created later when the environment is set.
    if not store.get("users") and os.environ.get("NETSCOPE_BOOTSTRAP_PASSWORD"):
        bootstrap_password = os.environ["NETSCOPE_BOOTSTRAP_PASSWORD"]
        username = os.environ.get("NETSCOPE_BOOTSTRAP_USERNAME", "admin")
        name = os.environ.get("NETSCOPE_BOOTSTRAP_NAME", "NOC Administrator")
        store["users"] = [{
            "id": 1,
            "username": username,
            "name": name,
            "role": ROLE_ADMIN,
            "password_hash": _hash_password(bootstrap_password),
            "created_at": time.time(),
            "disabled": False,
        }]
        store["next_id"] = 2
        _save_store(store)
    return store


def _save_store(store: Dict[str, Any]) -> None:
    path = _ensure_user_store()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(store, f, indent=2)
    tmp.replace(path)


def get_user(username: str) -> Optional[Dict[str, Any]]:
    store = _load_store()
    for user in store.get("users", []):
        if user.get("username") == username:
            return user
    return None


def create_user(username: str, password: str, name: str, role: str) -> Dict[str, Any]:
    username = username.strip()
    if not username:
        raise ValueError("Username is required")
    if len(password or "") < 8:
        raise ValueError("Password must be at least 8 characters")

    role_code = normalize_role(role)
    cfg = _security_cfg()
    if role_code == ROLE_ADMIN and not cfg["allow_admin_registration"]:
        role_code = ROLE_ANALYST

    with _lock:
        store = _load_store()
        if any(u.get("username") == username for u in store.get("users", [])):
            raise ValueError("Username already exists")
        next_id = int(store.get("next_id", 1))
        user = {
            "id": next_id,
            "username": username,
            "name": name.strip() or username,
            "role": role_code,
            "password_hash": _hash_password(password),
            "created_at": time.time(),
            "disabled": False,
        }
        store.setdefault("users", []).append(user)
        store["next_id"] = next_id + 1
        _save_store(store)
        return user


def authenticate(username: str, password: str, request: Optional[Request] = None) -> Optional[Dict[str, Any]]:
    client_ip = request.client.host if request and request.client else "unknown"
    key = "{}:{}".format(client_ip, username.strip().lower())
    cfg = _security_cfg()
    now = time.time()

    with _lock:
        item = _failed_logins.get(key)
        if item and item.get("lock_until", 0) > now:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts. Try again later.",
            )

    user = get_user(username)
    ok = bool(user and not user.get("disabled") and _verify_password(password, user.get("password_hash", "")))

    with _lock:
        if ok:
            _failed_logins.pop(key, None)
        else:
            item = _failed_logins.get(key, {"count": 0, "lock_until": 0})
            item["count"] = item.get("count", 0) + 1
            item["last_failed_at"] = now
            if item["count"] >= cfg["login_max_failures"]:
                item["lock_until"] = now + cfg["login_lockout_minutes"] * 60
            _failed_logins[key] = item

    return user if ok else None


def create_token(user: Dict[str, Any]) -> str:
    cfg = _security_cfg()
    now = int(time.time())
    payload = {
        "sub": user["username"],
        "role": normalize_role(user.get("role")),
        "iat": now,
        "exp": now + cfg["token_ttl_minutes"] * 60,
    }
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = "{}.{}".format(
        _b64e(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
    )
    sig = hmac.new(token_secret().encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return "{}.{}".format(signing_input, _b64e(sig))


def verify_token(token: str) -> Dict[str, Any]:
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
        signing_input = "{}.{}".format(header_b64, payload_b64)
        expected = hmac.new(token_secret().encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64e(expected), sig_b64):
            raise ValueError("bad signature")
        payload = json.loads(_b64d(payload_b64).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            raise ValueError("expired")
        return payload
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


def require_user(credentials: HTTPAuthorizationCredentials = Depends(_bearer)) -> Dict[str, Any]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    payload = verify_token(credentials.credentials)
    user = get_user(payload.get("sub", ""))
    if not user or user.get("disabled"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists")
    return user_public(user)


def require_admin(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    if normalize_role(user.get("role_code")) != ROLE_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    return user


def audit_event(action: str, request: Optional[Request] = None, user: Optional[Dict[str, Any]] = None,
                success: bool = True, target: Optional[str] = None, detail: Optional[str] = None) -> None:
    cfg = _security_cfg()
    path = cfg["audit_log_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.time(),
        "action": action,
        "success": bool(success),
        "target": target,
        "detail": detail,
        "user": user_public(user).get("username") if user else None,
        "role": user_public(user).get("role_code") if user else None,
        "client_ip": request.client.host if request and request.client else None,
        "user_agent": request.headers.get("user-agent") if request else None,
    }
    with _lock:
        with open(path, "a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")


def allowed_client_ip(ip: str) -> bool:
    cfg = _security_cfg()
    cidrs = cfg.get("allowed_cidrs") or []
    if not cidrs:
        return True
    try:
        client = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for cidr in cidrs:
        try:
            if client in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def security_config_public() -> Dict[str, Any]:
    cfg = _security_cfg()
    return {
        "registration_enabled": bool(cfg["allow_registration"]),
        "ip_allowlist_enabled": bool(cfg["enforce_ip_allowlist"]),
        "token_ttl_minutes": cfg["token_ttl_minutes"],
    }
