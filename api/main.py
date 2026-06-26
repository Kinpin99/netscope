import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes_system import router as system_router
from api.routes_devices import router as devices_router
from api.routes_alerts import router as alerts_router
from api.routes_topology import router as topology_router
from api.routes_traffic import router as traffic_router

app = FastAPI(
    title="Network Anomaly Detection API",
    description="Backend for the AI-powered network anomaly detection and health dashboard.",
    version="0.1.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system_router, prefix="/system", tags=["system"])
app.include_router(devices_router, prefix="/devices", tags=["devices"])
app.include_router(alerts_router, prefix="/alerts", tags=["alerts"])
app.include_router(topology_router, prefix="/topology", tags=["topology"])
app.include_router(traffic_router, prefix="/traffic", tags=["traffic"])


@app.get("/")
def root():
    return {
        "service": "network-anomaly-detection-api",
        "status": "ok",
        "docs": "/docs",
    }
