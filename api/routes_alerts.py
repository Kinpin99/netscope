"""
Alert queries (open alerts, historical queries with filters), issue
distribution view (must_add_to_project.txt item 5), and per-device health
scores (item 3).
"""

import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter, Query

from alerts.alert_engine import AlertEngine

router = APIRouter()


def _get_engine() -> AlertEngine:
    return AlertEngine()


@router.get("/open")
def list_open_alerts():
    """All currently-OPEN alerts, most relevant for a 'current issues' dashboard panel."""
    engine = _get_engine()
    return {"alerts": engine.store.list_open_alerts()}


@router.get("")
def list_alerts(
    since: Optional[float] = Query(None, description="Unix epoch - only alerts created at/after this time"),
    until: Optional[float] = Query(None, description="Unix epoch - only alerts created at/before this time"),
    device_ip: Optional[str] = Query(None, description="Filter by entity_id (device IP or scanner IP)"),
    building: Optional[str] = Query(None),
    severity: Optional[str] = Query(None, description="info|low|medium|high|critical"),
    status: Optional[str] = Query(None, description="open|closed"),
    last_hours: Optional[float] = Query(None, description="Shortcut: alerts from the last N hours"),
):
    """
    Historical alert query with filters. `last_hours` is a convenience that
    sets `since` to now - last_hours; if both are given, `since` wins.
    """
    if since is None and last_hours is not None:
        since = time.time() - last_hours * 3600

    engine = _get_engine()
    alerts = engine.store.list_alerts(
        since=since, until=until, device_ip=device_ip,
        building=building, severity=severity, status=status,
    )
    return {"alerts": alerts, "count": len(alerts)}


@router.get("/distribution")
def issue_distribution(
    since: Optional[float] = Query(None),
    until: Optional[float] = Query(None),
    last_hours: Optional[float] = Query(24, description="Default: last 24 hours"),
):
    """
    must_add_to_project.txt item 5: "Issue distribution view allows
    administrators to view the number of issues on different devices...
    This helps administrators quickly focus on the affected devices and the
    time range when many issues occur."

    Returns one summary per affected entity (device or scanner IP) with
    issue_count, max_severity, and the set of issue_types seen.
    """
    if since is None:
        since = time.time() - last_hours * 3600

    engine = _get_engine()
    distribution = engine.issue_distribution(since=since, until=until)
    return {
        "since": since,
        "until": until,
        "distribution": distribution,
    }


@router.get("/health-scores")
def health_scores():
    """
    Current per-device health scores (must_add_to_project.txt item 3),
    last computed by AlertEngine on the most recent processed window.
    """
    engine = _get_engine()
    return {"health_scores": engine.get_health_scores()}
