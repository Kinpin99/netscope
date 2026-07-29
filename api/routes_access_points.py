"""Access point management routes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter

from topology.topology_builder import TopologyBuilder
from onboarding.ztp_store import ZTPStore

router = APIRouter()


def _is_ap(device):
    dtype = (device.get("device_type") or "").lower()
    role = (device.get("role") or device.get("name") or "").lower()
    return dtype in {"access_point", "ap", "wireless_ap"} or "access point" in role or role.startswith("ap-")


@router.get("")
def list_access_points():
    builder = TopologyBuilder()
    devices = [d for d in builder.device_list() if _is_ap(d)]
    onboarding = ZTPStore().list_devices()
    ztp_by_ip = {}
    for item in onboarding:
        approval = item.get("approval") or {}
        ip = approval.get("data_ip") or item.get("data_ip") or item.get("management_ip")
        if ip:
            ztp_by_ip[ip] = item

    enriched = []
    for dev in devices:
        record = ztp_by_ip.get(dev.get("ip"))
        enriched.append({
            **dev,
            "onboarding_status": record.get("status") if record else None,
            "serial_number": (record or {}).get("serial_number"),
            "mac_address": (record or {}).get("mac_address"),
            "model": (record or {}).get("model"),
        })
    return {"access_points": enriched}
