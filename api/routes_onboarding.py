"""
api/routes_onboarding.py
------------------------
ZTP/PnP-inspired automated device onboarding for the school-network
prototype. Devices can phone home to the controller, admins can approve or
reject them, and approved devices are enrolled into the monitoring inventory.

This is intentionally a safe "ZTP-lite" implementation for simulation and
lab use. It does not attempt vendor-specific firmware upgrades, NETCONF, or
DHCP Option 43 parsing. In production those steps would be handled by the
actual wireless/network controller and this API would integrate with it.
"""

import sys
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from api.security import audit_event, require_admin, require_user
from onboarding.ztp_store import ZTPStore

router = APIRouter()


def _store() -> ZTPStore:
    return ZTPStore()


class PhoneHomeRequest(BaseModel):
    serial_number: str = Field(..., min_length=2)
    mac_address: str = Field(..., min_length=5)
    device_type: str = "access_point"
    model: str = "Simulated Device"
    firmware_version: str = "unknown"
    management_ip: str
    data_ip: Optional[str] = None
    hostname: Optional[str] = None
    enrollment_token: Optional[str] = None
    capabilities: Dict[str, Any] = {}


class ApprovalRequest(BaseModel):
    name: str
    device_type: str = "access_point"
    role: str = "Wireless Access Point"
    building: str = "Unassigned"
    floor: Optional[str] = None
    data_ip: Optional[str] = None
    management_ip: Optional[str] = None
    snmp_community: str = "public"
    snmp_if_index: int = 1
    if_speed_bps: int = 100000000
    monitoring_enabled: bool = True
    notes: Optional[str] = None


class CompleteRequest(BaseModel):
    status: str = "success"
    message: Optional[str] = None


@router.post("/phone-home")
def phone_home(payload: PhoneHomeRequest, request: Request):
    """
    Device-side entry point. A factory-default/simulated device calls this
    when it boots. If it is already approved, the response includes the config
    URL and generated config text; otherwise it remains pending for admin review.
    """
    try:
        result = _store().phone_home(payload.dict(), request)
    except ValueError as exc:
        audit_event("ztp_phone_home_rejected", request=request, success=False, target=payload.serial_number, detail=str(exc))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

    audit_event("ztp_phone_home", request=request, target=result["device"].get("id"), detail=result["device"].get("status"))
    return result


@router.get("/devices")
def list_devices(status_filter: Optional[str] = None, user=Depends(require_user)):
    return {"devices": _store().list_devices(status_filter=status_filter)}


@router.get("/pending")
def pending_devices(user=Depends(require_user)):
    return {"devices": _store().list_devices(status_filter="pending")}


@router.get("/provisioned")
def provisioned_devices(user=Depends(require_user)):
    return {"devices": _store().list_devices(status_filter="provisioned")}


@router.get("/devices/{device_id}")
def get_device(device_id: str, user=Depends(require_user)):
    device = _store().get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Onboarding device not found")
    return device


@router.post("/devices/{device_id}/approve")
def approve_device(device_id: str, payload: ApprovalRequest, request: Request, user=Depends(require_admin)):
    try:
        device = _store().approve_device(device_id, payload.dict(), user=user, request=request)
    except KeyError:
        audit_event("ztp_approve_missing", request=request, user=user, success=False, target=device_id)
        raise HTTPException(status_code=404, detail="Onboarding device not found")
    except ValueError as exc:
        audit_event("ztp_approve_failed", request=request, user=user, success=False, target=device_id, detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))

    audit_event("ztp_approved", request=request, user=user, target=device_id, detail=payload.name)
    return device


@router.post("/devices/{device_id}/reject")
def reject_device(device_id: str, request: Request, user=Depends(require_admin)):
    try:
        device = _store().reject_device(device_id, user=user, request=request)
    except KeyError:
        audit_event("ztp_reject_missing", request=request, user=user, success=False, target=device_id)
        raise HTTPException(status_code=404, detail="Onboarding device not found")

    audit_event("ztp_rejected", request=request, user=user, target=device_id)
    return device


@router.get("/devices/{device_id}/config")
def get_config(device_id: str, user=Depends(require_user)):
    try:
        return _store().get_generated_config(device_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Generated config not found")


@router.post("/devices/{device_id}/complete")
def complete_device(device_id: str, payload: CompleteRequest, request: Request):
    """
    Device-side completion callback. The simulated device calls this after
    downloading/applying its generated config.
    """
    try:
        device = _store().mark_complete(device_id, payload.status, payload.message, request)
    except KeyError:
        audit_event("ztp_complete_missing", request=request, success=False, target=device_id)
        raise HTTPException(status_code=404, detail="Onboarding device not found")

    audit_event("ztp_completed", request=request, target=device_id, detail=payload.status)
    return device
