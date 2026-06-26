import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter

from topology.topology_builder import TopologyBuilder

router = APIRouter()


def _get_builder() -> TopologyBuilder:
    return TopologyBuilder()


@router.get("/buildings")
def building_view():

    builder = _get_builder()
    return {"buildings": builder.building_view()}


@router.get("/devices")
def device_list():
    """Flat list of all devices with current health/status - for a simple device-list view."""
    builder = _get_builder()
    return {"devices": builder.device_list()}
