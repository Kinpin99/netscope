"""
api/routes_troubleshooting.py
-----------------------------
Automated troubleshooting and root-cause analysis routes.
"""

import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from api.security import audit_event, require_admin, require_user
from troubleshooting.event_store import SyslogEventStore
from troubleshooting.root_cause_engine import RootCauseEngine
from troubleshooting.syslog_parser import parse_syslog_line

router = APIRouter()


class SyslogIngestRequest(BaseModel):
    line: str
    device_ip: Optional[str] = None


@router.get("/incidents")
def incidents(last_hours: float = Query(24, ge=0.1, le=168), user=Depends(require_user)):
    return RootCauseEngine().analyze(last_hours=last_hours)


@router.get("/incidents/{incident_id}")
def incident_detail(incident_id: str, last_hours: float = Query(24, ge=0.1, le=168), user=Depends(require_user)):
    incident = RootCauseEngine().get_incident(incident_id, last_hours=last_hours)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found in the selected time window")
    return incident


@router.post("/analyze")
def analyze(last_hours: float = 24, user=Depends(require_user)):
    return RootCauseEngine().analyze(last_hours=last_hours)


@router.get("/syslogs")
def list_syslogs(last_hours: float = Query(24, ge=0.1, le=168), limit: int = Query(200, ge=1, le=1000), user=Depends(require_user)):
    since = time.time() - last_hours * 3600
    return {"events": SyslogEventStore().list_events(since=since, limit=limit)}


@router.post("/syslogs")
def ingest_syslog(payload: SyslogIngestRequest, request: Request, user=Depends(require_admin)):
    event = SyslogEventStore().append_raw(payload.line, device_ip=payload.device_ip)
    audit_event("syslog_ingested", request=request, user=user, target=event.get("device_ip"), detail=event.get("event_type"))
    return event


@router.post("/syslogs/parse")
def parse_syslog(payload: SyslogIngestRequest, user=Depends(require_user)):
    return parse_syslog_line(payload.line, default_device_ip=payload.device_ip)


@router.get("/topology-impact/{device_id}")
def topology_impact(device_id: str, user=Depends(require_user)):
    engine = RootCauseEngine()
    graph = engine.topology
    if not graph:
        raise HTTPException(status_code=503, detail="Topology graph is unavailable. Check NetworkX and topology.links in config.yaml")
    resolved = graph.resolve(device_id)
    if not resolved:
        raise HTTPException(status_code=404, detail="Device not found in topology graph")
    return {
        "device": graph.node_payload(resolved),
        "upstream": [graph.node_payload(x) for x in graph.upstream(resolved)],
        "downstream": [graph.node_payload(x) for x in graph.downstream(resolved)],
    }
