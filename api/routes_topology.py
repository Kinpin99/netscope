"""
api/routes_topology.py
-------------------------
Building-grouped network view (must_add_to_project.txt item 1):
"Allows administrators to view issues about network access, network
congestion, device status, and error packets from the perspective of
buildings."
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter

from topology.topology_builder import TopologyBuilder
from troubleshooting.root_cause_engine import RootCauseEngine

router = APIRouter()


def _get_builder() -> TopologyBuilder:
    return TopologyBuilder()


@router.get("/buildings")
def building_view():
    """
    One entry per building with device count, open issue count, max
    severity, average health score, and the full list of devices in that
    building (each with its own health/status).
    """
    builder = _get_builder()
    return {"buildings": builder.building_view()}


@router.get("/devices")
def device_list():
    """Flat list of all devices with current health/status - for a simple device-list view."""
    builder = _get_builder()
    return {"devices": builder.device_list()}


@router.get("/graph")
def topology_graph():
    """NetworkX topology graph used by troubleshooting/root-cause analysis."""
    return RootCauseEngine().topology_json()
