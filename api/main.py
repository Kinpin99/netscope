"""
api/main.py
-------------
FastAPI backend tying together telemetry, models, alerts, topology and the
React dashboard. Production-facing routes are protected by bearer-token auth;
admin-only operations are protected again at route level.
"""

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api.routes_auth import router as auth_router
from api.routes_system import router as system_router
from api.routes_devices import router as devices_router
from api.routes_alerts import router as alerts_router
from api.routes_topology import router as topology_router
from api.routes_traffic import router as traffic_router
from api.routes_onboarding import router as onboarding_router
from api.routes_access_points import router as access_points_router
from api.routes_troubleshooting import router as troubleshooting_router
from api.routes_settings import router as settings_router
from api.security import allowed_client_ip, audit_event, require_user, _security_cfg
from api.live_runtime import get_live_runtime


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return response


class LANAllowListMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cfg = _security_cfg()
        if cfg.get("force_https"):
            proto = request.headers.get("x-forwarded-proto", request.url.scheme)
            if proto != "https" and request.url.hostname not in {"127.0.0.1", "localhost"}:
                return RedirectResponse(str(request.url.replace(scheme="https")))

        if cfg.get("enforce_ip_allowlist"):
            client_ip = request.client.host if request.client else ""
            if not allowed_client_ip(client_ip):
                audit_event("ip_allowlist_denied", request=request, success=False, target=client_ip)
                return JSONResponse(status_code=403, content={"detail": "Client IP is not allowed"})
        return await call_next(request)




@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run lightweight CSV-backed inference independently of page visits."""
    runtime = get_live_runtime()
    stop_event = asyncio.Event()

    async def worker():
        while not stop_event.is_set():
            await asyncio.to_thread(runtime.run_cycle)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=runtime.poll_interval_sec)
            except asyncio.TimeoutError:
                pass

    task = asyncio.create_task(worker()) if runtime.enabled else None
    try:
        yield
    finally:
        stop_event.set()
        if task is not None:
            await task

app = FastAPI(
    title="Network Anomaly Detection API",
    description="Backend for the AI-powered network anomaly detection and health dashboard.",
    version="0.3.0",
    lifespan=lifespan,
)

_security = _security_cfg()
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(LANAllowListMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_security.get("cors_allowed_origins") or [],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(system_router, prefix="/system", tags=["system"], dependencies=[Depends(require_user)])
app.include_router(devices_router, prefix="/devices", tags=["devices"], dependencies=[Depends(require_user)])
app.include_router(alerts_router, prefix="/alerts", tags=["alerts"], dependencies=[Depends(require_user)])
app.include_router(topology_router, prefix="/topology", tags=["topology"], dependencies=[Depends(require_user)])
app.include_router(traffic_router, prefix="/traffic", tags=["traffic"], dependencies=[Depends(require_user)])
app.include_router(onboarding_router, prefix="/onboarding", tags=["onboarding"])
app.include_router(access_points_router, prefix="/access-points", tags=["access-points"], dependencies=[Depends(require_user)])
app.include_router(troubleshooting_router, prefix="/troubleshooting", tags=["troubleshooting"])
app.include_router(settings_router, prefix="/settings", tags=["settings"])


@app.get("/")
def root():
    return {
        "service": "network-anomaly-detection-api",
        "status": "ok",
        "docs": "/docs",
        "auth": "/auth/login",
    }
